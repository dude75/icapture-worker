"""Map TaskRecord to API responses."""

from __future__ import annotations

from app.artifacts import CONTENT_TYPE, FILENAME
from app.schemas import ArtifactInfo, ErrorDetail, TaskListItem, TaskMeta, TaskResponse
from app.tasks import TaskRecord


def record_to_response(record: TaskRecord) -> TaskResponse:
    error = ErrorDetail.model_validate(record.error) if record.error else None
    artifact = None
    if record.status.value == "success" and record.artifact_path:
        artifact = ArtifactInfo(
            ready=True,
            filename=FILENAME,
            content_type=CONTENT_TYPE,
            size_bytes=record.artifact_size_bytes,
        )
    return TaskResponse(
        status=record.status,
        meta=TaskMeta(
            timestamp=record.started_at,
            task_id=record.task_id,
            connector=record.connector,
            meeting_host=record.meeting_host,
            meeting_room=record.meeting_room,
            stopped_at=record.stopped_at,
            duration_sec=record.duration_sec,
        ),
        artifact=artifact,
        error=error,
    )


def record_to_list_item(record: TaskRecord) -> TaskListItem:
    return TaskListItem(
        task_id=record.task_id,
        status=record.status,
        timestamp=record.started_at,
        connector=record.connector,
    )
