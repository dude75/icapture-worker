"""Prometheus registry and scrape-time gauges."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.requests import ClientDisconnect, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    Info,
    generate_latest,
)
from prometheus_client.gc_collector import GCCollector
from prometheus_client.metrics_core import GaugeMetricFamily
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector
from prometheus_client.registry import Collector
from starlette.requests import Request
from starlette.routing import Match

from app.version import read_version

if TYPE_CHECKING:
    from app.config import Settings
    from app.manager import CaptureManager

CONTENT_TYPE = CONTENT_TYPE_LATEST
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
FINALIZE_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)

_active: Metrics | None = None


def http_path_template(request: Request) -> str:
    for route in request.app.router.routes:
        if not hasattr(route, "matches"):
            continue
        match, _child = route.matches(request.scope)
        if match == Match.FULL:
            path = getattr(route, "path", None)
            if isinstance(path, str) and path:
                return path
    return "unknown"


@dataclass
class RuntimeState:
    settings: Settings | None = None
    manager: CaptureManager | None = None
    manager_started: bool = False


class RuntimeCollector(Collector):
    def __init__(self, state: RuntimeState) -> None:
        self.state = state

    def collect(self):
        yield GaugeMetricFamily("icapture_up", "Process is serving /metrics", value=1.0)
        active = 0.0
        queued = 0.0
        workers = 0.0
        if self.state.manager is not None:
            active = float(self.state.manager.store.count_active())
            queued = float(self.state.manager.store.count_queued())
            workers = float(self.state.settings.WORKERS if self.state.settings else 0)
        yield GaugeMetricFamily(
            "icapture_capture_tasks_active",
            "Queued+joining+capturing+finalizing tasks",
            value=active,
        )
        yield GaugeMetricFamily("icapture_capture_tasks_queued", "Queued capture tasks", value=queued)
        yield GaugeMetricFamily("icapture_worker_slots", "Configured WORKERS", value=workers)


class Metrics:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = CollectorRegistry(auto_describe=True)
        self.state = RuntimeState(settings=settings)
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)
        self.registry.register(RuntimeCollector(self.state))

        self.task_transitions = Counter(
            "icapture_capture_task_transitions_total",
            "Capture task status transitions",
            ["status"],
            registry=self.registry,
        )
        self.join_failures = Counter(
            "icapture_capture_join_failures_total",
            "Failed room joins",
            ["reason"],
            registry=self.registry,
        )
        self.finalize_seconds = Histogram(
            "icapture_capture_finalize_seconds",
            "Finalize latency in seconds",
            buckets=FINALIZE_BUCKETS,
            registry=self.registry,
        )
        self.http_requests = Counter(
            "icapture_http_requests_total",
            "HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "icapture_http_request_duration_seconds",
            "HTTP request duration",
            ["method", "route"],
            buckets=HTTP_BUCKETS,
            registry=self.registry,
        )
        Info("icapture", "icapture-worker build info", registry=self.registry).info(
            {"version": read_version()}
        )

    def bind(self, *, manager: CaptureManager) -> None:
        self.state.manager = manager
        self.state.manager_started = True


def create_metrics(settings: Settings) -> Metrics:
    return Metrics(settings)


def set_active(metrics: Metrics | None) -> None:
    global _active
    _active = metrics


def get_active() -> Metrics | None:
    return _active


def render() -> bytes:
    metrics = get_active()
    if metrics is None:
        return generate_latest(CollectorRegistry())
    return generate_latest(metrics.registry)


class PrometheusHttpMiddleware:
    """ASGI middleware for HTTP metrics (avoids BaseHTTPMiddleware breaking FileResponse)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        method = scope.get("method", "GET")
        path = http_path_template(Request(scope, receive))
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except ClientDisconnect:
            status_code = 499
            raise
        finally:
            observe_http(method, path, status_code, time.perf_counter() - started)


def observe_http(method: str, route: str, status_code: int, duration_sec: float) -> None:
    metrics = get_active()
    if metrics is None or not metrics.settings.METRICS_ENABLED:
        return
    if route == "/metrics":
        return
    metrics.http_requests.labels(method=method, route=route, status=str(status_code)).inc()
    metrics.http_duration.labels(method=method, route=route).observe(duration_sec)


def observe_task_transition(status: str) -> None:
    metrics = get_active()
    if metrics is None or not metrics.settings.METRICS_ENABLED:
        return
    metrics.task_transitions.labels(status=status).inc()


def observe_join_failure(reason: str) -> None:
    metrics = get_active()
    if metrics is None or not metrics.settings.METRICS_ENABLED:
        return
    metrics.join_failures.labels(reason=reason or "unknown").inc()


def observe_finalize(duration_sec: float) -> None:
    metrics = get_active()
    if metrics is None or not metrics.settings.METRICS_ENABLED:
        return
    metrics.finalize_seconds.observe(max(duration_sec, 0.0))
