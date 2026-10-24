from __future__ import annotations

import asyncio
import gzip
import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from windows_worker import comfy_json, commands, runtime
from windows_worker.comfy import ComfyClient, SubmissionUnknown
from windows_worker.comfy_json import async_request_json, request_json, uploaded_input
from windows_worker.configuration import WorkerSettings
from windows_worker.journal import Journal
from windows_worker.observations import observe_queue
from windows_worker.runtime import Execution, Worker


class TrackedStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, *parts: bytes) -> None:
        self.parts, self.pulled, self.closed = parts, 0, False

    def __iter__(self) -> Iterator[bytes]:
        for part in self.parts:
            self.pulled += 1
            yield part

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for part in self:
            yield part

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.close()


async def read(stream: TrackedStream, headers: dict[str, str], mode: str, *, maximum: int = 4096) -> dict[str, Any]:
    def network(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Synthetic credential"
        assert request.headers["accept-encoding"] == "gzip, identity"
        return httpx.Response(200, headers=headers, stream=stream)
    transport = httpx.MockTransport(network)
    options = {"base_url": "http://127.0.0.1:8188", "headers": {"Authorization": "Synthetic credential"}, "transport": transport}
    if mode == "sync":
        with httpx.Client(**options) as client:
            return request_json(client, "GET", "/queue", maximum=maximum)
    async with httpx.AsyncClient(**options) as client:
        return await async_request_json(client, "GET", "/queue", maximum=maximum)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("encoding", ["identity", "gzip"])
async def test_decoded_and_wire_limits_stop_before_next_chunk(mode: str, encoding: str) -> None:
    content = b'{"value":"' + b"a" * (2 * 1024 * 1024) + b'"}'
    payload = gzip.compress(content) if encoding == "gzip" else content
    stream = TrackedStream(payload, b"must not be read")
    # A false small length cannot bypass streamed wire or decompression limits.
    with pytest.raises(DomainError) as failure:
        await read(stream, {"Content-Encoding": encoding, "Content-Length": "1"}, mode)
    assert failure.value.code == "response_too_large"
    assert stream.pulled == 1 and stream.closed


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_valid_gzip_split_across_header_and_trailer_keeps_credentials(mode: str) -> None:
    expected = {"value": "合成测试" * 100}
    content = json.dumps(expected, ensure_ascii=False).encode()
    payload = gzip.compress(content)
    stream = TrackedStream(*(payload[i:i + 1] for i in range(len(payload))))
    assert await read(stream, {"Content-Encoding": "gzip"}, mode, maximum=len(content)) == expected
    assert stream.closed


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("content, headers", [
    (b"[]", {}),
    (b"not json", {}),
    (b'{"value": NaN}', {}),
    (b'{"value": 1e999}', {}),
    (b'{"value":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}", {}),
    ('{"value":"test"}'.encode("utf-16"), {}),
    (gzip.compress(b"{}")[:-2], {"Content-Encoding": "gzip"}),
    (gzip.compress(b"{}") + gzip.compress(b"{}"), {"Content-Encoding": "gzip"}),
    (gzip.compress(b"{}") + b"trailing", {"Content-Encoding": "gzip"}),
    (b"{}", {"Content-Encoding": "br"}),
    (b"{}", {"Content-Length": "-1"}),
    (b"{}", {"Content-Length": "4097"}),
], ids=["not-object", "invalid-json", "nan", "float-overflow", "too-deep", "utf16", "truncated-gzip", "concatenated-gzip", "trailing-gzip", "unsupported-encoding", "negative-length", "oversized-length"])
async def test_invalid_metadata_always_closes_without_body_in_error(content: bytes, headers: dict[str, str], mode: str) -> None:
    stream = TrackedStream(content)
    with pytest.raises(DomainError) as failure:
        await read(stream, headers, mode)
    assert failure.value.code in ("invalid_comfy_response", "response_too_large")
    assert "Synthetic credential" not in str(failure.value)
    assert stream.closed


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_redirect_does_not_forward_credentials_even_if_client_follows(mode: str) -> None:
    requests: list[httpx.Request] = []
    stream = TrackedStream(b"unused redirect body")
    def network(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://outside.invalid/secret"}, stream=stream)
    options = {"base_url": "http://127.0.0.1:8188", "transport": httpx.MockTransport(network), "follow_redirects": True}
    with pytest.raises(httpx.HTTPStatusError):
        if mode == "sync":
            with httpx.Client(**options) as client:
                request_json(client, "GET", "/queue", maximum=4096)
        else:
            async with httpx.AsyncClient(**options) as client:
                await async_request_json(client, "GET", "/queue", maximum=4096)
    assert len(requests) == 1 and stream.pulled == 0 and stream.closed


def test_sync_slow_drip_obeys_total_budget_without_waiting_for_large_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(comfy_json, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    class SlowStream(TrackedStream):
        def __iter__(self) -> Iterator[bytes]:
            for part in super().__iter__():
                clock[0] += 11
                yield part
    stream = SlowStream(b" ", b" ", b" ", b"{}")
    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client, pytest.raises(httpx.ReadTimeout):
        request_json(client, "GET", "/queue", maximum=4096)
    assert stream.pulled == 3 and stream.closed


@pytest.mark.parametrize("cancel", [False, True])
async def test_async_deadline_and_cancellation_close_blocked_response(monkeypatch: pytest.MonkeyPatch, cancel: bool) -> None:
    started = asyncio.Event()
    class BlockedStream(TrackedStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"
    stream = BlockedStream()
    monkeypatch.setattr(comfy_json, "METADATA_SECONDS", 5 if cancel else 0.02)
    async with httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        task = asyncio.create_task(async_request_json(client, "GET", "/history", maximum=4096))
        await asyncio.wait_for(started.wait(), timeout=1)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else httpx.ReadTimeout):
            await asyncio.wait_for(task, timeout=1)
    assert stream.closed


@pytest.mark.parametrize("content, headers", [
    (b"x" * (comfy_json.MAX_RECEIPT_BYTES + 1), {}),
    (gzip.compress(b"x" * (comfy_json.MAX_RECEIPT_BYTES + 1)), {"Content-Encoding": "gzip"}),
    (b"[]", {}),
    (b'{"prompt_id":"../another?secret"}', {}),
], ids=["plain-overflow", "gzip-overflow", "not-object", "unsafe-prompt-id"])
def test_ambiguous_prompt_receipt_survives_restart_without_second_post(tmp_path: Path, content: bytes, headers: dict[str, str]) -> None:
    journal = Journal(tmp_path)
    journal.accept_claim({"attempt_id": "att_test", "execution_digest": "a" * 64})
    calls: list[str] = []
    stream = TrackedStream(content)
    def network(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert journal.get("att_test")["stage"] == "posting"
        return httpx.Response(200, stream=stream, headers=headers)
    client = ComfyClient("http://127.0.0.1:8188", transport=httpx.MockTransport(network))
    try:
        with pytest.raises(SubmissionUnknown):
            client.submit(journal, "att_test", {}, "b" * 64)
        original = journal.get("att_test")
        assert original and original["stage"] == "posting" and not original["cloud_ack"]
        journal.close()
        journal = Journal(tmp_path)
        assert journal.recovery()[0]["association_id"] == original["association_id"]
        with pytest.raises(DomainError):
            client.submit(journal, "att_test", {}, "b" * 64)
        assert calls == ["/prompt"] and stream.closed
    finally:
        client.close()
        journal.close()


@pytest.mark.parametrize("invalid", ["oversized", "structure"])
def test_reconciliation_failure_is_unknown_and_never_resubmittable(invalid: str) -> None:
    stream = TrackedStream(b"[]")
    headers = {"Content-Length": str(comfy_json.MAX_QUEUE_BYTES + 1)} if invalid == "oversized" else {}
    client = ComfyClient("http://127.0.0.1:8188", transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers=headers)))
    try:
        assert client.reconcile("synthetic-association", None) == {"observation": "unknown", "safe_to_resubmit": False}
        assert stream.closed
    finally:
        client.close()


async def test_queue_gzip_overflow_and_invalid_groups_do_not_become_idle() -> None:
    for content, headers, expected in [
        (gzip.compress(b"x" * (comfy_json.MAX_QUEUE_BYTES + 1)), {"Content-Encoding": "gzip"}, "response_too_large"),
        (b'{"queue_running": [], "queue_pending": "bad"}', {}, "invalid_response"),
    ]:
        stream = TrackedStream(content)
        async with httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(lambda _, stream=stream, headers=headers: httpx.Response(200, stream=stream, headers=headers))) as client:
            assert await observe_queue(client) == {"reachable": False, "error_code": expected}
        assert stream.closed


@pytest.mark.parametrize("kind, content, headers", [
    ("scan_inventory", b"unused", {"Content-Length": str(comfy_json.MAX_INVENTORY_BYTES + 1)}),
    ("scan_inventory", b'{"SyntheticNode": []}', {}),
    ("probe_channel", b'{"system": []}', {}),
    ("collect_diagnostics", b'{"queue_running": [], "queue_pending": false}', {}),
])
def test_fixed_management_commands_reject_invalid_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, content: bytes, headers: dict[str, str]) -> None:
    stream = TrackedStream(content)
    client = ComfyClient("http://127.0.0.1:8188", transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers=headers)))
    monkeypatch.setattr(commands, "ComfyClient", lambda *args: client)
    payload = {"revision": 1, "base_url": "http://127.0.0.1:8188"}
    command = {"type": kind, "payload": payload, "command_digest": fingerprint({"type": kind, "payload": payload})}
    with pytest.raises(DomainError):
        commands.CommandExecutor(tmp_path).execute(command)
    assert stream.closed and client.http.is_closed


@pytest.mark.parametrize("value", [
    {"name": "../private", "type": "input"},
    {"name": "folder/file", "type": "input"},
    {"name": "file.png", "subfolder": "../private", "type": "input"},
    {"name": "file.png", "subfolder": "a//b", "type": "input"},
    {"name": "file\u0000.png", "type": "input"},
    {"name": "file.png", "type": "output"},
])
def test_upload_receipt_cannot_name_unauthorized_paths(value: dict[str, Any]) -> None:
    with pytest.raises(DomainError):
        uploaded_input(value)
    assert uploaded_input({"name": "合成.png", "subfolder": "inputs", "type": "input"}) == "inputs/合成.png"


@pytest.mark.parametrize("failure_stage", ["prompt", "history", "capacity"])
async def test_execution_overflow_preserves_journal_and_gpu_with_native_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str) -> None:
    calls: list[str] = []
    prompt = str(uuid4())
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            calls.append(self.path)
            self.rfile.read(int(self.headers.get("content-length", "0")))
            content = b"x" * (comfy_json.MAX_RECEIPT_BYTES + 1) if failure_stage == "prompt" else json.dumps({"prompt_id": prompt}).encode()
            self.reply(content)

        def do_GET(self) -> None:
            calls.append(self.path)
            self.reply(b"x" * (comfy_json.MAX_HISTORY_BYTES + 1))

        def reply(self, content: bytes) -> None:
            payload = gzip.compress(content)
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    credential = tmp_path / "synthetic-worker.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}))
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Protocol test"}], channels=[{"base_url": origin, "hardware_id": "GPU-SYNTHETIC", "comfy_root": tmp_path}])
    worker = Worker(settings)
    events: list[str] = []
    try:
        await worker.http.aclose()
        def cloud(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/events"):
                events.append(json.loads(request.content)["type"])
            return httpx.Response(200, json={})
        worker.http = httpx.AsyncClient(base_url=settings.control_origin, transport=httpx.MockTransport(cloud))
        monkeypatch.setattr(runtime, "verify_environment", lambda *args: {})
        from conftest import capacity_sample
        from generation_contracts.capacity import WorkerCapacity
        async def measured_capacity() -> WorkerCapacity:
            return WorkerCapacity.model_validate(capacity_sample(free_disk_bytes=0) if failure_stage == "capacity" else capacity_sample())
        monkeypatch.setattr(worker, "capacity", measured_capacity)
        workflow = {"workflow_api": {"1": {"class_type": "SyntheticSave", "inputs": {"text": "", "filename_prefix": ""}}}, "bindings": [{"source": "prompt", "node_id": "1", "input_name": "text"}, {"source": "output_prefix", "node_id": "1", "input_name": "filename_prefix"}], "presets": [{"quality": "standard", "size": "1024x1024", "values": {}}], "runtime_profiles": [{"gpu_hardware_id": "GPU-SYNTHETIC", "execution_timeout_seconds": 60}]}
        execution = {"parameters": {"quality": "standard", "size": "1024x1024"}, "input": {"prompt": "Synthetic test"}}
        claim = {"attempt_id": "att_synthetic", "job_id": "job_synthetic", "execution": execution, "workflow": workflow, "workflow_digest": fingerprint(workflow), "execution_digest": fingerprint(execution), "channel": {"base_url": origin, "gpu_id": "gpu_synthetic"}, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}
        run = Execution(worker, claim)
        await run.run()
        row = worker.journal.get(claim["attempt_id"])
        if failure_stage == "capacity":
            assert row and row["stage"] == "closed" and row["cloud_ack"]
            assert calls == [] and events == ["preparing", "execution_failed"]
            assert not worker.quarantined
            return
        assert row and row["stage"] == ("posting" if failure_stage == "prompt" else "submitted") and not row["cloud_ack"]
        assert worker.quarantined and not run.released
        assert "gpu_released" not in events and "execution_failed" not in events and "cancel_confirmed" not in events
        worker.journal.close()
        worker.journal = Journal(settings.state_root)
        assert worker.journal.recovery()[0]["association_id"] == row["association_id"]
        await Execution(worker, claim).run()
        assert calls.count("/prompt") == 1
        assert "gpu_released" not in events
    finally:
        await worker.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
