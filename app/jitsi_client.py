"""HTTP client for the Node Jitsi capture engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("app")


@dataclass
class EngineHealth:
    ok: bool
    jitsi_status: str
    jitsi_reason: str | None = None


class JitsiEngineError(Exception):
    def __init__(self, message: str, *, reason: str = "engine_error") -> None:
        super().__init__(message)
        self.reason = reason


class JitsiEngineClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base = settings.JITSI_ENGINE_URL.rstrip("/")
        self._timeout = settings.JITSI_ENGINE_TIMEOUT_SEC

    async def health(self) -> EngineHealth:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base}/health")
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("jitsi engine health probe failed: %s", exc)
            return EngineHealth(ok=False, jitsi_status="unavailable", jitsi_reason="engine_unreachable")
        connector = (payload.get("connectors") or {}).get("jitsi") or {}
        status = str(connector.get("status") or "unavailable")
        reason = connector.get("reason")
        return EngineHealth(ok=True, jitsi_status=status, jitsi_reason=reason)

    async def start_capture(
        self,
        *,
        task_id: str,
        meeting_host: str,
        meeting_room: str,
        display_name: str,
        pin: str,
        jwt: str | None,
    ) -> None:
        body: dict[str, Any] = {
            "task_id": task_id,
            "meeting_host": meeting_host,
            "meeting_room": meeting_room,
            "display_name": display_name,
            "pin": pin,
        }
        if jwt:
            body["jwt"] = jwt
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base}/capture/start", json=body)
        except httpx.TimeoutException as exc:
            raise JitsiEngineError("engine timeout", reason="timeout") from exc
        except httpx.HTTPError as exc:
            raise JitsiEngineError("engine unreachable", reason="network") from exc
        if response.status_code >= 400:
            payload = response.json() if response.content else {}
            reason = str((payload.get("error") or {}).get("reason") or "join_failed")
            message = str((payload.get("error") or {}).get("message") or "join failed")
            raise JitsiEngineError(message, reason=reason)
        response.raise_for_status()

    async def stop_capture(self, task_id: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/capture/stop",
                    json={"task_id": task_id},
                )
        except httpx.TimeoutException as exc:
            raise JitsiEngineError("engine timeout", reason="timeout") from exc
        except httpx.HTTPError as exc:
            raise JitsiEngineError("engine unreachable", reason="network") from exc
        if response.status_code >= 400:
            payload = response.json() if response.content else {}
            reason = str((payload.get("error") or {}).get("reason") or "finalize_failed")
            message = str((payload.get("error") or {}).get("message") or "finalize failed")
            raise JitsiEngineError(message, reason=reason)
        payload = response.json()
        artifact_path = payload.get("artifact_path")
        if not artifact_path:
            raise JitsiEngineError("engine returned no artifact", reason="invalid_file")
        return str(artifact_path)

    async def list_sessions(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base}/capture/sessions")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("jitsi engine session list failed: %s", exc)
            return []
        task_ids = payload.get("task_ids")
        if not isinstance(task_ids, list):
            return []
        return [str(task_id) for task_id in task_ids]

    async def cancel_capture(self, task_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(f"{self._base}/capture/cancel", json={"task_id": task_id})
        except httpx.HTTPError:
            logger.warning("cancel request to jitsi engine failed for %s", task_id)
