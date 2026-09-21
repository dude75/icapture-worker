"""HTTP API for icapture-worker."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from app.artifacts import CONTENT_TYPE as ARTIFACT_CONTENT_TYPE
from app.artifacts import FILENAME as ARTIFACT_FILENAME
from app.auth import require_api_token, require_metrics_token
from app.config import get_settings
from app.logging_setup import setup_logging
from app.jitsi_client import JitsiEngineError
from app.manager import CaptureManager, QueueFullError, TaskConflictError
from app.prometheus_metrics import (
    CONTENT_TYPE,
    PrometheusHttpMiddleware,
    create_metrics,
    render,
    set_active,
)
from app.response_builder import record_to_list_item, record_to_response
from app.schemas import (
    CaptureRequest,
    ConnectorInfo,
    ConnectorStatus,
    ErrorCode,
    HealthResponse,
    SlotsInfo,
    TaskListItem,
    TaskResponse,
    TaskStatus,
    error_payload,
)
from app.version import read_version


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings)
    metrics = create_metrics(settings)
    set_active(metrics)
    manager = CaptureManager(settings)
    await manager.start()
    metrics.bind(manager=manager)
    app.state.manager = manager
    try:
        yield
    finally:
        await manager.stop()
        set_active(None)


app = FastAPI(title="icapture-worker", version=read_version(), lifespan=lifespan)
app.add_middleware(PrometheusHttpMiddleware)


def _api_error(status_code: int, code: ErrorCode, message: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message),
        headers={"Connection": "close"},
    )


def _http_error(status_code: int, code: ErrorCode, message: str | None = None) -> HTTPException:
    return HTTPException(status_code=status_code, detail=error_payload(code, message))


def get_manager() -> CaptureManager:
    return app.state.manager


@app.exception_handler(HTTPException)
async def http_exception_handler(_request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def health(manager: CaptureManager = Depends(get_manager)) -> HealthResponse:
    await manager.refresh_engine_health()
    connectors_raw = manager.connector_status()
    connectors = {
        name: ConnectorInfo(
            status=ConnectorStatus(str(info["status"])),
            label=str(info["label"]),
            reason=info.get("reason"),
        )
        for name, info in connectors_raw.items()
    }
    return HealthResponse(
        status="ok",
        version=read_version(),
        connectors=connectors,
        slots=manager.slots(),
    )


@app.get("/ready", response_model=None)
async def ready(manager: CaptureManager = Depends(get_manager)):
    slots: SlotsInfo = manager.slots()
    if slots.available > 0:
        return {"status": "ready"}
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "full"})


@app.get("/metrics")
def metrics(_: str = Depends(require_metrics_token)) -> Response:
    return Response(content=render(), media_type=CONTENT_TYPE)


@app.get("/tasks", response_model=list[TaskListItem])
def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    _: str = Depends(require_api_token),
    manager: CaptureManager = Depends(get_manager),
) -> list[TaskListItem]:
    return [
        record_to_list_item(record)
        for record in manager.store.list_tasks(status_filter)
    ]


@app.post("/capture", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def capture(
    request: Request,
    body: CaptureRequest,
    _: str = Depends(require_api_token),
    manager: CaptureManager = Depends(get_manager),
):
    task_id_out: list[str | None] = [None]

    async def cancel_if_client_gone() -> None:
        while True:
            if await request.is_disconnected():
                task_id = task_id_out[0]
                if task_id:
                    with contextlib.suppress(Exception):
                        await manager.cancel_capture(task_id)
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(cancel_if_client_gone())
    try:
        record = await manager.create_capture(body, task_id_out=task_id_out)
    except QueueFullError:
        return _api_error(status.HTTP_503_SERVICE_UNAVAILABLE, ErrorCode.queue_full)
    except JitsiEngineError:
        return _api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, ErrorCode.join_failed)
    except ValueError as exc:
        code = exc.args[0] if exc.args else ErrorCode.pipeline_error
        if code is ErrorCode.join_failed:
            return _api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, ErrorCode.join_failed)
        if code is ErrorCode.invalid_url:
            return _api_error(status.HTTP_400_BAD_REQUEST, ErrorCode.invalid_url)
        if code is ErrorCode.unsupported_connector:
            return _api_error(status.HTTP_400_BAD_REQUEST, ErrorCode.unsupported_connector)
        return _api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, ErrorCode.pipeline_error)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
    return record_to_response(record)


@app.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    _: str = Depends(require_api_token),
    manager: CaptureManager = Depends(get_manager),
) -> TaskResponse:
    if manager.store.get(task_id) is None:
        raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.not_found)
    await manager.refresh_capture_state(task_id)
    record = manager.store.get(task_id)
    if record is None:
        raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.not_found)
    return record_to_response(record)


@app.post("/tasks/{task_id}/stop", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def stop_task(
    task_id: str,
    _: str = Depends(require_api_token),
    manager: CaptureManager = Depends(get_manager),
) -> TaskResponse:
    try:
        record = await manager.stop_capture(task_id)
    except KeyError:
        raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.not_found) from None
    except TaskConflictError as exc:
        raise _http_error(status.HTTP_409_CONFLICT, exc.code) from None
    return record_to_response(record)


@app.get("/tasks/{task_id}/download", response_model=None)
def download_task(
    task_id: str,
    _: str = Depends(require_api_token),
    manager: CaptureManager = Depends(get_manager),
):
    try:
        path = manager.get_artifact_path(task_id)
    except KeyError:
        return _api_error(status.HTTP_404_NOT_FOUND, ErrorCode.not_found)
    except ValueError:
        return _api_error(status.HTTP_404_NOT_FOUND, ErrorCode.not_found)
    return FileResponse(path, media_type=ARTIFACT_CONTENT_TYPE, filename=ARTIFACT_FILENAME)


@app.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    _: str = Depends(require_api_token),
    manager: CaptureManager = Depends(get_manager),
) -> dict[str, str]:
    try:
        await manager.cancel_capture(task_id)
    except KeyError:
        raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.not_found) from None
    except TaskConflictError as exc:
        raise _http_error(status.HTTP_409_CONFLICT, exc.code) from None
    return {"status": "ok"}
