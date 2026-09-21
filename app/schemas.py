"""API schemas for capture tasks (aligned with itranscribe-worker response shapes)."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    queued = "queued"
    joining = "joining"
    capturing = "capturing"
    finalizing = "finalizing"
    success = "success"
    error = "error"
    canceled = "canceled"


class ConnectorStatus(str, Enum):
    loaded = "loaded"
    unavailable = "unavailable"


class ConnectorInfo(BaseModel):
    status: ConnectorStatus
    label: str
    reason: str | None = None


class ErrorCode(str, Enum):
    unauthorized = "unauthorized"
    queue_full = "queue_full"
    not_found = "not_found"
    invalid_url = "invalid_url"
    unsupported_connector = "unsupported_connector"
    join_failed = "join_failed"
    invalid_file = "invalid_file"
    task_timeout = "task_timeout"
    interrupted = "interrupted"
    pipeline_error = "pipeline_error"
    task_running = "task_running"
    artifact_not_ready = "artifact_not_ready"


def error_payload(code: ErrorCode, message: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "error", "error": {"code": code.value}}
    if message is not None:
        payload["error"]["message"] = message
    return payload


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str | None = None


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: str = Field(..., min_length=1)
    meeting_url: str = Field(..., min_length=1)
    pin: str = ""
    jwt: str | None = None
    display_name: str | None = None


class TaskMeta(BaseModel):
    timestamp: str
    task_id: str
    connector: str | None = None
    meeting_host: str | None = None
    meeting_room: str | None = None
    stopped_at: str | None = None
    duration_sec: float | None = None


class ArtifactInfo(BaseModel):
    ready: bool
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None


class TaskResponse(BaseModel):
    status: TaskStatus
    meta: TaskMeta
    artifact: ArtifactInfo | None = None
    error: ErrorDetail | None = None


class TaskListItem(BaseModel):
    task_id: str
    status: TaskStatus
    timestamp: str
    connector: str | None = None


class WorkersPoolInfo(BaseModel):
    """In-process pool capacity (idigest-hub reads max|active|available)."""

    max: int = Field(..., ge=0)
    active: int = Field(..., ge=0)
    available: int = Field(..., ge=0)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    connectors: dict[str, ConnectorInfo]
    workers: WorkersPoolInfo
