"""In-process stub capture when ICAPTURE_STUBS=1."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.artifacts import write_stub_artifact
from app.config import Settings


@dataclass
class StubSession:
    task_id: str
    started_monotonic: float = field(default_factory=time.monotonic)
    canceled: bool = False


class StubCaptureEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: dict[str, StubSession] = {}
        self._auto_stop_tasks: dict[str, asyncio.Task[None]] = {}

    async def start(
        self,
        *,
        task_id: str,
        meeting_host: str,
        meeting_room: str,
        display_name: str,
        pin: str,
        jwt: str | None,
        on_auto_stop,
    ) -> None:
        del meeting_host, meeting_room, display_name, pin, jwt
        self._sessions[task_id] = StubSession(task_id=task_id)
        if self.settings.MAX_CAPTURE_DURATION_SEC > 0:
            self._auto_stop_tasks[task_id] = asyncio.create_task(
                self._auto_stop(task_id, on_auto_stop)
            )

    async def _auto_stop(self, task_id: str, on_auto_stop) -> None:
        await asyncio.sleep(self.settings.MAX_CAPTURE_DURATION_SEC)
        if task_id in self._sessions and not self._sessions[task_id].canceled:
            await on_auto_stop(task_id, reason="task_timeout")

    async def stop(self, task_id: str) -> str:
        session = self._sessions.pop(task_id, None)
        auto_task = self._auto_stop_tasks.pop(task_id, None)
        if auto_task is not None:
            auto_task.cancel()
        if session is None or session.canceled:
            raise RuntimeError("stub session not active")
        duration = max(time.monotonic() - session.started_monotonic, 0.1)
        path = await asyncio.to_thread(
            write_stub_artifact,
            self.settings.DATA_DIR,
            task_id,
            duration,
        )
        return str(path)

    async def cancel(self, task_id: str) -> None:
        session = self._sessions.pop(task_id, None)
        auto_task = self._auto_stop_tasks.pop(task_id, None)
        if auto_task is not None:
            auto_task.cancel()
        if session is not None:
            session.canceled = True
