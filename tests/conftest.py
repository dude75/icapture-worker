"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


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
    max_captures: str = "2",
    extra: dict[str, str] | None = None,
) -> None:
    monkeypatch.setenv("API_TOKEN", "test-api")
    monkeypatch.setenv("METRICS_TOKEN", "test-metrics")
    monkeypatch.setenv("ICAPTURE_STUBS", "1")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MAX_CONCURRENT_CAPTURES", max_captures)
    monkeypatch.setenv("TASK_TTL_SEC", "3600")
    monkeypatch.setenv("MAX_CAPTURE_DURATION_SEC", "300")
    if extra:
        for key, value in extra.items():
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    isolate_env(tmp_path, monkeypatch, max_captures="2")
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().API_TOKEN}"}


def metrics_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().METRICS_TOKEN}"}
