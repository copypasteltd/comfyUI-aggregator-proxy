from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from generation_domain.errors import DomainError
from generation_domain.workflows import TASK_TYPES
from windows_worker.configuration import WorkerSettings
from windows_worker.runtime import Execution, Worker

from tests.unit.test_workflow_scalar_targets import scalar_manifest


@asynccontextmanager
async def collector(tmp_path: Path, task_type: str) -> AsyncIterator[tuple[Execution, Path, Path, dict[str, Any], bytes]]:
    root = tmp_path / "comfy"
    (root / "output").mkdir(parents=True)
    suffix = ".glb" if task_type == "image_to_3d" else ".wav" if task_type == "text_to_audio" else ".mp4" if task_type.endswith("video") else ".png"
    payload = b"Synthetic original completed output\x00" * 33000
    source = root / "output" / ("original" + suffix)
    source.write_bytes(payload)
    credential = tmp_path / "credential.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), encoding="utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Output copy protocol fixture"}], channels=[{"base_url": "http://127.0.0.1:8188", "hardware_id": "GPU-SYNTHETIC", "comfy_root": root}])
    worker = Worker(settings)
    try:
        manifest = scalar_manifest(task_type)
        manifest["output"]["max_bytes"] = 2 * 1024 * 1024
        claim = {"attempt_id": "att_" + "1" * 32, "job_id": "job_synthetic", "workflow": manifest, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}
        history = {"outputs": {"2": {manifest["output"]["history_field"]: [{"filename": source.name, "subfolder": "", "type": "output"}]}}}
        destination = settings.state_root / "outputs" / claim["attempt_id"] / ("0" + suffix)
        yield Execution(worker, claim), source, destination, history, payload
    finally:
        await worker.close()


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
@pytest.mark.parametrize("state", ["fresh", "empty", "partial", "complete"])
async def test_six_capabilities_resume_only_identical_output_copies(tmp_path: Path, task_type: str, state: str) -> None:
    async with collector(tmp_path, task_type) as (execution, source, destination, history, payload):
        if state != "fresh":
            destination.parent.mkdir(parents=True)
            destination.write_bytes(payload if state == "complete" else payload[:1024 * 1024 + 7] if state == "partial" else b"")
        paths, specs = await execution.collect(history, execution.worker.settings.channels[0])
        assert paths == [destination] and destination.read_bytes() == source.read_bytes() == payload
        assert specs == [{"position": 0, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "media_type": "model/gltf-binary" if task_type == "image_to_3d" else "audio/wav" if task_type == "text_to_audio" else "video/mp4" if task_type.endswith("video") else "image/png"}]
        assert execution.worker.journal.outputs(execution.aid) is None
        assert not execution.worker.journal.pending()


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
async def test_mismatched_existing_output_is_preserved_for_reconciliation(tmp_path: Path, task_type: str) -> None:
    async with collector(tmp_path, task_type) as (execution, source, destination, history, payload):
        destination.parent.mkdir(parents=True)
        previous = b"Synthetic different unique retained output"
        destination.write_bytes(previous)
        with pytest.raises(DomainError) as failure:
            await execution.collect(history, execution.worker.settings.channels[0])
        assert failure.value.code == "local_output_mismatch"
        assert destination.read_bytes() == previous and source.read_bytes() == payload
        assert execution.worker.journal.outputs(execution.aid) is None


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
async def test_native_windows_history_folder_collects_original_output(tmp_path: Path, task_type: str) -> None:
    async with collector(tmp_path, task_type) as (execution, source, destination, history, payload):
        nested = source.parent / "render-control" / execution.aid
        nested.mkdir(parents=True)
        source.rename(nested / source.name)
        entry = history["outputs"]["2"][execution.claim["workflow"]["output"]["history_field"]][0]
        entry["subfolder"] = "render-control\\" + execution.aid
        paths, specs = await execution.collect(history, execution.worker.settings.channels[0])
        assert paths == [destination] and destination.read_bytes() == payload
        assert specs[0]["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("folder", [r"..\outside", r"C:\Windows", r"\\server\share", r"\rooted", r"safe\..\outside", r"safe\\nested"])
async def test_windows_folder_normalization_preserves_path_rejection(tmp_path: Path, folder: str) -> None:
    async with collector(tmp_path, "text_to_image") as (execution, source, destination, history, payload):
        history["outputs"]["2"]["images"][0]["subfolder"] = folder
        with pytest.raises(DomainError) as rejected:
            await execution.collect(history, execution.worker.settings.channels[0])
        assert rejected.value.code == "unsafe_path"
        assert source.read_bytes() == payload and not destination.exists()
