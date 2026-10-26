from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import capacity_sample
from generation_contracts.capacity import WorkerCapacity
from generation_domain.values import fingerprint
from generation_domain.workflows import TASK_TYPES
from windows_worker import runtime
from windows_worker.configuration import WorkerSettings
from windows_worker.runtime import Execution, Worker

from tests.unit.test_workflow_scalar_targets import (
    overwrite_edge,
    scalar_execution,
    scalar_manifest,
)


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
@pytest.mark.parametrize("kind", ["binding", "preset"])
async def test_legacy_edge_overwrites_stop_before_journal_intent_or_comfy_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task_type: str, kind: str) -> None:
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            calls.append(self.path)
            self.rfile.read(int(self.headers.get("content-length", "0")))
            self.send_error(500)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    credential = tmp_path / "synthetic-worker.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), encoding="utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Protocol fixture"}], channels=[{"base_url": origin, "hardware_id": "GPU-SYNTHETIC", "comfy_root": tmp_path}])
    worker = Worker(settings)
    events: list[dict[str, Any]] = []
    try:
        await worker.http.aclose()

        def cloud(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/events"):
                events.append(json.loads(request.content))
            return httpx.Response(200, json={})

        worker.http = httpx.AsyncClient(base_url=settings.control_origin, transport=httpx.MockTransport(cloud))
        monkeypatch.setattr(runtime, "verify_environment", lambda *args: {})

        async def capacity() -> WorkerCapacity:
            return WorkerCapacity.model_validate(capacity_sample())

        monkeypatch.setattr(worker, "capacity", capacity)
        manifest = scalar_manifest(task_type)
        overwrite_edge(manifest, kind)
        manifest["runtime_profiles"] = [{"gpu_hardware_id": "GPU-SYNTHETIC", "execution_timeout_seconds": 60}]
        execution, paths = scalar_execution(task_type)
        claim = {"attempt_id": "att_scalar_synthetic", "job_id": "job_scalar_synthetic", "execution": execution, "workflow": manifest, "workflow_digest": fingerprint(manifest), "execution_digest": fingerprint(execution), "channel": {"base_url": origin, "gpu_id": "gpu_synthetic"}, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}
        attempt = Execution(worker, claim)

        async def inputs(_comfy: httpx.AsyncClient) -> dict[str, str]:
            return paths

        monkeypatch.setattr(attempt, "inputs", inputs)
        await attempt.run()
        assert calls == []
        assert not any(e["type"] in {"submit_intent", "submitted", "outputs_collected", "gpu_released"} for e in events)
        failed = [e for e in events if e["type"] == "execution_failed"]
        assert len(failed) == 1 and failed[0]["payload"]["execution_stop_confirmed"] is True
        row = worker.journal.get(claim["attempt_id"])
        assert row["stage"] == "closed" and row["cloud_ack"]
        assert row["association_id"] is None and row["prompt_id"] is None
        assert not worker.quarantined and not worker.journal.recovery()
    finally:
        await worker.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
