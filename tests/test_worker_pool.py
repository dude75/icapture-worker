"""In-process worker pool accounting (no HTTP)."""

from app.config import get_settings
from app.queueing import CaptureRunner
from app.schemas import TaskStatus
from tests.conftest import isolate_env


def test_worker_pool_reflects_pending_tasks(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch, workers="4", worker_queue_size="0")
    runner = CaptureRunner(get_settings())
    try:
        assert runner.worker_pool() == {"max": 4, "active": 0, "available": 4}
        for index in range(3):
            runner.store.create(
                task_id=f"pending-{index}",
                connector="jitsi",
                meeting_host="meet.example.com",
                meeting_room=f"room-{index}",
                display_name="bot",
                pin="",
                jwt=None,
            )
        assert runner.worker_pool() == {"max": 4, "active": 3, "available": 1}
    finally:
        runner.store.close()


def test_worker_pool_caps_active_at_max(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch, workers="2", worker_queue_size="4")
    runner = CaptureRunner(get_settings())
    try:
        for index in range(4):
            runner.store.create(
                task_id=f"queued-{index}",
                connector="jitsi",
                meeting_host="meet.example.com",
                meeting_room=f"room-{index}",
                display_name="bot",
                pin="",
                jwt=None,
            )
        assert runner.worker_pool() == {"max": 2, "active": 2, "available": 0}
    finally:
        runner.store.close()


def test_count_active_includes_joining(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    runner = CaptureRunner(get_settings())
    try:
        runner.store.create(
            task_id="join-task",
            connector="jitsi",
            meeting_host="meet.example.com",
            meeting_room="room",
            display_name="bot",
            pin="",
            jwt=None,
        )
        runner.store.update_status("join-task", TaskStatus.joining)
        assert runner.store.count_active() == 1
    finally:
        runner.store.close()
