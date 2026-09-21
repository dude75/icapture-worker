import asyncio

import pytest

from app.artifacts import write_stub_artifact
from app.manager import CaptureManager
from app.schemas import ErrorCode, ErrorDetail, TaskStatus
from tests.conftest import isolate_env


def _create_error_task(manager: CaptureManager, task_id: str) -> None:
    manager.store.create(
        task_id=task_id,
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    manager.store.mark_error(
        task_id,
        ErrorDetail(code=ErrorCode.pipeline_error, message="file does not start with RIFF id"),
    )


@pytest.mark.asyncio
async def test_recover_error_task_from_valid_artifact(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    manager = CaptureManager()
    task_id = "recover-me"
    _create_error_task(manager, task_id)
    write_stub_artifact(str(tmp_path), task_id, 1.0)

    assert await manager._try_recover_artifact(task_id) is True
    record = manager.store.get(task_id)
    assert record is not None
    assert record.status is TaskStatus.success
    assert record.artifact_path is not None
    assert record.duration_sec is not None
    assert record.duration_sec > 0


@pytest.mark.asyncio
async def test_reconcile_recoverable_errors(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    manager = CaptureManager()
    task_id = "error-with-file"
    _create_error_task(manager, task_id)
    write_stub_artifact(str(tmp_path), task_id, 0.5)

    await manager.reconcile_all()
    record = manager.store.get(task_id)
    assert record is not None
    assert record.status is TaskStatus.success

