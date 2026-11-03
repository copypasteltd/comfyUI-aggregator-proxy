from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.validation import JobCreate
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.workflow_inputs import INPUT_FEATURE, input_assets, supports_inputs
from generation_domain.workflows import compile_graph, validate_manifest, validate_reference_inputs
from generation_infrastructure.input_media import validate_input
from generation_infrastructure.jobs import check_input_contract
from generation_infrastructure.model_usage import model_usage
from jsonschema import Draft202012Validator


def request(manifest: dict[str, Any]) -> dict[str, Any]:
    return {"model": "synthetic-all-inputs", "task_type": manifest["task_type"], "input": {"prompt": "Synthetic motion", "media": [{"slot": slot["name"], "asset_id": "asset_" + str(i + 1) * 32} for i, slot in enumerate(manifest["input_slots"])], "texts": {"script": "Speaker A: A synthetic example.\nSpeaker B: Another example."}}, "parameters": {"size": "320x240", "quality": "standard", "seconds": 0.5, "seed": 9, "n": 1}}


def test_all_input_kinds_and_script_compile_without_changing_frozen_graph(multimodal_manifest: dict[str, Any]) -> None:
    manifest = multimodal_manifest
    original = deepcopy(manifest)
    WorkflowManifest.model_validate(manifest)
    validate_manifest(manifest, manifest["task_type"])
    value = request(manifest)
    JobCreate.model_validate(value)
    prepared = {item.source: f"controlled/{i}.bin" for i, item in enumerate(input_assets(value["input"], manifest))}
    graph = compile_graph(manifest, value, prepared, "render-control/synthetic/result")
    assert graph["1"]["inputs"]["image"] == "controlled/0.bin"
    assert graph["2"]["inputs"]["video"] == "controlled/1.bin"
    assert graph["3"]["inputs"]["audio"] == "controlled/2.bin"
    assert graph["4"]["inputs"]["file_path"] == "controlled/3.bin"
    assert graph["5"]["inputs"]["script"] == value["input"]["texts"]["script"]
    assert graph["5"]["inputs"]["seed"] == 9 and manifest == original


@pytest.mark.parametrize("variant", ["duplicate", "missing", "unknown", "unknown_text", "long_text", "empty_text", "missing_prompt"])
def test_named_inputs_fail_closed(multimodal_manifest: dict[str, Any], variant: str) -> None:
    value = request(multimodal_manifest)["input"]
    if variant == "duplicate":
        value["media"].append(value["media"][0])
    elif variant == "missing":
        value["media"].pop()
    elif variant == "unknown":
        value["media"][0]["slot"] = "private_path"
    elif variant == "unknown_text":
        value["texts"]["shell"] = "unrecognized data"
    elif variant == "long_text":
        value["texts"]["script"] = "a" * 8001
    elif variant == "empty_text":
        value["texts"]["script"] = "  "
    else:
        value.pop("prompt")
    with pytest.raises(DomainError):
        validate_reference_inputs(multimodal_manifest, value)


@pytest.mark.parametrize("variant", ["wrong_kind", "too_large", "unbound", "linked_target", "duplicate_name", "legacy"])
def test_manifest_rejects_uncontrolled_or_unused_slots(multimodal_manifest: dict[str, Any], variant: str) -> None:
    manifest = multimodal_manifest
    if variant == "wrong_kind":
        manifest["input_slots"][0]["mime_types"] = ["application/x-pickle"]
    elif variant == "too_large":
        manifest["input_slots"][0]["max_bytes"] = 512 * 1024**2
    elif variant == "unbound":
        manifest["bindings"] = [b for b in manifest["bindings"] if b["source"] != "media:voice"]
    elif variant == "linked_target":
        manifest["bindings"][0].update(node_id="5", input_name="image")
    elif variant == "duplicate_name":
        manifest["input_slots"].append(manifest["input_slots"][0])
    else:
        manifest["format_version"] = "1.0"
    with pytest.raises(DomainError):
        validate_manifest(manifest, manifest["task_type"])


def test_missing_prepared_media_cannot_reuse_a_frozen_private_file(multimodal_manifest: dict[str, Any]) -> None:
    with pytest.raises(DomainError) as error:
        compile_graph(multimodal_manifest, request(multimodal_manifest), {}, "render-control/test/result")
    assert error.value.code == "missing_input_slot"


def test_output_basename_is_stable_per_attempt_and_has_no_directory(multimodal_manifest: dict[str, Any]) -> None:
    manifest = deepcopy(multimodal_manifest)
    binding = next(value for value in manifest["bindings"] if value["source"] == "output_prefix")
    binding["source"] = "output_basename"
    validate_manifest(manifest, manifest["task_type"])
    prepared = {"media:" + slot["name"]: "render-control/synthetic/input.bin" for slot in manifest["input_slots"]}
    first = compile_graph(manifest, request(manifest), prepared, "render-control/attempt-one/result")
    again = compile_graph(manifest, request(manifest), prepared, "render-control/attempt-one/result")
    second = compile_graph(manifest, request(manifest), prepared, "render-control/attempt-two/result")
    value = first[binding["node_id"]]["inputs"][binding["input_name"]]
    assert first == again and value != second[binding["node_id"]]["inputs"][binding["input_name"]]
    assert value.startswith("rc-") and "/" not in value and "\\" not in value


def test_old_workflow_cannot_silently_ignore_new_inputs(multimodal_manifest: dict[str, Any]) -> None:
    value = request(multimodal_manifest)
    with pytest.raises(DomainError) as error:
        validate_reference_inputs({"format_version": "1.0", "bindings": []}, value["input"])
    assert error.value.code == "input_not_supported"
    assert not supports_inputs(multimodal_manifest, [])
    assert supports_inputs(multimodal_manifest, [INPUT_FEATURE])
    assert supports_inputs({"format_version": "1.0"}, [])


def test_usage_exposes_typed_roles_and_valid_examples_without_internal_paths(multimodal_manifest: dict[str, Any]) -> None:
    defaults = {"size": "320x240", "quality": "standard", "seconds": 0.5, "n": 1}
    usage = model_usage("h3-reference-mixed", multimodal_manifest["task_type"], multimodal_manifest, defaults, {"display_name": "H3 mixed inputs", "description": "Synthetic source video, voice and mesh controls"})
    assert usage["schema_version"] == "1.1" and len(usage["input_slots"]) == 4
    assert "path_mode" not in str(usage) and "workflow_api" not in str(usage)
    JobCreate.model_validate(usage["example_request"])
    check_input_contract(multimodal_manifest, usage["example_request"])
    validate = Draft202012Validator(usage["request_schema"])
    validate.validate(usage["example_request"])
    invalid = deepcopy(usage["example_request"])
    invalid["input"]["media"].pop()
    assert list(validate.iter_errors(invalid))
    invalid = deepcopy(usage["example_request"])
    invalid["input"]["media"][1] = invalid["input"]["media"][0]
    assert list(validate.iter_errors(invalid))


@pytest.mark.parametrize("kind,fixture,mime", [("audio", "audio_wav", "audio/wav"), ("video", "input_video", "video/mp4"), ("model", "model3d_glb", "model/gltf-binary")])
def test_real_media_are_fully_validated_and_source_rewound(request: pytest.FixtureRequest, kind: str, fixture: str, mime: str) -> None:
    path: Path = request.getfixturevalue(fixture)
    with path.open("rb") as source:
        metadata = validate_input(source, kind)
        assert metadata["media_type"] == mime and source.tell() == 0
    if kind == "video":
        assert metadata["has_audio"] and metadata["width"] == 320 and 450 <= metadata["duration_milliseconds"] <= 650


@pytest.mark.parametrize("kind", ["image", "audio", "video", "model", "pickle"])
def test_fake_media_and_unsafe_kinds_are_rejected(kind: str) -> None:
    with pytest.raises(DomainError):
        validate_input(BytesIO(b"Not a media file; local paths and URLs are not inputs"), kind)
