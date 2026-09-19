from datetime import datetime, timedelta, timezone

from app.artifact_cleanup import remove_task_artifacts
from app.artifacts import write_stub_artifact
from app.schemas import ErrorCode, ErrorDetail, TaskStatus
from app.tasks import TaskStore
from tests.conftest import isolate_env


def _utc_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_purge_expired_deletes_task_and_artifact(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch, extra={"TASK_TTL_SEC": "60"})
    store = TaskStore(str(tmp_path / "tasks.db"))
    task_id = "ttl-task"
    store.create(
        task_id=task_id,
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    write_stub_artifact(str(tmp_path), task_id, 0.5)
    old = _utc_iso(datetime.now(timezone.utc) - timedelta(hours=2))
    store.mark_success(
        task_id,
        stopped_at=old,
        duration_sec=0.5,
        artifact_path=str(tmp_path / "artifacts" / f"{task_id}.m4a"),
        artifact_size_bytes=128,
    )

    purged = store.purge_expired(3600)
    assert purged == [task_id]
    assert store.get(task_id) is None
    remove_task_artifacts(str(tmp_path), task_id)
    assert not (tmp_path / "artifacts" / f"{task_id}.m4a").exists()


def test_purge_expired_skips_active_and_recent(tmp_path, monkeypatch) -> None:
    store = TaskStore(str(tmp_path / "tasks.db"))
    store.create(
        task_id="active-task",
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    store.create(
        task_id="recent-error",
        connector="jitsi",
        meeting_host="meet.example.com",
        meeting_room="room",
        display_name="bot",
        pin="",
        jwt=None,
    )
    store.mark_error(
        "recent-error",
        ErrorDetail(code=ErrorCode.pipeline_error, message="boom"),
    )

    purged = store.purge_expired(3600)
    assert purged == []
    assert store.get("active-task") is not None
    assert store.get("recent-error") is not None
