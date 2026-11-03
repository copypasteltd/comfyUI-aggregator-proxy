from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from generation_domain.errors import DomainError
from windows_worker.configuration import WorkerSettings
from windows_worker.runtime import Execution, Worker

from tests.unit.test_workflow_context_contract import context_bytes


def prepared(tmp_path: Path, manifest: dict[str, Any]) -> tuple[Worker, dict[str, Any]]:
    root = tmp_path / "comfy"
    root.mkdir()
    credential = tmp_path / "credential.json"
    credential.write_text(
        json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}),
        "utf-8",
    )
    settings = WorkerSettings(
        control_origin="https://control.invalid",
        credential_file=credential,
        state_root=tmp_path / "state",
        gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Synthetic context protocol"}],
        channels=[
            {
                "base_url": "http://127.0.0.1:8188",
                "hardware_id": "GPU-SYNTHETIC",
                "comfy_root": root,
            }
        ],
    )
    claim = {
        "attempt_id": "att_synthetic",
        "job_id": "job_synthetic",
        "workflow": manifest,
        "channel": {"base_url": "http://127.0.0.1:8188"},
        "execution": {},
        "fencing_token": 7,
        "lease_expires_at": 2000,
        "server_time": 1000,
    }
    return Worker(settings), claim


@pytest.mark.parametrize("failure", [None, "digest", "truncated", "oversized", "executable"])
@pytest.mark.asyncio
async def test_context_download_checks_fence_integrity_and_safe_format_before_upload(
    tmp_path: Path, context_manifest: dict[str, Any], failure: str | None
) -> None:
    worker, claim = prepared(tmp_path, context_manifest)
    data = context_bytes()
    frozen = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    if failure == "digest":
        frozen["sha256"] = "0" * 64
    if failure == "truncated":
        data = data[:-1]
    if failure == "oversized":
        data += b"extra"
    if failure == "executable":
        data = b"\x80\x04cos\nsystem\n."
        frozen = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    claim["execution"]["context"] = frozen
    await worker.http.aclose()
    uploads = []

    async def download(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/attempts/att_synthetic/context"
        assert (
            request.url.params["fencing_token"] == "7"
            and request.url.params["boot_id"] == worker.boot
        )
        return httpx.Response(200, content=data)

    async def upload(request: httpx.Request) -> httpx.Response:
        assert failure is None, "Invalid context must never reach ComfyUI"
        body = await request.aread()
        assert data in body and b".safetensors" in body and b'name="overwrite"\r\n\r\nfalse' in body
        uploads.append(request.url.path)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(
                json.dumps(
                    {
                        "name": "owned.safetensors",
                        "type": "input",
                        "subfolder": "render-control/att_synthetic",
                    }
                ).encode()
            ),
        )

    worker.http = httpx.AsyncClient(
        base_url="https://control.invalid", transport=httpx.MockTransport(download)
    )
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(upload)
        ) as comfy:
            if failure:
                with pytest.raises(DomainError) as error:
                    await Execution(worker, claim).context_input(comfy)
                assert error.value.code == "invalid_workflow_context"
            else:
                assert (
                    await Execution(worker, claim).context_input(comfy)
                    == "render-control/att_synthetic/owned.safetensors"
                )
                assert uploads == ["/upload/image"]
        assert not list((worker.settings.state_root / "inputs").iterdir())
    finally:
        await worker.close()


@pytest.mark.parametrize("failure", [None, "missing", "multiple", "temp", "executable"])
@pytest.mark.asyncio
async def test_context_collection_requires_unique_formal_file_and_preserves_exact_bytes(
    tmp_path: Path, context_manifest: dict[str, Any], failure: str | None
) -> None:
    worker, claim = prepared(tmp_path, context_manifest)
    local = worker.settings.channel(claim["channel"]["base_url"])
    path = local.local_root / "output" / "synthetic.safetensors"
    path.parent.mkdir()
    data = context_bytes() if failure != "executable" else b"\x80\x04cos\nsystem\n."
    path.write_bytes(data)
    entry = {
        "filename": path.name,
        "subfolder": "",
        "type": "temp" if failure == "temp" else "output",
    }
    entries = [] if failure == "missing" else [entry, entry] if failure == "multiple" else [entry]
    node = context_manifest["context"]["save_node_id"]
    try:
        async with httpx.AsyncClient() as comfy:
            operation = Execution(worker, claim).collect_context(
                {"outputs": {node: {"latents": entries}}}, local, comfy
            )
            if failure:
                with pytest.raises(DomainError) as error:
                    await operation
                assert error.value.code == "invalid_workflow_context"
            else:
                result = await operation
                assert (
                    result
                    and result["sha256"] == hashlib.sha256(data).hexdigest()
                    and result["bytes"] == len(data)
                )
                assert (worker.settings.state_root / result["file"]).read_bytes() == data
                assert path.read_bytes() == data
    finally:
        await worker.close()
