"""Prometheus ``/metrics`` payload and HTTP request instrumentation.

The scrape is an ops surface, same class as ``POST /v1/ops/backup``: the
route is wired with ``require_master_key`` in ``main``. This module builds
the text and records request count, latency, and in-flight gauges on the
shared registry so scrapes reflect live traffic.
"""

from __future__ import annotations

import os
import time
from typing import Awaitable, Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST
_METRICS_PATH = "/metrics"
_SERVICE = "cerebrumdev-factory"

_registry = CollectorRegistry()
_collectors_ready = False

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the factory API",
    ["method", "handler", "status"],
    registry=_registry,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "handler"],
    registry=_registry,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "HTTP requests currently being handled",
    registry=_registry,
)
CEREBRUMDEV_UP = Gauge(
    "cerebrumdev_up",
    "CerebrumDev.ai factory process is up",
    registry=_registry,
)
BUILD_INFO = Info(
    "cerebrumdev_build",
    "CerebrumDev.ai build metadata (no secrets)",
    registry=_registry,
)


def _git_sha_from_env() -> str:
    for key in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION"):
        val = os.getenv(key, "").strip()
        if val:
            return val
    return ""


def _ensure_collectors() -> None:
    global _collectors_ready
    if _collectors_ready:
        return
    ProcessCollector(registry=_registry)
    PlatformCollector(registry=_registry)
    CEREBRUMDEV_UP.set(1)
    _collectors_ready = True


def _refresh_build_info() -> None:
    sha = _git_sha_from_env()
    BUILD_INFO.info(
        {
            "service": _SERVICE,
            "git_sha": sha or "unknown",
            "git_sha_short": sha[:7] if sha else "unknown",
        }
    )


def _handler_label(request: Request) -> str:
    route = request.scope.get("route")
    if route is not None:
        path = getattr(route, "path", "") or ""
        if path:
            return path
    # Unmatched paths: bucket by first segment only — never echo full URLs or IDs.
    parts = request.url.path.strip("/").split("/")
    if not parts or not parts[0]:
        return "/"
    if len(parts) == 1:
        return f"/{parts[0]}"
    return f"/{parts[0]}/{parts[1]}"


def _track_request(method: str, handler: str, status: str, elapsed: float) -> None:
    HTTP_REQUESTS_TOTAL.labels(method=method, handler=handler, status=status).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method, handler=handler).observe(elapsed)


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    """Record request count, latency, and in-flight gauges for every route."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == _METRICS_PATH:
            return await call_next(request)
        method = request.method
        HTTP_REQUESTS_IN_FLIGHT.inc()
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - start
            _track_request(method, _handler_label(request), "500", elapsed)
            raise
        else:
            elapsed = time.perf_counter() - start
            _track_request(
                method,
                _handler_label(request),
                str(response.status_code),
                elapsed,
            )
            return response
        finally:
            HTTP_REQUESTS_IN_FLIGHT.dec()


def render_metrics() -> bytes:
    """Return the latest Prometheus text exposition for the shared registry."""
    _ensure_collectors()
    _refresh_build_info()
    return generate_latest(_registry)


def metrics_response() -> Response:
    return Response(content=render_metrics(), media_type=METRICS_CONTENT_TYPE)
