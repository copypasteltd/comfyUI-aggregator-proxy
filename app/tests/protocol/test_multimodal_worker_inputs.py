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


@pytest.mark.parametrize("kind,fixture,mime,suffix", [("audio", "audio_wav", "audio/wav", ".wav"), ("video", "input_video", "video/mp4", ".mp4"), ("model", "model3d_glb", "model/gltf-binary", ".glb")])
@pytest.mark.asyncio
async def test_worker_transfers_exact_verified_bytes_using_actual_type(request: pytest.FixtureRequest, tmp_path: Path, multimodal_manifest: dict[str, Any], kind: str, fixture: str, mime: str, suffix: str) -> None:
    path: Path = request.getfixturevalue(fixture)
    data = path.read_bytes()
    chosen = next(slot for slot in multimodal_manifest["input_slots"] if slot["kind"] == kind)
    root = tmp_path / "comfy"
    root.mkdir()
    credential = tmp_path / "credential.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), "utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Synthetic input protocol"}], channels=[{"base_url": "http://127.0.0.1:8188", "hardware_id": "GPU-SYNTHETIC", "comfy_root": root}])
    worker = Worker(settings)
    await worker.http.aclose()
    aid, identifier = "att_synthetic", "asset_" + "2" * 32
    worker.http = httpx.AsyncClient(base_url="https://control.invalid", transport=httpx.MockTransport(lambda req: httpx.Response(200, content=data, headers={"Content-Type": mime})))
    posted = []

    async def upload(req: httpx.Request) -> httpx.Response:
        body = await req.aread()
        posted.append(body)
        assert req.url.path == "/upload/image" and data in body
        assert ("Content-Type: " + mime).encode() in body and suffix.encode() in body
        assert b'name="overwrite"\r\n\r\nfalse' in body
        receipt = {"name": "controlled" + suffix, "type": "input", "subfolder": "render-control/" + aid}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(receipt).encode()), headers={"Content-Type": "application/json"})

    claim = {"attempt_id": aid, "job_id": "job_synthetic", "workflow": multimodal_manifest, "channel": {"base_url": "http://127.0.0.1:8188"}, "execution": {"input": {"media": [{"slot": chosen["name"], "asset_id": identifier}]}, "asset_hashes": {identifier: hashlib.sha256(data).hexdigest()}}, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(upload)) as comfy:
            result = await Execution(worker, claim).inputs(comfy)
        assert result == {"media:" + chosen["name"]: "render-control/" + aid + "/controlled" + suffix}
        assert len(posted) == 1 and list((settings.state_root / "inputs").iterdir()) == []
    finally:
        await worker.close()


@pytest.mark.parametrize("failure", ["wrong_mime", "digest", "too_large", "http_absolute"])
@pytest.mark.asyncio
async def test_bad_inputs_never_reach_comfy_submission(tmp_path: Path, multimodal_manifest: dict[str, Any], audio_wav: Path, failure: str) -> None:
    root = tmp_path / "comfy"
    root.mkdir()
    credential = tmp_path / "credential.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), "utf-8")
    channel = {"base_url": "http://127.0.0.1:8188", "hardware_id": "GPU-SYNTHETIC", "comfy_root": root}
    if failure == "http_absolute":
        channel = {"base_url": "http://127.0.0.1:8188", "hardware_id": "GPU-SYNTHETIC", "connection_mode": "remote_http"}
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Synthetic input rejection"}], channels=[channel], allow_api_only=failure == "http_absolute")
    chosen = next(slot for slot in multimodal_manifest["input_slots"] if slot["kind"] == "audio")
    if failure == "too_large":
        chosen["max_bytes"] = 1
    if failure == "http_absolute":
        chosen["path_mode"] = "comfy_absolute"
    data = audio_wav.read_bytes()
    worker = Worker(settings)
    await worker.http.aclose()
    worker.http = httpx.AsyncClient(base_url="https://control.invalid", transport=httpx.MockTransport(lambda req: httpx.Response(200, content=data, headers={"Content-Type": "image/png" if failure == "wrong_mime" else "audio/wav"})))
    identifier = "asset_" + "2" * 32
    claim = {"attempt_id": "att_synthetic", "job_id": "job_synthetic", "workflow": multimodal_manifest, "channel": {"base_url": channel["base_url"]}, "execution": {"input": {"media": [{"slot": chosen["name"], "asset_id": identifier}]}, "asset_hashes": {identifier: "0" * 64 if failure == "digest" else hashlib.sha256(data).hexdigest()}}, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}

    async def forbidden(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Rejected media must not be uploaded or submitted")

    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(forbidden)) as comfy:
            with pytest.raises(DomainError) as error:
                await Execution(worker, claim).inputs(comfy)
        assert error.value.code == {"wrong_mime": "input_type_mismatch", "digest": "input_digest_mismatch", "too_large": "input_too_large", "http_absolute": "input_path_mode_unsupported"}[failure]
        assert not list(settings.state_root.glob("inputs/*"))
    finally:
        await worker.close()
