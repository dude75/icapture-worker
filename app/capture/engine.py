"""In-process capture via Playwright (real meeting audio)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.capture.browser import BrowserCaptureSession
from app.config import Settings

logger = logging.getLogger("app")


class CaptureEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: dict[str, BrowserCaptureSession] = {}
        self._finished: dict[str, asyncio.Event] = {}
        self._auto_stop: dict[str, asyncio.Task[None]] = {}
        self._disconnect_watch: dict[str, asyncio.Task[None]] = {}

    def is_active(self, task_id: str) -> bool:
        return task_id in self._sessions

    def list_active(self) -> list[str]:
        return list(self._sessions.keys())

    async def run_capture(
        self,
        task_id: str,
        slot: int,
        *,
        connector: str,
        meeting_host: str,
        meeting_room: str,
        display_name: str,
        pin: str,
        jwt: str | None,
        on_joined,
        on_auto_stop,
    ) -> None:
        del jwt
        finished = asyncio.Event()
        self._finished[task_id] = finished
        session = BrowserCaptureSession(
            settings=self.settings,
            task_id=task_id,
            slot=slot,
            connector=connector,
            meeting_host=meeting_host,
            meeting_room=meeting_room,
            display_name=display_name,
            pin=pin,
        )
        self._sessions[task_id] = session
        if self.settings.MAX_CAPTURE_DURATION_SEC > 0:
            self._auto_stop[task_id] = asyncio.create_task(
                self._auto_stop_task(task_id, on_auto_stop)
            )
        exc_to_raise: BaseException | None = None
        disconnect_watch: asyncio.Task[None] | None = None
        try:
            await session.open_and_join()
            if on_joined is not None:
                await on_joined()
            disconnect_watch = asyncio.create_task(
                self._watch_disconnect(task_id, session, on_auto_stop)
            )
            self._disconnect_watch[task_id] = disconnect_watch
            await finished.wait()
        except BaseException as exc:
            exc_to_raise = exc
        finally:
            watch = self._disconnect_watch.pop(task_id, disconnect_watch)
            if watch is not None:
                watch.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watch
            self._auto_stop.pop(task_id, None)
            self._finished.pop(task_id, None)
            self._sessions.pop(task_id, None)
            try:
                await session.close()
            except Exception as close_exc:
                logger.warning("browser close task=%s: %s", task_id, close_exc)
        if exc_to_raise is not None:
            raise exc_to_raise

    async def _auto_stop_task(self, task_id: str, on_auto_stop) -> None:
        await asyncio.sleep(self.settings.MAX_CAPTURE_DURATION_SEC)
        if task_id not in self._sessions:
            return
        await on_auto_stop(task_id, reason="task_timeout")

    async def _watch_disconnect(
        self,
        task_id: str,
        session: BrowserCaptureSession,
        on_auto_stop,
    ) -> None:
        try:
            reason = await session.wait_for_disconnect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("disconnect watch failed task=%s: %s", task_id, exc)
            return
        if task_id not in self._sessions:
            return
        logger.info("browser capture auto-stop task=%s reason=%s", task_id, reason)
        await on_auto_stop(task_id, reason=reason)

    async def stop(self, task_id: str) -> str:
        session = self._sessions.get(task_id)
        if session is None:
            raise RuntimeError("capture session not active")
        auto = self._auto_stop.pop(task_id, None)
        if auto is not None:
            auto.cancel()
        path = await session.finalize_to_mp3()
        finished = self._finished.get(task_id)
        if finished is not None:
            finished.set()
        return str(path)

    async def cancel(self, task_id: str) -> None:
        auto = self._auto_stop.pop(task_id, None)
        if auto is not None:
            auto.cancel()
        session = self._sessions.pop(task_id, None)
        finished = self._finished.pop(task_id, None)
        if session is not None:
            await session.close()
        if finished is not None:
            finished.set()
