import asyncio
import struct

import pytest

from app.artifacts import PCM_SAMPLE_RATE, append_pcm_samples, temp_pcm_path
from app.manager import CaptureManager
from app.schemas import ErrorCode, ErrorDetail, TaskStatus
from tests.conftest import isolate_env


def _write_pcm(data_dir: str, task_id: str, duration_sec: float = 1.0) -> None:
    path = temp_pcm_path(data_dir, task_id)
    frame_count = int(duration_sec * PCM_SAMPLE_RATE)
    samples = [int(8000 * (i % 100) / 100) for i in range(frame_count)]
    append_pcm_samples(path, samples)


@pytest.mark.asyncio
async def test_recover_error_task_from_spooled_pcm(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    manager = CaptureManager()
    task_id = "pcm-recover"
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
        ErrorDetail(code=ErrorCode.interrupted, message="orphaned"),
    )
    _write_pcm(str(tmp_path), task_id, 0.5)

    assert await manager._try_recover_pcm(task_id) is True
    record = manager.store.get(task_id)
    assert record is not None
    assert record.status is TaskStatus.success
    assert record.artifact_path is not None
    assert record.duration_sec is not None
    assert record.duration_sec > 0
    assert not temp_pcm_path(str(tmp_path), task_id).exists()


@pytest.mark.asyncio
async def test_reconcile_skips_pcm_when_session_active(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    manager = CaptureManager()
    task_id = "live-capture"
    manager.store.create(
        task_id=task_id,
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    manager.store.update_status(task_id, TaskStatus.capturing)
    _write_pcm(str(tmp_path), task_id, 0.25)
    manager.runner.engine._sessions[task_id] = object()  # type: ignore[attr-defined]

    await manager.reconcile_all()
    record = manager.store.get(task_id)
    assert record is not None
    assert record.status is TaskStatus.capturing
