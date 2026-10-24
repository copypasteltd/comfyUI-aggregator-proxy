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
@pytest.mark.parametrize("variant", ["valid", "temp", "traversal", "multiple", "wrong_mime"])
async def test_audio_collection_uses_final_output_and_canonical_mime(tmp_path: Path, audio_manifest: dict[str, Any], audio_wav: Path, variant: str) -> None:
    root = tmp_path / "comfy"
    (root / "output").mkdir(parents=True)
    (root / "temp").mkdir()
    target = root / "output/actual.wav"
    shutil.copyfile(audio_wav, target)
    credential = tmp_path / "credential.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), encoding="utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "GPU-SYNTHETIC", "name": "Synthetic audio protocol"}], channels=[{"base_url": "http://127.0.0.1:8188", "hardware_id": "GPU-SYNTHETIC", "comfy_root": root}])
    worker = Worker(settings)
    try:
        claim = {"attempt_id": "att_synthetic", "job_id": "job_synthetic", "workflow": audio_manifest, "fencing_token": 1, "lease_expires_at": 2000, "server_time": 1000}
        item: dict[str, str] = {"filename": "actual.wav", "subfolder": "", "type": "output"}
        expected = None
        if variant == "temp":
            item["type"] = "temp"
            expected = "invalid_output_path"
        elif variant == "traversal":
            item["filename"] = "../actual.wav"
            expected = "unsafe_path"
        elif variant == "multiple":
            expected = "output_count_mismatch"
        elif variant == "wrong_mime":
            item["filename"] = "actual.mp4"
            shutil.copyfile(audio_wav, root / "output/actual.mp4")
            expected = "output_media_mismatch"
        history = {"outputs": {"2": {"audio": [item, item] if variant == "multiple" else [item]}, "unrelated": {"audio": [{"filename": "private.wav", "type": "output"}]}}}
        execution = Execution(worker, claim)
        if expected:
            with pytest.raises(DomainError) as failure:
                await execution.collect(history, settings.channels[0])
            assert failure.value.code == expected
        else:
            paths, specs = await execution.collect(history, settings.channels[0])
            assert paths[0].read_bytes() == audio_wav.read_bytes()
            assert specs == [{"position": 0, "bytes": audio_wav.stat().st_size, "sha256": hashlib.sha256(audio_wav.read_bytes()).hexdigest(), "media_type": "audio/wav"}]
    finally:
        await worker.close()
