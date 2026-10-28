from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from generation_domain.errors import DomainError
from windows_worker.configuration import WorkerSettings
from windows_worker.runtime import Execution, Worker


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["valid", "temp", "traversal", "multiple", "wrong_mime", "preview_only"])
async def test_model3d_collects_declared_final_glb_not_previews(tmp_path: Path, model3d_manifest: dict[str, Any], model3d_glb: Path, variant: str) -> None:
    root = tmp_path / "comfy"
    (root / "output").mkdir(parents=True)
    (root / "temp").mkdir()
    shutil.copyfile(model3d_glb, root / "output/mesh.glb")
    credential = tmp_path / "credential.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), encoding="utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Synthetic mesh protocol"}], channels=[{"base_url": "http://127.0.0.1:8188", "hardware_id": "GPU-SYNTHETIC", "comfy_root": root}])
    worker = Worker(settings)
    try:
        item = {"filename": "mesh.glb", "subfolder": "", "type": "output"}
        expected = None
        if variant == "temp":
            item["type"] = "temp"
            expected = "invalid_output_path"
        elif variant == "traversal":
            item["filename"] = "../mesh.glb"
            expected = "unsafe_path"
        elif variant == "multiple":
            expected = "output_count_mismatch"
        elif variant == "wrong_mime":
            item["filename"] = "mesh.obj"
            shutil.copyfile(model3d_glb, root / "output/mesh.obj")
            expected = "output_media_mismatch"
        elif variant == "preview_only":
            expected = "output_count_mismatch"
        final = model3d_manifest["output"]["final_node_ids"][0]
        history = {"outputs": {final: {"3d": [] if variant == "preview_only" else [item, item] if variant == "multiple" else [item], "images": [{"filename": "preview.png", "type": "output"}]}, "other": {"3d": [{"filename": "private.glb", "type": "output"}]}}}
        execution = Execution(worker, {"attempt_id": "att_synthetic", "job_id": "job_synthetic", "workflow": model3d_manifest, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000})
        if expected:
            with pytest.raises(DomainError) as failure:
                await execution.collect(history, settings.channels[0])
            assert failure.value.code == expected
        else:
            paths, specs = await execution.collect(history, settings.channels[0])
            assert paths[0].read_bytes() == model3d_glb.read_bytes()
            assert specs == [{"position": 0, "bytes": model3d_glb.stat().st_size, "sha256": hashlib.sha256(model3d_glb.read_bytes()).hexdigest(), "media_type": "model/gltf-binary"}]
    finally:
        await worker.close()
