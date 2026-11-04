from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from generation_contracts.recovery import HOST_RESTART_PROTOCOL, Reconciliation
from generation_domain.values import fingerprint
from windows_worker import host_recovery
from windows_worker.runtime import Worker

from tests.protocol.test_worker_contexts import prepared

pytestmark = pytest.mark.protocol


@pytest.fixture
def loopback(history: str) -> Iterator[tuple[str, list[tuple[str, str]]]]:
    requests: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append((self.command, self.path))
            if self.path == "/queue":
                result: Any = {"queue_running": [], "queue_pending": []}
            else:
                if history == "unavailable":
                    self.send_error(503)
                    return
                result = {} if history == "absent" else []
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            requests.append((self.command, self.path))
            self.send_error(500)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not any(method == "POST" for method, _ in requests)


@pytest.mark.parametrize("history", ["absent", "invalid", "unavailable"])
async def test_original_checkpoint_survives_worker_restart_and_requires_successful_absence(
    tmp_path: Path, context_manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch, history: str,
    loopback: tuple[str, list[tuple[str, str]]],
) -> None:
    origin, _ = loopback
    worker, claim = prepared(tmp_path, context_manifest)
    worker.settings.channels[0].base_url = origin
    claim.update(boot_id=str(uuid4()), channel={"base_url": origin}, execution_digest=fingerprint(claim["execution"]))
    association = ""
    settings = worker.settings
    tick = [900000]
    monkeypatch.setattr(host_recovery, "windows_host", lambda: ("a" * 64, tick[0]))
    bodies: list[dict[str, Any]] = []

    async def cloud(request: httpx.Request) -> httpx.Response:
        data = json.loads(await request.aread())
        if request.url.path.endswith("/reconciliation-context"):
            return httpx.Response(200, json={"channel": claim["channel"]})
        assert request.url.path == "/internal/v1/reconciliations"
        Reconciliation.model_validate(data)
        bodies.append(data)
        assert data["previous_boot_id"] == claim["boot_id"] and data["previous_fencing_token"] == 7
        assert data["association_id"] == association and data["prompt_id"] is None
        if data["observation"] == "host_restarted":
            proof = data["host_restart"]
            assert proof["checkpoint_digest"] == "d" * 64 and tick[0] == 1000
            assert data["evidence_digest"] == fingerprint({"observation": "host_restarted", "source": "native_host_uptime", "host_restart": proof, "prompt_id": None})
            return httpx.Response(200, json={"status": "closed", "job_status": "failed"})
        result = {"status": "attention_required", "safe_to_resubmit": False}
        if data.get("host_checkpoint"):
            result["host_checkpoint_digest"] = "d" * 64
        return httpx.Response(200, json=result)

    async def bind(value: Worker, supported: bool) -> None:
        await value.http.aclose()
        value.http = httpx.AsyncClient(base_url=settings.control_origin, transport=httpx.MockTransport(cloud))
        value.host_recovery_supported = supported

    try:
        worker.journal.accept_claim(claim)
        association = worker.journal.intent(claim["attempt_id"], "c" * 64)
        worker.journal.begin_post(claim["attempt_id"], association)
        # An old server does not receive new fields or arm a host checkpoint.
        await bind(worker, False)
        await worker.recover()
        assert "host_checkpoint" not in bodies[-1] and "host_restart" not in bodies[-1]
        assert worker.journal.host_checkpoint(claim["attempt_id"]) is None
        await bind(worker, True)
        await worker.recover()
        if history != "absent":
            assert bodies[-1]["observation"] == "unknown" and "host_checkpoint" not in bodies[-1]
            assert worker.journal.host_checkpoint(claim["attempt_id"]) is None
            assert worker.quarantined
            return
        baseline = bodies[-1]["host_checkpoint"]
        assert worker.journal.host_checkpoint(claim["attempt_id"])["server_digest"] == "d" * 64
        original_boot = worker.boot
        await worker.close()
        worker = Worker(settings)
        assert worker.boot != original_boot
        tick[0] = 910000
        await bind(worker, True)
        await worker.recover()
        assert bodies[-1]["host_checkpoint"] == baseline and worker.quarantined
        assert not worker.journal.get(claim["attempt_id"])["cloud_ack"]
        # Explicit synthetic native-uptime reset; this is not a hardware reboot.
        tick[0] = 1000
        await worker.recover()
        assert bodies[-1]["observation"] == "host_restarted"
        assert worker.journal.get(claim["attempt_id"])["cloud_ack"] and not worker.quarantined
    finally:
        await worker.close()


@pytest.mark.parametrize("protocols,expected", [(None, False), ([], False), (["future-unknown"], False), (HOST_RESTART_PROTOCOL, False), ([HOST_RESTART_PROTOCOL], True)])
async def test_registration_explicitly_negotiates_host_protocol(
    tmp_path: Path, context_manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch, protocols: Any, expected: bool,
) -> None:
    worker, _ = prepared(tmp_path, context_manifest)
    inspected = asyncio.Event()

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert path == "/internal/v1/register"
        return {"node_id": worker.node_id, "boot_id": worker.boot, "reconciliation_required": ["att_unknown"], "recovery_protocols": protocols}

    async def background() -> None:
        await asyncio.Future()

    async def recover() -> None:
        assert worker.host_recovery_supported is expected
        inspected.set()
        await asyncio.Future()

    monkeypatch.setattr(worker, "post", post)
    monkeypatch.setattr(worker, "recover", recover)
    for name in ("heartbeat", "management", "observe"):
        monkeypatch.setattr(worker, name, background)
    running = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(inspected.wait(), timeout=5)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        assert worker._closed
