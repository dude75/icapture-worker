"""Capture task lifecycle (queue + in-process workers)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.artifact_cleanup import remove_task_artifacts
from app.artifacts import (
    artifact_path,
    convert_pcm_to_mp3,
    ensure_artifacts_dir,
    ensure_tmp_dir,
    temp_pcm_path,
    validate_artifact,
)
from app.capture import browser as browser_capture
from app.config import Settings, get_settings
from app.url_parser import InvalidMeetingUrl, parse_capture_url
from app.prometheus_metrics import observe_finalize, observe_task_transition
from app.queueing import CaptureRunner, QueueFullError, TaskConflictError
from app.schemas import CaptureRequest, ConnectorStatus, ErrorCode, ErrorDetail, TaskStatus
from app.tasks import TaskRecord

logger = logging.getLogger("app")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


SUPPORTED_CONNECTORS = {"jitsi", "telemost", "zoom", "meet"}


class CaptureManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.runner = CaptureRunner(self.settings)
        self.store = self.runner.store
        self._finalize_tasks: dict[str, asyncio.Task[None]] = {}
        self._finalize_started_at: dict[str, float] = {}
        self._ttl_task: asyncio.Task[None] | None = None
        self._browser_ok = False
        self._browser_reason: str | None = "startup"

    async def start(self) -> None:
        ensure_artifacts_dir(self.settings.DATA_DIR)
        ensure_tmp_dir(self.settings.DATA_DIR)
        Path(self.settings.LOG_DIR).mkdir(parents=True, exist_ok=True)

        async def on_auto_stop(task_id: str, *, reason: str) -> None:
            await self._handle_auto_stop(task_id, reason=reason)

        self._browser_ok, self._browser_reason = await browser_capture.check_browser_ready()
        self.runner.configure(on_auto_stop=on_auto_stop)
        await self.runner.start()
        self._ttl_task = asyncio.create_task(self._ttl_loop())
        await self.reconcile_all()

    async def stop(self) -> None:
        if self._ttl_task is not None:
            self._ttl_task.cancel()
        for task in list(self._finalize_tasks.values()):
            task.cancel()
        await self.runner.stop()

    def connector_status(self) -> dict[str, dict[str, str | None]]:
        enabled = self.settings.enabled_connectors
        result: dict[str, dict[str, str | None]] = {}
        labels = {
            "jitsi": "Jitsi Meet",
            "telemost": "Yandex Telemost",
            "zoom": "Zoom",
            "meet": "Google Meet",
        }
        browser_connectors = {"jitsi", "telemost"}
        for name in ("jitsi", "telemost", "zoom", "meet"):
            label = labels[name]
            if name not in enabled:
                result[name] = {
                    "status": ConnectorStatus.unavailable.value,
                    "label": label,
                    "reason": "disabled",
                }
            elif name in browser_connectors:
                if self._browser_ok:
                    result[name] = {"status": ConnectorStatus.loaded.value, "label": label}
                else:
                    result[name] = {
                        "status": ConnectorStatus.unavailable.value,
                        "label": label,
                        "reason": self._browser_reason or "browser_unavailable",
                    }
            else:
                result[name] = {
                    "status": ConnectorStatus.unavailable.value,
                    "label": label,
                    "reason": "not_implemented",
                }
        return result

    async def refresh_browser_health(self) -> None:
        self._browser_ok, self._browser_reason = await browser_capture.check_browser_ready()

    def worker_pool(self) -> dict[str, int]:
        return self.runner.worker_pool()

    async def reconcile_all(self) -> None:
        for status in (TaskStatus.error, TaskStatus.finalizing, TaskStatus.capturing):
            for record in self.store.list_tasks(status):
                if self.runner.engine.is_active(record.task_id):
                    continue
                if await self._try_recover_artifact(record.task_id):
                    logger.info("recovered %s task %s from artifact", status.value, record.task_id)
                elif await self._try_recover_pcm(record.task_id):
                    logger.info("recovered %s task %s from spooled pcm", status.value, record.task_id)

    async def create_capture(
        self,
        request: CaptureRequest,
        *,
        task_id_out: list[str | None] | None = None,
    ) -> TaskRecord:
        connector = request.connector.lower()
        if connector not in SUPPORTED_CONNECTORS:
            raise ValueError(ErrorCode.unsupported_connector)
        if connector not in self.settings.enabled_connectors:
            raise ValueError(ErrorCode.unsupported_connector)
        if connector not in {"jitsi", "telemost"}:
            raise ValueError(ErrorCode.unsupported_connector)

        display_name = (request.display_name or self.settings.DEFAULT_BOT_DISPLAY_NAME).strip()
        pin = request.pin or ""

        try:
            parse_capture_url(connector, request.meeting_url)
        except InvalidMeetingUrl as exc:
            raise ValueError(ErrorCode.invalid_url) from exc

        await self.refresh_browser_health()
        if not self._browser_ok:
            raise RuntimeError(self._browser_reason or "browser_unavailable")

        try:
            record = await self.runner.submit_from_url(
                connector=connector,
                meeting_url=request.meeting_url,
                display_name=display_name,
                pin=pin,
                jwt=request.jwt,
            )
        except ValueError as exc:
            if exc.args and exc.args[0] is ErrorCode.invalid_url:
                raise
            raise

        if task_id_out is not None:
            task_id_out[0] = record.task_id
        return record

    async def stop_capture(self, task_id: str) -> TaskRecord:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status is TaskStatus.success:
            return record
        if record.status is TaskStatus.queued:
            raise TaskConflictError()
        if record.status is TaskStatus.finalizing:
            return record
        if record.status is TaskStatus.joining:
            await self.runner.engine.cancel(task_id)
            remove_task_artifacts(self.settings.DATA_DIR, task_id)
            self.store.mark_canceled(task_id)
            observe_task_transition(TaskStatus.canceled.value)
            return self.store.get(task_id)  # type: ignore[return-value]
        if record.status is TaskStatus.capturing:
            self.store.update_status(task_id, TaskStatus.finalizing)
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
        if record.status is TaskStatus.queued:
            await self.runner.delete_queued_or_cancel(task_id)
            remove_task_artifacts(self.settings.DATA_DIR, task_id)
            return self.store.get(task_id)  # type: ignore[return-value]
        if record.status in {TaskStatus.joining, TaskStatus.capturing, TaskStatus.finalizing}:
            finalize_task = self._finalize_tasks.pop(task_id, None)
            if finalize_task is not None:
                finalize_task.cancel()
            await self.runner.engine.cancel(task_id)
            remove_task_artifacts(self.settings.DATA_DIR, task_id)
            self.store.mark_canceled(task_id)
            observe_task_transition(TaskStatus.canceled.value)
            return self.store.get(task_id)  # type: ignore[return-value]
        return record

    def get_artifact_path(self, task_id: str) -> Path:
        path, _size = self.get_artifact_download(task_id)
        return path

    def get_artifact_download(self, task_id: str) -> tuple[Path, int]:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status is not TaskStatus.success:
            raise ValueError(ErrorCode.artifact_not_ready)
        path = Path(record.artifact_path or "")
        if not path.is_file():
            raise ValueError(ErrorCode.artifact_not_ready)
        expected_size = record.artifact_size_bytes
        if expected_size is None or expected_size < 128:
            raise ValueError(ErrorCode.artifact_not_ready)
        if path.stat().st_size != expected_size:
            raise ValueError(ErrorCode.artifact_not_ready)
        return path, expected_size

    async def refresh_capture_state(self, task_id: str) -> None:
        record = self.store.get(task_id)
        if record is None:
            return
        if record.status is TaskStatus.error:
            if not await self._try_recover_artifact(task_id):
                await self._try_recover_pcm(task_id)

    async def _handle_auto_stop(self, task_id: str, *, reason: str) -> None:
        del reason
        try:
            await self.stop_capture(task_id)
            await self.wait_until_terminal(
                task_id,
                timeout_sec=self.settings.CAPTURE_FINALIZE_TIMEOUT_SEC,
            )
        except Exception as exc:
            logger.warning("auto-stop failed for %s: %s", task_id, exc)

    async def _finalize(self, task_id: str) -> None:
        started = asyncio.get_event_loop().time()
        self._finalize_started_at[task_id] = started
        try:
            engine_path = await asyncio.wait_for(
                self.runner.engine.stop(task_id),
                timeout=self.settings.CAPTURE_FINALIZE_TIMEOUT_SEC,
            )
            path = Path(engine_path)
            if not path.is_file():
                path = artifact_path(self.settings.DATA_DIR, task_id)
            size, duration = await asyncio.to_thread(validate_artifact, path)
            self.store.mark_success(
                task_id,
                stopped_at=_utc_now_iso(),
                duration_sec=duration,
                artifact_path=str(path),
                artifact_size_bytes=size,
            )
            observe_task_transition(TaskStatus.success.value)
        except Exception as exc:
            if await self._recover_after_finalize_failure(task_id, exc):
                return
            self.store.mark_error(
                task_id,
                ErrorDetail(code=ErrorCode.pipeline_error, message=str(exc)),
            )
            observe_task_transition(TaskStatus.error.value)
        finally:
            self._finalize_tasks.pop(task_id, None)
            self._finalize_started_at.pop(task_id, None)
            observe_finalize(asyncio.get_event_loop().time() - started)

    async def _recover_after_finalize_failure(self, task_id: str, exc: Exception) -> bool:
        if isinstance(exc, asyncio.TimeoutError):
            logger.warning("finalize timed out task=%s — trying spooled pcm", task_id)
        session = self.runner.engine._sessions.get(task_id)
        if session is not None:
            session.cancel_pcm_spool()
        if await self._try_recover_artifact(task_id):
            logger.info("recovered task %s after finalize error: %s", task_id, exc)
            await self.runner.engine.abort_session(task_id)
            return True
        if await self._try_recover_pcm(task_id):
            logger.info("recovered task %s from spooled pcm after finalize error: %s", task_id, exc)
            await self.runner.engine.abort_session(task_id)
            return True
        if self.runner.engine.is_active(task_id):
            await self.runner.engine.abort_session(task_id)
        return False

    async def _try_recover_artifact(self, task_id: str) -> bool:
        path = artifact_path(self.settings.DATA_DIR, task_id)
        if not path.is_file():
            return False
        try:
            size, duration = await asyncio.to_thread(validate_artifact, path)
        except ValueError:
            return False
        self.store.mark_success(
            task_id,
            stopped_at=_utc_now_iso(),
            duration_sec=duration,
            artifact_path=str(path),
            artifact_size_bytes=size,
        )
        observe_task_transition(TaskStatus.success.value)
        return True

    async def _nudge_stuck_finalizing(self) -> None:
        """Finalize must not block forever on Playwright; recover from spooled PCM."""
        now = asyncio.get_event_loop().time()
        grace = self.settings.CAPTURE_FINALIZE_TIMEOUT_SEC + 30.0
        for record in self.store.list_tasks(TaskStatus.finalizing):
            started = self._finalize_started_at.get(record.task_id)
            if started is None:
                pcm_path = temp_pcm_path(self.settings.DATA_DIR, record.task_id)
                if pcm_path.is_file():
                    idle_sec = now - pcm_path.stat().st_mtime
                    spool_grace = max(30.0, self.settings.CAPTURE_PCM_SPOOL_INTERVAL_SEC * 3)
                    if idle_sec >= spool_grace:
                        started = now - grace - 1
            if started is None or now - started < grace:
                continue
            logger.warning(
                "finalizing exceeded timeout task=%s — forcing pcm recovery",
                record.task_id,
            )
            finalize_task = self._finalize_tasks.get(record.task_id)
            if finalize_task is not None:
                finalize_task.cancel()
            if await self._recover_after_finalize_failure(
                record.task_id,
                asyncio.TimeoutError(),
            ):
                self._finalize_tasks.pop(record.task_id, None)
                self._finalize_started_at.pop(record.task_id, None)

    async def _try_recover_pcm(self, task_id: str) -> bool:
        pcm_path = temp_pcm_path(self.settings.DATA_DIR, task_id)
        if not pcm_path.is_file() or pcm_path.stat().st_size == 0:
            return False
        mp3_path = artifact_path(self.settings.DATA_DIR, task_id)
        if mp3_path.is_file():
            return await self._try_recover_artifact(task_id)
        try:
            await asyncio.to_thread(convert_pcm_to_mp3, pcm_path, mp3_path)
            pcm_path.unlink(missing_ok=True)
            size, duration = await asyncio.to_thread(validate_artifact, mp3_path)
        except ValueError:
            return False
        self.store.mark_success(
            task_id,
            stopped_at=_utc_now_iso(),
            duration_sec=duration,
            artifact_path=str(mp3_path),
            artifact_size_bytes=size,
        )
        observe_task_transition(TaskStatus.success.value)
        return True

    async def _ttl_loop(self) -> None:
        while True:
            interval = 5 if self.store.count_active() > 0 else 30
            await asyncio.sleep(interval)
            try:
                await self._nudge_stuck_finalizing()
                await self.reconcile_all()
            except Exception as exc:
                logger.warning("reconcile loop failed: %s", exc)
            if self.settings.TASK_TTL_SEC > 0:
                for task_id in self.store.purge_expired(self.settings.TASK_TTL_SEC):
                    remove_task_artifacts(self.settings.DATA_DIR, task_id)
                    logger.info("purged expired task %s", task_id)
