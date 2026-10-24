"""Observe bounded HTTP metadata without buffering payloads or trusting client IDs."""
from __future__ import annotations

import asyncio
import re
import time
from ipaddress import IPv6Address, ip_address
from uuid import UUID, uuid4

from generation_application.ports import GatewayIdentity
from generation_contracts.access_events import AccessCompletion
from generation_domain.values import utcnow
from generation_infrastructure.access_events import (
    AccessEventWriter,
    AccessRecord,
    AccessSubject,
)
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

METHODS = frozenset(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"))


def normalized_ip(value: str) -> str | None:
    try:
        address = ip_address(value)
        return str(address.ipv4_mapped or IPv6Address(address.packed)) if isinstance(address, IPv6Address) else str(address)
    except ValueError:
        return None


class AccessEventMiddleware:
    def __init__(self, app: ASGIApp, writer: AccessEventWriter) -> None:
        self.app, self.writer = app, writer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(("/v1/", "/gateway/v1/")):
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["request_id"] = request_id = str(uuid4())
        started_at, started = utcnow(), time.monotonic()
        request_bytes, response_bytes = 0, 0
        status: int | None = None
        complete, disconnected = False, False
        completion: AccessCompletion | None = None

        async def counted_receive() -> Message:
            nonlocal request_bytes, disconnected
            message = await receive()
            if message["type"] == "http.request":
                request_bytes += len(message.get("body", b""))
            elif message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def counted_send(message: Message) -> None:
            nonlocal status, response_bytes, complete
            if message["type"] == "http.response.start":
                status = message["status"]
                # Early errors also carry the same server-generated correlation ID.
                headers = list(message.get("headers", []))
                if not any(key.lower() == b"x-request-id" for key, _ in headers):
                    message = {**message, "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))]}
            await send(message)
            if message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                complete = not message.get("more_body", False)

        try:
            await self.app(scope, counted_receive, counted_send)
        except (asyncio.CancelledError, ClientDisconnect):
            completion = "disconnected"
            raise
        except Exception:
            completion = "failed"
            if status is None:
                status = 500
            raise
        finally:
            completion = completion or ("completed" if complete else "disconnected" if disconnected else "failed")
            subject = state.get("access_subject")
            if not isinstance(subject, AccessSubject):
                subject = None
            service = state.get("access_gateway")
            gateway_id = subject.gateway_id if subject else service.gateway_id if isinstance(service, GatewayIdentity) else None
            entry = "gateway" if scope["path"].startswith("/gateway/v1/") else "direct"
            template = getattr(scope.get("route"), "path", None)
            if not isinstance(template, str) or not 1 <= len(template) <= 256 or not template.startswith(("/v1/", "/gateway/v1/")):
                template = "/gateway/v1/*" if entry == "gateway" else "/v1/*"
            code = state.get("access_error_code")
            if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
                code = None
            if code is None and completion != "completed":
                code = "client_disconnected" if completion == "disconnected" else "internal_error"
            if code is None and status is not None and status >= 400:
                code = "http_" + str(status)
            peer = scope.get("client")
            event = AccessRecord(request_id=UUID(request_id), started_at=started_at, tenant_id=subject.tenant_id if subject else None, project_id=subject.project_id if subject else None, key_id=subject.key_id if subject else None, gateway_id=gateway_id, entry=entry, method=scope["method"] if scope["method"] in METHODS else "OTHER", route=template, peer_ip=normalized_ip(peer[0]) if peer else None, client_ip=subject.client_ip if subject else None, status_code=status, error_code=code, completion=completion, duration_milliseconds=max(0, min(86400000, int((time.monotonic() - started) * 1000))), request_bytes=min(2**63 - 1, request_bytes), response_bytes=min(2**63 - 1, response_bytes))
            await self.writer.record(event)
