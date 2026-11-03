from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.worker import Inventory
from generation_domain.errors import DomainError
from generation_domain.workflow_contexts import inspect_context
from generation_domain.workflow_inputs import INPUT_FEATURE
from generation_domain.workflows import validate_manifest
from generation_infrastructure.media import check_specification


def context_bytes(*, extra: dict[str, Any] | None = None) -> bytes:
    header = {"video": {"dtype": "BF16", "shape": [1, 4, 2, 2, 2], "data_offsets": [0, 64]}, "audio": {"dtype": "F32", "shape": [1, 2, 2, 2], "data_offsets": [64, 96]}, **(extra or {})}
    raw = json.dumps(header).encode()
    return struct.pack("<Q", len(raw)) + raw + b"\0" * 96


def test_context_is_bounded_data_without_executable_deserialization(tmp_path: Path) -> None:
    target = tmp_path / "context.safetensors"
    target.write_bytes(context_bytes())
    assert inspect_context(target)["shapes"]["video"] == [1, 4, 2, 2, 2]


@pytest.mark.parametrize("change", ["pickle", "extra", "overlap", "huge_shape", "integer", "duplicate", "truncated", "header_length"])
def test_context_rejects_unsafe_or_malformed_payloads(tmp_path: Path, change: str) -> None:
    raw = context_bytes()
    length = struct.unpack("<Q", raw[:8])[0]
    header = json.loads(raw[8:8 + length])
    if change == "extra":
        header["untrusted"] = header["video"]
    if change == "overlap":
        header["audio"]["data_offsets"] = [0, 32]
    if change == "huge_shape":
        header["video"]["shape"][2] = 2**62
    if change == "integer":
        header["video"]["dtype"] = "I64"
    encoded = json.dumps(header).encode()
    if change == "duplicate":
        encoded = encoded[:-1] + b',"video":{}}'
    raw = struct.pack("<Q", len(encoded)) + encoded + raw[8 + length:]
    raw = {"pickle": b"\x80\x04cos\nsystem\n.", "truncated": raw[:-1], "header_length": struct.pack("<Q", 2**62) + b"{}"}.get(change, raw)
    target = tmp_path / "bad.ctx"
    target.write_bytes(raw)
    with pytest.raises(DomainError) as error:
        inspect_context(target)
    assert error.value.code == "invalid_workflow_context"


def test_registered_worker_can_advertise_typed_input_support() -> None:
    inventory = Inventory(gpus=[], features=["audio-output-v1", "model3d-output-v1", "remote-http-v1", INPUT_FEATURE])
    assert INPUT_FEATURE in inventory.features


@pytest.mark.parametrize("mode", ["generated", "source_video", "reference_audio", "silent"])
def test_sound_policy_is_enforced_on_actual_final_track(mode: str) -> None:
    metadata = {"media_type": "video/mp4", "width": 320, "height": 240, "duration_milliseconds": 500, "has_audio": mode != "silent"}
    parameters = {"size": "320x240", "seconds": 0.5}
    check_specification(metadata, parameters, video_audio_policy={"mode": mode})
    with pytest.raises(DomainError) as error:
        check_specification({**metadata, "has_audio": not metadata["has_audio"]}, parameters, video_audio_policy={"mode": mode})
    assert error.value.code == "output_spec_mismatch"


def test_sound_policy_requires_owned_required_matching_kind(multimodal_manifest: dict[str, Any]) -> None:
    output = multimodal_manifest["output"]
    output["audio_policy"] = {"mode": "source_video", "source_slot": "source_video", "description": "Keep the original video audio."}
    validate_manifest(multimodal_manifest, "references_to_video")
    output["audio_policy"]["source_slot"] = "voice"
    with pytest.raises(DomainError) as error:
        validate_manifest(multimodal_manifest, "references_to_video")
    assert error.value.code == "invalid_audio_policy"
