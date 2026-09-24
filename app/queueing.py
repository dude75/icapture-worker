"""WORKERS slots and queued capture tasks (itranscribe-style)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from app.capture.engine import CaptureEngine
from app.config import Settings, get_settings
from app.prometheus_metrics import observe_task_transition
from app.schemas import ErrorCode, ErrorDetail, TaskStatus
from app.tasks import TaskRecord, TaskStore
from app.url_parser import InvalidMeetingUrl, parse_capture_url

logger = logging.getLogger("app")


class QueueFullError(Exception):
    code = ErrorCode.queue_full


class TaskConflictError(Exception):
    code = ErrorCode.task_running


class CaptureRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = TaskStore(self.settings.SQLITE_PATH)
        self.engine = CaptureEngine(self.settings)
        self._free_slots: asyncio.Queue[int] = asyncio.Queue()
        for index in range(self.settings.WORKERS):
            self._free_slots.put_nowait(index)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._submit_lock = asyncio.Lock()
        self._cancelled: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._dispatcher: asyncio.Task[None] | None = None
        self._ttl_task: asyncio.Task[None] | None = None
        self._on_auto_stop: Callable[..., Awaitable[None]] | None = None

    def configure(self, *, on_auto_stop: Callable[..., Awaitable[None]]) -> None:
        self._on_auto_stop = on_auto_stop

    def worker_pool(self) -> dict[str, int]:
        max_workers = self.settings.WORKERS
        pending = self.store.count_active()
        active = min(pending, max_workers)
        available = max(0, max_workers - active)
        return {"max": max_workers, "active": active, "available": available}

    async def start(self) -> None:
        self._restore_unfinished()
        self._dispatcher = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        if self._dispatcher is not None:
            self._dispatcher.cancel()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for task_id in list(self.engine.list_active()):
            await self.engine.cancel(task_id)
        self.store.close()

    def _restore_unfinished(self) -> None:
        from app.artifacts import artifact_path

        for status in (TaskStatus.joining, TaskStatus.capturing):
            for record in self.store.list_tasks(status):
                self.store.mark_error(record.task_id, ErrorDetail(code=ErrorCode.interrupted))
                observe_task_transition(TaskStatus.error.value)
        for record in self.store.list_tasks(TaskStatus.finalizing):
            if not artifact_path(self.settings.DATA_DIR, record.task_id).is_file():
                self.store.mark_error(record.task_id, ErrorDetail(code=ErrorCode.interrupted))
                observe_task_transition(TaskStatus.error.value)
        for record in self.store.list_queued_fifo():
            self._queue.put_nowait(record.task_id)

    async def submit(
        self,
        *,
        connector: str,
        meeting_host: str,
        meeting_room: str,
        display_name: str,
        pin: str,
        jwt: str | None,
    ) -> TaskRecord:
        async with self._submit_lock:
            max_pending = self.settings.WORKERS + self.settings.WORKER_QUEUE_SIZE
            if self.store.count_active() >= max_pending:
                raise QueueFullError()
            task_id = str(uuid.uuid4())
            record = self.store.create(
                task_id=task_id,
                connector=connector,
                meeting_host=meeting_host,
                meeting_room=meeting_room,
                display_name=display_name,
                pin=pin,
                jwt=jwt,
            )
            await self._queue.put(task_id)
            observe_task_transition(TaskStatus.queued.value)
            return record

    async def submit_from_url(
        self,
        *,
        connector: str,
        meeting_url: str,
        display_name: str,
        pin: str,
        jwt: str | None,
    ) -> TaskRecord:
        try:
            meeting_host, meeting_room = parse_capture_url(connector, meeting_url)
        except InvalidMeetingUrl as exc:
            raise ValueError(ErrorCode.invalid_url) from exc
        return await self.submit(
            connector=connector,
            meeting_host=meeting_host,
            meeting_room=meeting_room,
            display_name=display_name,
            pin=pin,
            jwt=jwt,
        )

    async def delete_queued_or_cancel(self, task_id: str) -> None:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status in {TaskStatus.joining, TaskStatus.capturing}:
            raise TaskConflictError()
        if record.status is TaskStatus.queued:
            self._cancelled.add(task_id)
        self.store.mark_canceled(task_id)
        observe_task_transition(TaskStatus.canceled.value)

    async def _dispatch_loop(self) -> None:
        while True:
            task_id = await self._queue.get()
            task = asyncio.create_task(self._run_one(task_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_one(self, task_id: str) -> None:
        slot = await self._free_slots.get()
        try:
            if task_id in self._cancelled:
                self._cancelled.discard(task_id)
                return
            record = self.store.get(task_id)
            if record is None or record.status is not TaskStatus.queued:
                return

            assert self._on_auto_stop is not None

            self.store.update_status(task_id, TaskStatus.joining)
            observe_task_transition(TaskStatus.joining.value)

            async def auto_stop(task_id: str, *, reason: str) -> None:
                await self._on_auto_stop(task_id, reason=reason)

            async def joined() -> None:
                self.store.update_status(task_id, TaskStatus.capturing)
                observe_task_transition(TaskStatus.capturing.value)

            try:
                await self.engine.run_capture(
                    task_id,
                    slot,
                    connector=record.connector,
                    meeting_host=record.meeting_host,
                    meeting_room=record.meeting_room,
                    display_name=record.display_name,
                    pin=record.pin,
                    jwt=record.jwt,
                    on_joined=joined,
                    on_auto_stop=auto_stop,
                )
            except Exception as exc:
                self.store.mark_error(
                    task_id,
                    ErrorDetail(code=ErrorCode.join_failed, message=str(exc)),
                )
                observe_task_transition(TaskStatus.error.value)
                logger.warning("capture join failed %s: %s", task_id, exc)
        finally:
            self._free_slots.put_nowait(slot)
            self._queue.task_done()
