"""Streaming-safe HTTP metadata; paths, credentials and bodies never enter logs."""
from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.routing import RouteContext, iter_route_contexts
from generation_infrastructure.database_diagnostics import capture_database_failure
from generation_infrastructure.monitoring import ProcessMetrics
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

METHODS = frozenset(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"))


class RequestLog:
    def __init__(self, metrics: ProcessMetrics) -> None:
        self.metrics = metrics
        self.queue: queue.Queue[str] = queue.Queue(maxsize=1024)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._write, name="render-control-http-log", daemon=True)
            self._thread.start()

    def emit(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False))
        except (queue.Full, ValueError, TypeError):
            self.metrics.log_dropped.inc()

    def _write(self) -> None:
        while not self._stop.is_set() or not self.queue.empty():
            try:
                value = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                sys.stderr.write(value + "\n")
                sys.stderr.flush()
            except (OSError, ValueError):
                self.metrics.log_dropped.inc()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def operation_group(path: str, method: str) -> str:
    read = method in ("GET", "HEAD", "OPTIONS")
    if path.startswith("/internal/monitoring/v1/"):
        return "monitoring"
    if path.startswith("/health/"):
        return "health"
    for prefix, name in (("/admin/api/v1/", "admin"), ("/gateway/v1/", "gateway"), ("/v1/", "business"), ("/internal/v1/", "worker")):
        if path.startswith(prefix):
            return name + ("_read" if read else "_write")
    return "admin_static" if path == "/admin" or path.startswith("/admin/") else "other"


def rejection_class(status: int, code: object) -> str:
    if status == 401:
        return "identity"
    if status == 403:
        return "permission"
    if status == 429:
        return "capacity" if code in ("capacity_exceeded", "generation_capacity_exceeded", "unfinished_jobs_exceeded", "worker_capacity_unavailable") else "quota"
    if status in (409, 412, 428):
        return "conflict"
    if status in (400, 413, 415, 422):
        return "validation"
    return "internal" if status >= 500 else "other"


class MonitoringMiddleware:
    def __init__(self, app: ASGIApp, metrics: ProcessMetrics, log: RequestLog, routes: frozenset[str], contexts: tuple[RouteContext, ...] = ()) -> None:
        self.app, self.metrics, self.log, self.routes = app, metrics, log, routes
        self.contexts: dict[int, list[RouteContext]] = {}
        for context in contexts:
            if context.path in routes:
                self.contexts.setdefault(id(context.original_route), []).append(context)

    def route_template(self, scope: Scope, operation: str) -> str:
        original = scope.get("route")
        for context in self.contexts.get(id(original), ()):
            template = context.path
            if template is not None and template in self.routes and context.matches(scope)[0] in (Match.FULL, Match.PARTIAL):
                return template
        template = getattr(original, "path", None)
        return template if template in self.routes else "<admin_static>" if operation == "admin_static" else "<unmatched>"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        state = scope.setdefault("state", {})
        state["request_id"] = str(uuid4())
        method = scope["method"] if scope["method"] in METHODS else "OTHER"
        operation = operation_group(scope["path"], method)
        self.metrics.inflight.labels(operation).inc()
        status, request_bytes, response_bytes = 500, 0, 0
        complete, disconnected = False, False
        completion = "failed"
        header_seconds: float | None = None

        async def counted_receive() -> Message:
            nonlocal request_bytes, disconnected
            message = await receive()
            if message["type"] == "http.request":
                request_bytes += len(message.get("body", b""))
            elif message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def counted_send(message: Message) -> None:
            nonlocal status, response_bytes, complete, header_seconds
            if message["type"] == "http.response.start":
                status = message["status"]
                header_seconds = time.monotonic() - started
                self.metrics.header_time.labels(operation).observe(header_seconds)
                if not any(key.lower() == b"x-request-id" for key, _ in message.get("headers", [])):
                    message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", state["request_id"].encode("ascii"))]}
            await send(message)
            if message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                complete = not message.get("more_body", False)

        with capture_database_failure() as database:
            try:
                await self.app(scope, counted_receive, counted_send)
                completion = "completed" if complete else "disconnected" if disconnected else "failed"
            except (asyncio.CancelledError, ClientDisconnect):
                completion = "disconnected"
                raise
            except Exception:
                completion = "failed"
                raise
            finally:
                duration = time.monotonic() - started
                status_class = f"{status // 100}xx" if 100 <= status <= 599 else "other"
                self.metrics.inflight.labels(operation).dec()
                self.metrics.requests.labels(operation, method, status_class, completion).inc()
                self.metrics.response_time.labels(operation).observe(duration)
                self.metrics.request_bytes.labels(operation).inc(request_bytes)
                self.metrics.response_bytes.labels(operation).inc(response_bytes)
                if status >= 400:
                    self.metrics.rejections.labels(operation, rejection_class(status, state.get("access_error_code"))).inc()
                route = self.route_template(scope, operation)
                try:
                    request_id = str(UUID(state["request_id"]))
                except (ValueError, TypeError, KeyError):
                    request_id = str(uuid4())
                event = {"event": "http_request", "request_id": request_id, "route": route, "method": method, "status": status, "completion": completion, "headers_milliseconds": round(header_seconds * 1000, 3) if header_seconds is not None else None, "duration_milliseconds": round(duration * 1000, 3), "request_bytes": min(request_bytes, 2**63 - 1), "response_bytes": min(response_bytes, 2**63 - 1)}
                if database.value is not None:
                    event["database_failure"] = database.value
                self.log.emit(event)


class ObservedAPI(FastAPI):
    """Keep the framework's full stack, observing its final error responses too."""

    def __init__(self, *, metrics: ProcessMetrics, request_log: RequestLog, allow_any_origin: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._http_metrics, self._http_request_log = metrics, request_log
        self._allow_any_origin = allow_any_origin

    def build_middleware_stack(self) -> ASGIApp:
        contexts = tuple(iter_route_contexts(self.routes))
        routes = frozenset(context.path for context in contexts if context.path is not None)
        stack = super().build_middleware_stack()
        if self._allow_any_origin:
            stack = CORSMiddleware(stack, allow_origin_regex=".*", allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "ETag", "Retry-After", "Content-Range", "Accept-Ranges", "Content-Disposition", "Content-Length"])
        return MonitoringMiddleware(stack, self._http_metrics, self._http_request_log, routes, contexts)
