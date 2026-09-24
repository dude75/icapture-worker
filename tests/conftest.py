"""Shared pytest fixtures."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.capture import browser as browser_module
from app.capture import telemost as telemost_module
from app.config import get_settings

_FAKE_MEETING = Path(__file__).resolve().parent / "fixtures" / "fake_meeting.html"


@pytest.fixture(scope="session", autouse=True)
def _browser_test_meeting() -> Iterator[None]:
    def _test_url(meeting_host: str, meeting_room: str, display_name: str) -> str:
        del meeting_host, meeting_room, display_name
        return _FAKE_MEETING.as_uri()

    def _test_private_join(meeting_url: str) -> str:
        del meeting_url
        return _FAKE_MEETING.as_uri()

    browser_module.build_meeting_url = _test_url  # type: ignore[method-assign]
    telemost_module.build_private_join_url = _test_private_join  # type: ignore[method-assign]

    async def _browser_ready() -> tuple[bool, str | None]:
        return True, None

    browser_module.check_browser_ready = _browser_ready  # type: ignore[method-assign]
    yield


@pytest.fixture(scope="session", autouse=True)
def _test_tokens() -> Iterator[None]:
    mp = pytest.MonkeyPatch()
    mp.setenv("API_TOKEN", "test-api")
    mp.setenv("METRICS_TOKEN", "test-metrics")
    get_settings.cache_clear()
    yield
    mp.undo()
    get_settings.cache_clear()


def isolate_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    workers: str = "2",
    worker_queue_size: str = "0",
    extra: dict[str, str] | None = None,
) -> None:
    monkeypatch.setenv("API_TOKEN", "test-api")
    monkeypatch.setenv("METRICS_TOKEN", "test-metrics")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKERS", workers)
    monkeypatch.setenv("WORKER_QUEUE_SIZE", worker_queue_size)
    monkeypatch.setenv("TASK_TTL_SEC", "3600")
    monkeypatch.setenv("MAX_CAPTURE_DURATION_SEC", "300")
    monkeypatch.setenv("ENABLED_CONNECTORS", "jitsi")
    if extra:
        for key, value in extra.items():
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    isolate_env(tmp_path, monkeypatch, workers="2")
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().API_TOKEN}"}


def metrics_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().METRICS_TOKEN}"}


def wait_for_task_status(
    client: TestClient,
    task_id: str,
    target: str,
    *,
    timeout_sec: float = 5.0,
) -> dict:
    deadline = time.time() + timeout_sec
    body: dict | None = None
    while time.time() < deadline:
        poll = client.get(f"/tasks/{task_id}", headers=auth_headers())
        body = poll.json()
        if body["status"] == target:
            return body
        time.sleep(0.05)
    assert body is not None
    assert body["status"] == target
    return body


def start_capture(
    client: TestClient,
    *,
    url: str = "https://meet.example.com/TestRoom",
) -> str:
    response = client.post(
        "/capture",
        headers=auth_headers(),
        json={
            "connector": "jitsi",
            "meeting_url": url,
            "pin": "",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] in {"queued", "joining", "capturing"}
    assert body["artifact"] is None
    assert body["error"] is None
    task_id = body["meta"]["task_id"]
    if body["status"] != "capturing":
        wait_for_task_status(client, task_id, "capturing")
    return task_id


def complete_capture(client: TestClient, task_id: str) -> dict:
    time.sleep(0.75)
    stop = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert stop.status_code == 202
    return wait_for_task_status(client, task_id, "success")
