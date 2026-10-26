from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from conftest import capacity_sample
from generation_contracts.capacity import WorkerCapacity
from generation_domain.values import fingerprint
from generation_domain.workflows import TASK_TYPES
from windows_worker.runtime import Execution

from tests.protocol.test_final_output_shapes import malformed
from tests.protocol.test_output_collection_resume import collector
from tests.unit.test_workflow_scalar_targets import scalar_execution


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
@pytest.mark.parametrize("shape", ["node_list", "item_string", "healthy"])
async def test_bad_history_isolated_while_same_worker_finishes_other_task(tmp_path: Path, task_type: str, shape: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async with collector(tmp_path, task_type) as (sample, source, destination, history, payload):
        worker = sample.worker
        field = sample.claim["workflow"]["output"]["history_field"]
        histories = {"synthetic-bad": deepcopy(history) if shape == "healthy" else malformed(history, field, shape), "synthetic-good": history}
        calls: list[tuple[str, str]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                calls.append(("GET", path))
                if path == "/queue":
                    value: Any = {"queue_running": [], "queue_pending": []}
                else:
                    prompt = path.rsplit("/", 1)[-1]
                    value = {prompt: {**histories[prompt], "status": {"completed": True, "status_str": "success"}}}
                raw = json.dumps(value).encode()
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
        run: asyncio.Task[None] | None = None
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            worker.settings.channels[0].base_url = origin
            manifest = deepcopy(sample.claim["workflow"])
            manifest["runtime_profiles"] = [{"gpu_hardware_id": "GPU-SYNTHETIC", "execution_timeout_seconds": 60}]
            execution, _ = scalar_execution(task_type)
            leases = {}
            for n, prompt in [("1", "synthetic-bad"), ("2", "synthetic-good")]:
                aid = "att_" + n * 32
                claim = {"attempt_id": aid, "job_id": "job_" + n * 32, "boot_id": "00000000-0000-0000-0000-000000000001", "execution": execution, "workflow": manifest, "execution_digest": fingerprint(execution), "workflow_digest": fingerprint(manifest), "channel": {"base_url": origin, "gpu_id": "gpu_" + n * 32}, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}
                worker.journal.accept_claim(claim)
                association = worker.journal.intent(aid, claim["workflow_digest"])
                worker.journal.begin_post(aid, association)
                worker.journal.submitted(aid, prompt)
                leases[aid] = {**claim, "boot_id": worker.boot, "fencing_token": 2, "resume_state": "running", "prompt_id": prompt, "journal_seq": 0}
            events: list[dict[str, Any]] = []
            uploads: list[bytes] = []
            completed: list[str] = []
            new_claims: list[str] = []
            finished = asyncio.Event()
            cleanup_started = asyncio.Event()

            async def hold_failed_task_cleanup(attempt: Execution) -> None:
                try:
                    await asyncio.Future()
                finally:
                    if shape != "healthy" and attempt.aid == "att_" + "1" * 32:
                        cleanup_started.set()
                        await finished.wait()

            monkeypatch.setattr(Execution, "renew", hold_failed_task_cleanup)

            async def cloud(request: httpx.Request) -> httpx.Response:
                path = request.url.path
                body = await request.aread()
                data = json.loads(body) if body and request.method != "PUT" else {}
                if path.endswith("/register"):
                    return httpx.Response(200, json={"node_id": worker.node_id, "boot_id": worker.boot, "reconciliation_required": list(leases)})
                if path.endswith("/reconciliation-context"):
                    return httpx.Response(200, json={"channel": leases[path.split("/")[-2]]["channel"]})
                if path == "/internal/v1/reconciliations":
                    return httpx.Response(200, json={"status": "resumed", "lease": leases[data["attempt_id"]]})
                if path.endswith("/events"):
                    events.append(data)
                if path.endswith("/upload-authorizations"):
                    return httpx.Response(200, json={"uploads": [{"id": "upl_synthetic", "url": "/internal/v1/uploads/synthetic", "uploaded": False}], "manifest_digest": fingerprint(data["outputs"])})
                if request.method == "PUT":
                    uploads.append(body)
                if path.endswith("/complete"):
                    completed.append(path.split("/")[-2])
                    if shape != "healthy" or len(completed) == 2:
                        finished.set()
                if path == "/internal/v1/claims":
                    new_claims.append(path)
                return httpx.Response(200, json={})

            await worker.http.aclose()
            worker.http = httpx.AsyncClient(base_url=worker.settings.control_origin, transport=httpx.MockTransport(cloud))

            async def capacity() -> WorkerCapacity:
                return WorkerCapacity.model_validate(capacity_sample())

            monkeypatch.setattr(worker, "capacity", capacity)
            run = asyncio.create_task(worker.run())
            await asyncio.wait_for(finished.wait(), timeout=20)
            bad_id, good_id = "att_" + "1" * 32, "att_" + "2" * 32
            async with asyncio.timeout(10):
                while bad_id in worker.active and not run.done():
                    await asyncio.sleep(0.1)
            assert not run.done() and not worker.http.is_closed
            assert not any(method == "POST" for method, _ in calls)
            bad = worker.journal.get(bad_id)
            if shape == "healthy":
                assert not worker.quarantined and bad["cloud_ack"] and not worker.journal.recovery()
                assert set(completed) == {bad_id, good_id} and uploads == [payload, payload]
                assert len(events) == 4 and destination.read_bytes() == payload
            else:
                assert cleanup_started.is_set()
                assert worker.quarantined and not new_claims
                assert not bad["cloud_ack"] and bad["prompt_id"] == "synthetic-bad" and bad["association_id"]
                assert worker.journal.outputs(bad_id) is None and not destination.exists()
                assert not any(event["attempt_id"] == bad_id for event in events)
                assert completed == [good_id] and uploads == [payload]
                assert [event["type"] for event in events] == ["outputs_collected", "gpu_released"]
            assert worker.journal.get(good_id)["cloud_ack"]
            assert source.read_bytes() == payload
        finally:
            if run is not None:
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive() and worker._closed
