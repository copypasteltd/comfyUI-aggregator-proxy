from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from control_api.access import AccessEventMiddleware, normalized_ip
from generation_infrastructure.access_events import AccessRecord, AccessSubject
from starlette.requests import ClientDisconnect


class Recorder:
    def __init__(self) -> None:
        self.events: list[AccessRecord] = []

    async def record(self, event: AccessRecord) -> bool:
        self.events.append(event)
        return True


def scope() -> dict[str, Any]:
    return {"type": "http", "method": "GET", "path": "/v1/content/Synthetic-private-path/download", "query_string": b"key=Synthetic-private-query", "headers": [(b"authorization", b"Synthetic-private-header")], "client": ("127.0.0.1", 50000)}


async def test_stream_disconnect_counts_only_successful_sends_without_payload() -> None:
    recorder = Recorder()
    async def app(current: dict[str, Any], receive: Any, send: Any) -> None:
        current["state"]["access_subject"] = AccessSubject(uuid4(), uuid4(), uuid4(), "127.0.0.1")
        current["route"] = SimpleNamespace(path="/v1/content/{identifier}/download")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": True})
        await send({"type": "http.response.body", "body": b"Synthetic-private-body", "more_body": False})
    async def disconnected_send(message: dict[str, Any]) -> None:
        if message.get("body") == b"Synthetic-private-body":
            raise ClientDisconnect()
    with pytest.raises(ClientDisconnect):
        await AccessEventMiddleware(app, recorder)(scope(), None, disconnected_send)
    event = recorder.events[0]
    assert event.status_code == 200 and event.completion == "disconnected" and event.response_bytes == 2
    assert event.route == "/v1/content/{identifier}/download" and "Synthetic-private" not in str(event)


@pytest.mark.parametrize("after_headers", [False, True])
async def test_application_error_is_recorded_once_without_replacing_or_echoing_error(after_headers: bool) -> None:
    recorder = Recorder()
    async def app(current: dict[str, Any], receive: Any, send: Any) -> None:
        if after_headers:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("Synthetic-private-exception")
    async def sent(message: Any) -> None:
        pass
    with pytest.raises(RuntimeError, match="Synthetic-private-exception"):
        await AccessEventMiddleware(app, recorder)(scope(), None, sent)
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.completion == "failed" and event.status_code == (200 if after_headers else 500)
    assert event.route == "/v1/*" and event.error_code == "internal_error"
    assert "Synthetic-private" not in str(event)


async def test_cancelled_application_keeps_disconnect_metadata() -> None:
    recorder, entered = Recorder(), asyncio.Event()
    async def app(current: Any, receive: Any, send: Any) -> None:
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(AccessEventMiddleware(app, recorder)(scope(), None, None))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(recorder.events) == 1 and recorder.events[0].completion == "disconnected"
    assert recorder.events[0].status_code is None


def test_ip_metadata_has_no_scope_suffix_or_raw_input() -> None:
    assert normalized_ip("::ffff:192.0.2.1") == "192.0.2.1"
    assert normalized_ip("fe80::1%Synthetic-private-zone") == "fe80::1"
    assert normalized_ip("Synthetic-private-non-ip") is None
