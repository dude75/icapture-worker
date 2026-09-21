"""Manager startup recovery and crash-interrupted tasks."""

import pytest

from app.artifacts import write_stub_artifact
from app.manager import CaptureManager
from app.schemas import ErrorCode, TaskStatus
from app.tasks import TaskStore
from tests.conftest import isolate_env


@pytest.mark.asyncio
async def test_restore_joining_marks_interrupted(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    store = TaskStore(str(tmp_path / "tasks.db"))
    task_id = "orphaned-joining"
    store.create(
        task_id=task_id,
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    store.update_status(task_id, TaskStatus.joining)

    manager = CaptureManager()
    await manager.start()
    try:
        record = store.get(task_id)
        assert record is not None
        assert record.status is TaskStatus.error
        assert record.error is not None
        assert record.error["code"] == ErrorCode.interrupted.value
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_restore_unfinished_marks_interrupted(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    store = TaskStore(str(tmp_path / "tasks.db"))
    task_id = "orphaned-capture"
    store.create(
        task_id=task_id,
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    store.update_status(task_id, TaskStatus.capturing)

    manager = CaptureManager()
    await manager.start()
    try:
        record = store.get(task_id)
        assert record is not None
        assert record.status is TaskStatus.error
        assert record.error is not None
        assert record.error["code"] == ErrorCode.interrupted.value
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_restore_finalizing_without_artifact_marks_interrupted(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_env(tmp_path, monkeypatch)
    store = TaskStore(str(tmp_path / "tasks.db"))
    task_id = "orphaned-finalizing"
    store.create(
        task_id=task_id,
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    store.update_status(task_id, TaskStatus.finalizing)

    manager = CaptureManager()
    await manager.start()
    try:
        record = store.get(task_id)
        assert record is not None
        assert record.status is TaskStatus.error
        assert record.error is not None
        assert record.error["code"] == ErrorCode.interrupted.value
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_restore_finalizing_with_artifact_recovers_success(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_env(tmp_path, monkeypatch)
    store = TaskStore(str(tmp_path / "tasks.db"))
    task_id = "recover-on-restart"
    store.create(
        task_id=task_id,
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    store.update_status(task_id, TaskStatus.finalizing)
    write_stub_artifact(str(tmp_path), task_id, 0.5)

    manager = CaptureManager()
    await manager.start()
    try:
        record = store.get(task_id)
        assert record is not None
        assert record.status is TaskStatus.success
        assert record.artifact_path is not None
        assert record.duration_sec is not None
        assert record.duration_sec > 0
    finally:
        await manager.stop()
