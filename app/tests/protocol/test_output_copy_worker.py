from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from generation_domain.values import fingerprint
from generation_domain.workflows import TASK_TYPES
from windows_worker.configuration import WorkerSettings
from windows_worker.runtime import Execution, Worker

from tests.unit.test_workflow_scalar_targets import scalar_execution, scalar_manifest


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
@pytest.mark.parametrize("state", ["partial", "complete", "different"])
async def test_restarted_worker_reuses_original_output_without_generation(tmp_path: Path, task_type: str, state: str) -> None:
    manifest = scalar_manifest(task_type)
    manifest["output"]["max_bytes"] = 2 * 1024 * 1024
    manifest["runtime_profiles"] = [{"gpu_hardware_id": "GPU-SYNTHETIC", "execution_timeout_seconds": 60}]
    extension = ".glb" if task_type == "image_to_3d" else ".wav" if task_type == "text_to_audio" else ".mp4" if task_type.endswith("video") else ".png"
    output = tmp_path / "comfy/output"
    output.mkdir(parents=True)
    source = output / ("original" + extension)
    payload = b"Synthetic completed original output\x00" * 33000
    source.write_bytes(payload)
    prompt = "synthetic-original-prompt"
    calls: list[tuple[str, str]] = []
    history = {prompt: {"outputs": {"2": {manifest["output"]["history_field"]: [{"filename": source.name, "type": "output", "subfolder": ""}]}}, "status": {"completed": True, "status_str": "success"}}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            calls.append(("GET", self.path))
            data = history if self.path == "/history/" + prompt else {"queue_running": [], "queue_pending": []}
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            calls.append(("POST", self.path))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_error(500)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    worker: Worker | None = None
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        credential = tmp_path / "credential.json"
        credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), encoding="utf-8")
        settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Copy recovery fixture"}], channels=[{"base_url": origin, "hardware_id": "GPU-SYNTHETIC", "comfy_root": output.parent}])
        worker = Worker(settings)
        execution, _ = scalar_execution(task_type)
        claim = {"attempt_id": "att_" + "1" * 32, "job_id": "job_synthetic", "boot_id": worker.boot, "execution": execution, "workflow": manifest, "execution_digest": fingerprint(execution), "workflow_digest": fingerprint(manifest), "channel": {"base_url": origin, "gpu_id": "gpu_synthetic"}, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}
        worker.journal.accept_claim(claim)
        association = worker.journal.intent(claim["attempt_id"], claim["workflow_digest"])
        worker.journal.begin_post(claim["attempt_id"], association)
        worker.journal.submitted(claim["attempt_id"], prompt)
        destination = settings.state_root / "outputs" / claim["attempt_id"] / ("0" + extension)
        destination.parent.mkdir(parents=True)
        previous = payload if state == "complete" else payload[:1048583] if state == "partial" else b"Synthetic different retained output"
        destination.write_bytes(previous)
        await worker.close()
        worker = Worker(settings)
        assert worker.quarantined and worker.journal.recovery()
        events: list[dict[str, Any]] = []
        uploaded: list[bytes] = []
        completed: list[dict[str, Any]] = []

        async def cloud(request: httpx.Request) -> httpx.Response:
            body = await request.aread()
            if request.url.path.endswith("/events"):
                events.append(json.loads(body))
            elif request.url.path.endswith("/upload-authorizations"):
                specs = json.loads(body)["outputs"]
                return httpx.Response(200, json={"uploads": [{"id": "upl_synthetic", "url": "/internal/v1/uploads/synthetic", "uploaded": False}], "manifest_digest": fingerprint(specs)})
            elif request.method == "PUT":
                uploaded.append(body)
            elif request.url.path.endswith("/complete"):
                completed.append(json.loads(body))
            return httpx.Response(200, json={})

        await worker.http.aclose()
        worker.http = httpx.AsyncClient(base_url=settings.control_origin, transport=httpx.MockTransport(cloud))
        claim = {**claim, "boot_id": worker.boot, "fencing_token": 2, "prompt_id": prompt, "resume_state": "running", "journal_seq": 0}
        worker.journal.rebase(claim)
        attempt = Execution(worker, claim)
        await attempt.run()
        row = worker.journal.get(claim["attempt_id"])
        assert not any(method == "POST" for method, _ in calls)
        assert source.read_bytes() == payload
        if state == "different":
            assert not events and not uploaded and not completed
            assert destination.read_bytes() == previous and not row["cloud_ack"]
            assert worker.quarantined and worker.journal.outputs(claim["attempt_id"]) is None
        else:
            assert [e["type"] for e in events] == ["outputs_collected", "gpu_released"]
            assert uploaded == [payload] and len(completed) == 1
            assert row["stage"] == "closed" and row["cloud_ack"] and not worker.journal.recovery()
            package = worker.journal.outputs(claim["attempt_id"])
            assert package["retain_until"] - package["confirmed_at"] >= 3600
            assert destination.read_bytes() == payload and attempt.released
    finally:
        if worker is not None:
            await worker.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
