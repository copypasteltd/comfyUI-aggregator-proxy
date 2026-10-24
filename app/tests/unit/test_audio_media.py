from __future__ import annotations

import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.defaults import default_key_policy
from generation_contracts.validation import JobCreate, KeyPolicy
from generation_contracts.worker import Register
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.media_types import AUDIO_MIME_TYPES, output_mime
from generation_domain.policies import enforce_limit
from generation_domain.workflows import audio_specification, compile_graph, validate_manifest
from generation_infrastructure.media import check_specification, inspect_media
from pydantic import ValidationError


def test_audio_compile_has_no_image_dimensions_and_keeps_release_immutable(audio_manifest: dict[str, Any]) -> None:
    WorkflowManifest.model_validate(audio_manifest)
    validate_manifest(audio_manifest, "text_to_audio")
    original = deepcopy(audio_manifest)
    execution = {"input": {"prompt": "synthetic audio"}, "parameters": {"quality": "standard", "seconds": 2, "seed": 12}}
    graph = compile_graph(audio_manifest, execution, {}, "results/audio")
    assert graph["1"]["inputs"] == {"text": "synthetic audio", "seed": 12}
    assert graph["2"]["inputs"]["filename_prefix"] == "results/audio"
    assert audio_specification(audio_manifest, execution["parameters"]) == {"audio_format": "wav", "sample_rate": 24000, "channels": 1}
    assert audio_manifest == original


@pytest.mark.parametrize("field,value", [("format_version", "1.0"), ("output.history_field", "images"), ("output.adapter", "save-image-v1"), ("output.max_bytes", 268435457), ("presets.seconds", 600.001), ("presets.sample_rate", 0), ("presets.channels", True), ("presets.audio_format", "mp3"), ("presets.size", "512x512")])
def test_audio_release_rejects_unsupported_claims(audio_manifest: dict[str, Any], field: str, value: Any) -> None:
    parts = field.split(".")
    target = audio_manifest[parts[0]][0] if parts[0] == "presets" else audio_manifest[parts[0]] if len(parts) == 2 else audio_manifest
    target[parts[-1]] = value
    with pytest.raises(DomainError):
        validate_manifest(audio_manifest, "text_to_audio")


def test_old_policy_and_worker_do_not_implicitly_support_audio() -> None:
    policy = default_key_policy()
    policy["limits"].pop("daily_audio_milliseconds")
    policy["spec_limits"].pop("audio_max_milliseconds")
    parsed = KeyPolicy.model_validate(policy)
    assert "audio:create" not in parsed.scopes
    assert parsed.limits.daily_audio_milliseconds.value == parsed.spec_limits.audio_max_milliseconds == 0
    with pytest.raises(DomainError, match="daily_audio_milliseconds"):
        enforce_limit(policy["limits"], "daily_audio_milliseconds", 0, 1)
    request = Register(protocol_version="0.4.0", boot_id="00000000-0000-0000-0000-000000000001", inventory={"gpus": []})
    assert request.inventory.features == []


@pytest.mark.parametrize("parameter", [{"size": "512x512"}, {"fps": 24}, {"seconds": 600.001}])
def test_audio_request_rejects_image_parameters_or_excess_duration(parameter: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobCreate(model="synthetic-audio", task_type="text_to_audio", input={"prompt": "sample"}, parameters=parameter)


@pytest.mark.parametrize("format_name,codec", [("wav", "pcm_s16le"), ("flac", "flac"), ("mp3", "libmp3lame"), ("opus", "libopus")])
def test_audio_files_are_probed_and_fully_decoded(audio_wav: Path, tmp_path: Path, format_name: str, codec: str) -> None:
    decoder = shutil.which("ffmpeg")
    assert decoder
    target = tmp_path / ("encoded." + format_name)
    subprocess.run([decoder, "-nostdin", "-v", "error", "-i", str(audio_wav), "-c:a", codec, str(target)], check=True, timeout=20)  # noqa: S603 -- synthetic local media only
    metadata = inspect_media(target, AUDIO_MIME_TYPES[format_name])
    assert metadata["codec"] == {"wav": "pcm_s16le", "flac": "flac", "mp3": "mp3", "opus": "opus"}[format_name]
    assert 490 <= metadata["duration_milliseconds"] <= 530
    expected = {"audio_format": format_name, "sample_rate": 48000 if format_name == "opus" else 24000, "channels": 1}
    check_specification(metadata, {"seconds": 2}, audio_spec=expected)
    assert output_mime(target.suffix) == AUDIO_MIME_TYPES[format_name]
    with pytest.raises(DomainError, match="冻结"):
        check_specification(metadata, {"seconds": 0.1}, audio_spec=expected)


def test_audio_corrupt_truncated_and_mislabeled_files_fail(audio_wav: Path) -> None:
    with pytest.raises(DomainError, match="音频"):
        inspect_media(audio_wav, "audio/flac")
    original = audio_wav.read_bytes()
    audio_wav.write_bytes(original[:len(original) // 2])
    with pytest.raises(DomainError, match="音频"):
        inspect_media(audio_wav, "audio/wav")
    audio_wav.write_bytes(b"RIFF" + bytes(40))
    with pytest.raises(DomainError, match="音频"):
        inspect_media(audio_wav, "audio/wav")
