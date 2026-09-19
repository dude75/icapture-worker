"""Capture slot manager and task lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.artifact_cleanup import remove_task_artifacts
from app.artifacts import artifact_path, ensure_artifacts_dir, validate_artifact
from app.config import Settings, get_settings
from app.jitsi_client import JitsiEngineClient, JitsiEngineError
from app.prometheus_metrics import (
    observe_finalize,
    observe_join_failure,
    observe_task_completed,
)
from app.schemas import CaptureRequest, ConnectorStatus, ErrorCode, ErrorDetail, SlotsInfo, TaskStatus
from app.stub_capture import StubCaptureEngine
from app.tasks import TaskRecord, TaskStore
from app.url_parser import InvalidMeetingUrl, parse_meeting_url

logger = logging.getLogger("app")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

SUPPORTED_CONNECTORS = {"jitsi"}
STUB_CONNECTORS = {"zoom"}


class QueueFullError(Exception):
    code = ErrorCode.queue_full


class TaskConflictError(Exception):
    code = ErrorCode.task_running


class CaptureManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = TaskStore(self.settings.SQLITE_PATH)
        self.engine = JitsiEngineClient(self.settings)
        self._stub_engine: StubCaptureEngine | None = None
        self._use_stub = os.environ.get("ICAPTURE_STUBS", "").lower() in {"1", "true", "yes"}
        if self._use_stub:
            self._stub_engine = StubCaptureEngine(self.settings)
        self._lock = asyncio.Lock()
        self._finalize_tasks: dict[str, asyncio.Task[None]] = {}
        self._auto_stop_tasks: dict[str, asyncio.Task[None]] = {}
        self._ttl_task: asyncio.Task[None] | None = None
        self._engine_health = None

    @property
    def use_stub(self) -> bool:
        return self._use_stub

    async def start(self) -> None:
        ensure_artifacts_dir(self.settings.DATA_DIR)
        Path(self.settings.LOG_DIR).mkdir(parents=True, exist_ok=True)
        await self._restore_unfinished()
        self._ttl_task = asyncio.create_task(self._ttl_loop())
        self._engine_health = await self.engine.health()
        await self.reconcile_all()

    async def stop(self) -> None:
        if self._ttl_task is not None:
            self._ttl_task.cancel()
        for task in list(self._finalize_tasks.values()):
            task.cancel()
        for task in list(self._auto_stop_tasks.values()):
            task.cancel()
        await self._shutdown_active_captures()
        self.store.close()

    async def refresh_engine_health(self):
        self._engine_health = await self.engine.health()
        return self._engine_health

    def connector_status(self) -> dict[str, dict[str, str | None]]:
        jitsi_status = ConnectorStatus.loaded if self._jitsi_available() else ConnectorStatus.unavailable
        jitsi_reason = None
        if jitsi_status is ConnectorStatus.unavailable and self._engine_health is not None:
            jitsi_reason = self._engine_health.jitsi_reason or "engine_unreachable"
        return {
            "jitsi": {
                "status": jitsi_status.value,
                "label": "Jitsi Meet",
                "reason": jitsi_reason,
            },
            "zoom": {
                "status": ConnectorStatus.unavailable.value,
                "label": "Zoom",
                "reason": "not_implemented",
            },
        }

    def slots(self) -> SlotsInfo:
        active = self.store.count_active()
        maximum = self.settings.MAX_CONCURRENT_CAPTURES
        return SlotsInfo(max=maximum, active=active, available=max(maximum - active, 0))

    def _jitsi_available(self) -> bool:
        if self._use_stub:
            return True
        if self._engine_health is None:
            return False
        return self._engine_health.jitsi_status == ConnectorStatus.loaded.value

    async def _restore_unfinished(self) -> None:
        for record in self.store.list_unfinished():
            if await self._try_recover_artifact(record.task_id):
                logger.info("restored unfinished task %s from on-disk artifact", record.task_id)
                continue
            self.store.mark_error(record.task_id, ErrorDetail(code=ErrorCode.interrupted))
            observe_task_completed(TaskStatus.error.value)
            remove_task_artifacts(self.settings.DATA_DIR, record.task_id)

    def _artifact_ready(self, task_id: str) -> tuple[Path, int, float] | None:
        path = artifact_path(self.settings.DATA_DIR, task_id)
        if not path.is_file():
            return None
        try:
            size, duration = validate_artifact(path)
        except ValueError as exc:
            logger.debug("artifact not recoverable for %s: %s", task_id, exc)
            return None
        return path, size, duration

    def _mark_success_from_artifact(
        self,
        task_id: str,
        path: Path,
        size: int,
        duration: float,
    ) -> None:
        stopped_at = _utc_now_iso()
        self.store.mark_success(
            task_id,
            stopped_at=stopped_at,
            duration_sec=duration,
            artifact_path=str(path),
            artifact_size_bytes=size,
        )
        observe_task_completed(TaskStatus.success.value)

    async def _try_recover_artifact(self, task_id: str) -> bool:
        ready = await asyncio.to_thread(self._artifact_ready, task_id)
        if ready is None:
            return False
        path, size, duration = ready
        self._mark_success_from_artifact(task_id, path, size, duration)
        return True

    async def reconcile_recoverable_tasks(self) -> None:
        """Promote tasks to success when a valid artifact exists on disk."""
        for status in (TaskStatus.error, TaskStatus.finalizing):
            for record in self.store.list_tasks(status):
                if await self._try_recover_artifact(record.task_id):
                    logger.info("recovered %s task %s from on-disk artifact", status.value, record.task_id)

    async def reconcile_stale_tasks(self) -> None:
        """Mark DB tasks as interrupted when the Node engine has no live session."""
        if self._use_stub:
            return
        live_ids = set(await self.engine.list_sessions())
        for record in self.store.list_unfinished():
            if record.task_id in live_ids:
                continue
            if await self._try_recover_artifact(record.task_id):
                continue
            try:
                await self.engine.stop_capture(record.task_id)
            except Exception as exc:
                logger.debug("stale stop flush failed for %s: %s", record.task_id, exc)
            if await self._try_recover_artifact(record.task_id):
                continue
            logger.warning("reconcile stale task %s (no node session)", record.task_id)
            self._mark_interrupted(record.task_id, message="engine session lost")

    async def reconcile_orphan_node_sessions(self) -> None:
        """Drop Node sessions that are not tracked as unfinished tasks in the DB."""
        if self._use_stub:
            return
        live_ids = set(await self.engine.list_sessions())
        known_ids = {record.task_id for record in self.store.list_unfinished()}
        for task_id in live_ids - known_ids:
            logger.warning("reconcile orphan node session %s", task_id)
            await self.engine.cancel_capture(task_id)

    async def reconcile_all(self) -> None:
        await self.reconcile_recoverable_tasks()
        await self.reconcile_stale_tasks()
        await self.reconcile_orphan_node_sessions()

    async def refresh_capture_state(self, task_id: str) -> None:
        """Pick up kick/auto-finalize: success if artifact exists, else flush stale node session."""
        record = self.store.get(task_id)
        if record is None:
            return
        if record.status is TaskStatus.capturing:
            await self._sync_capturing_task(task_id)
        elif record.status is TaskStatus.error:
            await self._try_recover_artifact(task_id)

    async def _sync_capturing_task(self, task_id: str) -> None:
        if await self._try_recover_artifact(task_id):
            return
        if self._use_stub:
            return
        live_ids = set(await self.engine.list_sessions())
        if task_id in live_ids:
            return
        try:
            await self.engine.stop_capture(task_id)
        except Exception as exc:
            logger.debug("capture sync stop failed for %s: %s", task_id, exc)
        await self._try_recover_artifact(task_id)

    def _mark_interrupted(self, task_id: str, *, message: str) -> None:
        self.store.mark_error(
            task_id,
            ErrorDetail(code=ErrorCode.interrupted, message=message),
        )
        observe_task_completed(TaskStatus.error.value)
        remove_task_artifacts(self.settings.DATA_DIR, task_id)

    async def _shutdown_active_captures(self) -> None:
        for record in list(self.store.list_unfinished()):
            try:
                await self.cancel_capture(record.task_id)
            except Exception as exc:
                logger.warning("shutdown cancel failed for %s: %s", record.task_id, exc)

    async def create_capture(
        self,
        request: CaptureRequest,
        *,
        task_id_out: list[str | None] | None = None,
    ) -> TaskRecord:
        if request.connector in STUB_CONNECTORS or request.connector not in SUPPORTED_CONNECTORS:
            raise ValueError(ErrorCode.unsupported_connector)
        if not self._jitsi_available():
            raise ValueError(ErrorCode.unsupported_connector)
        try:
            meeting_host, meeting_room = parse_meeting_url(request.meeting_url)
        except InvalidMeetingUrl as exc:
            raise ValueError(ErrorCode.invalid_url) from exc

        async with self._lock:
            await self.reconcile_all()
            if self.slots().available <= 0:
                raise QueueFullError()

            task_id = str(uuid.uuid4())
            if task_id_out is not None:
                task_id_out[0] = task_id
            display_name = (request.display_name or self.settings.DEFAULT_BOT_DISPLAY_NAME).strip()
            pin = request.pin or ""
            record = self.store.create(
                task_id=task_id,
                connector=request.connector,
                meeting_host=meeting_host,
                meeting_room=meeting_room,
                display_name=display_name,
                pin=pin,
                jwt=request.jwt,
            )

            try:
                if self._use_stub:
                    assert self._stub_engine is not None
                    await self._stub_engine.start(
                        task_id=task_id,
                        meeting_host=meeting_host,
                        meeting_room=meeting_room,
                        display_name=display_name,
                        pin=pin,
                        jwt=request.jwt,
                        on_auto_stop=self._handle_auto_stop,
                    )
                else:
                    await self.engine.start_capture(
                        task_id=task_id,
                        meeting_host=meeting_host,
                        meeting_room=meeting_room,
                        display_name=display_name,
                        pin=pin,
                        jwt=request.jwt,
                    )
                    if self.settings.MAX_CAPTURE_DURATION_SEC > 0:
                        self._auto_stop_tasks[task_id] = asyncio.create_task(
                            self._auto_stop(task_id)
                        )
            except JitsiEngineError as exc:
                self.store.mark_error(task_id, ErrorDetail(code=ErrorCode.join_failed, message=str(exc)))
                observe_join_failure(exc.reason)
                observe_task_completed(TaskStatus.error.value)
                raise JitsiEngineError(str(exc), reason=exc.reason) from exc
            except Exception as exc:
                self.store.mark_error(
                    task_id,
                    ErrorDetail(code=ErrorCode.pipeline_error, message=str(exc)),
                )
                observe_task_completed(TaskStatus.error.value)
                raise

            observe_task_completed(TaskStatus.capturing.value)
            return self.store.get(task_id)  # type: ignore[return-value]

    async def stop_capture(self, task_id: str) -> TaskRecord:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status is TaskStatus.success:
            return record
        if record.status in {TaskStatus.finalizing, TaskStatus.capturing}:
            if record.status is TaskStatus.finalizing:
                return record
            self.store.update_status(task_id, TaskStatus.finalizing)
            auto_task = self._auto_stop_tasks.pop(task_id, None)
            if auto_task is not None:
                auto_task.cancel()
            self._finalize_tasks[task_id] = asyncio.create_task(self._finalize(task_id))
            return self.store.get(task_id)  # type: ignore[return-value]
        raise TaskConflictError()

    async def wait_until_terminal(self, task_id: str, *, timeout_sec: float = 60.0) -> TaskRecord:
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            record = self.store.get(task_id)
            if record is None:
                raise KeyError(task_id)
            if record.status in {TaskStatus.success, TaskStatus.error, TaskStatus.canceled}:
                return record
            await asyncio.sleep(0.05)
        raise TimeoutError(task_id)

    async def cancel_capture(self, task_id: str) -> TaskRecord:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status is TaskStatus.success:
            raise TaskConflictError()
        if record.status is TaskStatus.canceled:
            return record
        if record.status in {TaskStatus.capturing, TaskStatus.finalizing}:
            auto_task = self._auto_stop_tasks.pop(task_id, None)
            if auto_task is not None:
                auto_task.cancel()
            finalize_task = self._finalize_tasks.pop(task_id, None)
            if finalize_task is not None:
                finalize_task.cancel()
            if self._use_stub and self._stub_engine is not None:
                await self._stub_engine.cancel(task_id)
            else:
                await self.engine.cancel_capture(task_id)
            remove_task_artifacts(self.settings.DATA_DIR, task_id)
            self.store.mark_canceled(task_id)
            observe_task_completed(TaskStatus.canceled.value)
            return self.store.get(task_id)  # type: ignore[return-value]
        return record

    def get_artifact_path(self, task_id: str) -> Path:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status is not TaskStatus.success:
            raise ValueError(ErrorCode.artifact_not_ready)
        path = Path(record.artifact_path or "")
        if not path.is_file():
            raise ValueError(ErrorCode.artifact_not_ready)
        return path

    async def _auto_stop(self, task_id: str) -> None:
        await asyncio.sleep(self.settings.MAX_CAPTURE_DURATION_SEC)
        record = self.store.get(task_id)
        if record is not None and record.status is TaskStatus.capturing:
            await self._handle_auto_stop(task_id, reason="task_timeout")

    async def _handle_auto_stop(self, task_id: str, *, reason: str) -> None:
        del reason
        try:
            await self.stop_capture(task_id)
            await self.wait_until_terminal(task_id, timeout_sec=self.settings.JITSI_ENGINE_TIMEOUT_SEC)
        except Exception as exc:
            logger.warning("auto-stop failed for %s: %s", task_id, exc)

    async def _finalize(self, task_id: str) -> None:
        started = asyncio.get_event_loop().time()
        try:
            if self._use_stub and self._stub_engine is not None:
                engine_path = await self._stub_engine.stop(task_id)
            else:
                engine_path = await self.engine.stop_capture(task_id)
            path = Path(engine_path)
            if not path.is_file():
                path = artifact_path(self.settings.DATA_DIR, task_id)
            size, duration = await asyncio.to_thread(validate_artifact, path)
            stopped_at = _utc_now_iso()
            self.store.mark_success(
                task_id,
                stopped_at=stopped_at,
                duration_sec=duration,
                artifact_path=str(path),
                artifact_size_bytes=size,
            )
            observe_task_completed(TaskStatus.success.value)
        except JitsiEngineError as exc:
            if await self._try_recover_artifact(task_id):
                logger.info("recovered task %s after engine stop error: %s", task_id, exc)
                return
            code = ErrorCode.task_timeout if exc.reason == "timeout" else ErrorCode.invalid_file
            self.store.mark_error(task_id, ErrorDetail(code=code, message=str(exc)))
            observe_task_completed(TaskStatus.error.value)
        except Exception as exc:
            if await self._try_recover_artifact(task_id):
                logger.info("recovered task %s after finalize error: %s", task_id, exc)
                return
            self.store.mark_error(
                task_id,
                ErrorDetail(code=ErrorCode.pipeline_error, message=str(exc)),
            )
            observe_task_completed(TaskStatus.error.value)
        finally:
            self._finalize_tasks.pop(task_id, None)
            observe_finalize(asyncio.get_event_loop().time() - started)

    async def _ttl_loop(self) -> None:
        while True:
            interval = 5 if self.store.count_active() > 0 else 30
            await asyncio.sleep(interval)
            try:
                await self.reconcile_all()
            except Exception as exc:
                logger.warning("reconcile loop failed: %s", exc)
            if self.settings.TASK_TTL_SEC > 0:
                for task_id in self.store.purge_expired(self.settings.TASK_TTL_SEC):
                    remove_task_artifacts(self.settings.DATA_DIR, task_id)
                    logger.info("purged expired task %s", task_id)
