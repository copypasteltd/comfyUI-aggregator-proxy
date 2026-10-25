from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from conftest import capacity_sample
from generation_contracts.capacity import WorkerCapacity
from generation_domain.errors import DomainError
from generation_domain.retries import RETRY_POLICY, confirmed_oom_evidence
from generation_domain.values import fingerprint
from windows_worker import runtime
from windows_worker.configuration import WorkerSettings
from windows_worker.journal import Journal
from windows_worker.runtime import Execution, Worker


@pytest.mark.parametrize(
    "mode,code",
    [
        ("oom", "comfy_oom"),
        ("oom_evidenced", "comfy_oom"),
        ("text_only", "comfy_execution_failed"),
        ("queue_running", None),
        ("queue_malformed", None),
        ("bad_output", "comfy_output_invalid"),
        ("unstable_output", None),
        ("media_rejected", "comfy_output_invalid"),
        ("media_transport", None),
    ],
)
async def test_actual_execution_failure_paths_keep_journal_and_never_guess_gpu_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, code: str | None
) -> None:
    prompt = str(uuid4())
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", "0")))
            calls.append(self.path)
            self.reply({"prompt_id": prompt})

        def do_GET(self) -> None:
            calls.append(self.path)
            if self.path == "/queue":
                self.reply(
                    {
                        "queue_running": [[1, prompt]]
                        if mode == "queue_running"
                        else [[1]]
                        if mode == "queue_malformed"
                        else [],
                        "queue_pending": [],
                    }
                )
            else:
                error = mode in {"oom", "oom_evidenced", "text_only", "queue_running", "queue_malformed"}
                status = {
                    "status_str": "error" if error else "success",
                    "completed": True,
                    "messages": [
                        [
                            "execution_error",
                            {
                                "prompt_id": prompt,
                                "exception_type": "RuntimeError"
                                if mode == "text_only"
                                else "torch.OutOfMemoryError",
                                "exception_message": "CUDA out of memory synthetic-private-prompt",
                                "current_inputs": {"private": "synthetic-private-prompt"},
                            },
                        ]
                    ],
                }
                self.reply({prompt: {"status": status, "outputs": {}}})

        def reply(self, data: dict[str, Any]) -> None:
            value = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    credential = tmp_path / "synthetic-worker.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}))
    settings = WorkerSettings(
        control_origin="https://control.invalid",
        credential_file=credential,
        state_root=tmp_path / "state",
        gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Protocol test"}],
        channels=[{"base_url": origin, "hardware_id": "GPU-SYNTHETIC", "comfy_root": tmp_path}],
    )
    worker = Worker(settings)
    events: list[dict[str, Any]] = []
    try:
        await worker.http.aclose()

        def cloud(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/events"):
                events.append(json.loads(request.content))
            if request.url.path.endswith("upload-authorizations"):
                return httpx.Response(
                    200,
                    json={
                        "uploads": [
                            {
                                "id": "upl_synthetic",
                                "url": "/internal/v1/uploads/synthetic",
                                "uploaded": False,
                            }
                        ],
                        "manifest_digest": "a" * 64,
                    },
                )
            if request.method == "PUT":
                return httpx.Response(
                    422 if mode == "media_rejected" else 503,
                    json={"error": {"code": "invalid_image"}},
                )
            return httpx.Response(200, json={})

        worker.http = httpx.AsyncClient(
            base_url=settings.control_origin, transport=httpx.MockTransport(cloud)
        )
        monkeypatch.setattr(runtime, "verify_environment", lambda *args: {})

        async def capacity() -> WorkerCapacity:
            return WorkerCapacity.model_validate(capacity_sample())

        monkeypatch.setattr(worker, "capacity", capacity)
        workflow = {
            "workflow_api": {
                "1": {"class_type": "SyntheticSave", "inputs": {"text": "", "filename_prefix": ""}}
            },
            "bindings": [
                {"source": "prompt", "node_id": "1", "input_name": "text"},
                {"source": "output_prefix", "node_id": "1", "input_name": "filename_prefix"},
            ],
            "presets": [{"quality": "standard", "size": "1024x1024", "values": {}}],
            "runtime_profiles": [
                {"gpu_hardware_id": "GPU-SYNTHETIC", "execution_timeout_seconds": 60}
            ],
            "output": {"final_node_ids": ["1"], "expected_count": 1, "history_field": "images"},
        }
        execution = {
            "parameters": {"quality": "standard", "size": "1024x1024"},
            "input": {"prompt": "Synthetic test"},
        }
        claim = {
            "attempt_id": "att_synthetic",
            "job_id": "job_synthetic",
            "execution": execution,
            "workflow": workflow,
            "workflow_digest": fingerprint(workflow),
            "execution_digest": fingerprint(execution),
            "channel": {"base_url": origin, "gpu_id": "gpu_synthetic"},
            "fencing_token": 1,
            "lease_expires_at": 2000,
            "server_time": 1000,
        }
        if mode == "oom_evidenced":
            claim["retry_policy"] = RETRY_POLICY
        run = Execution(worker, claim)

        async def collect(*args: Any) -> tuple[list[Path], list[dict[str, Any]]]:
            if mode == "unstable_output":
                raise DomainError("output_not_stable", "Synthetic still-changing output")
            path = settings.state_root / "outputs/att_synthetic/0.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-invalid-media")
            return [path], [
                {
                    "position": 0,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "media_type": "image/png",
                }
            ]

        if mode in {"unstable_output", "media_rejected", "media_transport"}:
            monkeypatch.setattr(run, "collect", collect)
        await run.run()
        failures = [e for e in events if e["type"] == "execution_failed"]
        row = worker.journal.get(claim["attempt_id"])
        assert calls.count("/prompt") == 1 and "/queue" in calls
        assert bool(row["stage"] == "closed" and row["cloud_ack"]) is (code is not None)
        assert [e["payload"]["error_code"] for e in failures] == ([code] if code else [])
        assert "synthetic-private-prompt" not in json.dumps(events)
        if mode == "oom_evidenced":
            assert confirmed_oom_evidence(failures[0]["payload"], prompt_id=prompt, association_id=row["association_id"])
        elif failures:
            assert "retry_evidence" not in failures[0]["payload"]
        if mode.startswith("media_"):
            assert any(e["type"] == "gpu_released" for e in events)
            assert run.released
        else:
            assert not any(e["type"] == "gpu_released" for e in events)
        if code is None:
            assert worker.quarantined
            worker.journal.close()
            worker.journal = Journal(settings.state_root)
            assert worker.journal.recovery()
        else:
            assert not worker.quarantined
    finally:
        await worker.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
