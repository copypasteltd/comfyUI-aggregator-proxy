from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from generation_contracts.model_usage import ModelDocumentation
from generation_contracts.validation import JobCreate
from generation_domain.workflows import validate_reference_inputs
from generation_infrastructure.model_usage import model_usage
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonValidationError
from pydantic import ValidationError


def usage_manifest(task: str = "text_to_image", *, count: int = 2, weighted: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    video, audio = task.endswith("video"), task == "text_to_audio"
    default: dict[str, Any] = {"quality": "standard", "n": 1}
    preset: dict[str, Any] = {"quality": "standard", "values": {"PRIVATE-PRESET-NODE": "PRIVATE-MODEL-PATH"}}
    supported = ["quality", "seed", "n"]
    if not audio:
        preset["size"] = default["size"] = "512x288" if video else "1024x1024"
        supported.append("size")
    if video or audio:
        preset["seconds"] = default["seconds"] = 1.625 if video else 2
        supported.append("seconds")
    if audio:
        preset.update(audio_format="wav", sample_rate=24000, channels=1)
    bindings = [{"source": source, "node_id": "PRIVATE-NODE", "input_name": source} for source in ("prompt", "seed", "output_prefix")]
    if task == "image_to_image":
        bindings.append({"source": "source_image", "node_id": "PRIVATE-NODE", "input_name": "image"})
        supported += ["strength", "negative_prompt"]
    if task.startswith("references_"):
        for index in range(count):
            bindings.append({"source": f"reference_{index}", "node_id": "PRIVATE-NODE", "input_name": f"image{index}"})
        if weighted:
            bindings.append({"source": "reference_weight_0", "node_id": "PRIVATE-NODE", "input_name": "weight0"})
    return {"task_type": task, "bindings": bindings, "supported_parameters": supported,
            "presets": [preset], "workflow_api": {"PRIVATE-GRAPH": "PRIVATE-MODEL-PATH"},
            "dependency_lock": {"PRIVATE-CREDENTIAL": "PRIVATE-MODEL-PATH"},
            "output": {"mime_types": ["audio/wav" if audio else "video/mp4" if video else "image/png"], "expected_count": 1, "max_bytes": 1048576}}, default


@pytest.mark.parametrize("task", ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"])
def test_usage_describes_six_task_types_with_valid_examples_and_no_private_graph(task: str) -> None:
    manifest, defaults = usage_manifest(task)
    usage = model_usage("public-model", task, manifest, defaults, {})
    example = usage["example_request"]
    JobCreate.model_validate(example)
    Draft202012Validator(usage["request_schema"]).validate(example)
    validate_reference_inputs(manifest, example["input"])
    assert "PRIVATE-" not in str(usage)
    assert usage["task_type"] == task and usage["output"]["media_types"] == manifest["output"]["mime_types"]
    assert usage["references"]["supported"] == (task == "image_to_image" or task.startswith("references_"))
    assert usage["endpoints"]["poll"] == "/v1/jobs/{job_id}"
    assert usage["endpoints"]["download"] == "/v1/content/{content_id}/download"


def test_request_schema_validates_preset_combinations_after_applying_model_defaults() -> None:
    manifest, defaults = usage_manifest()
    manifest["presets"].append({"quality": "draft", "size": "512x512", "values": {}})
    usage = model_usage("public-model", "text_to_image", manifest, defaults, {})
    validate = Draft202012Validator(usage["request_schema"])
    base = {"model": "public-model", "task_type": "text_to_image", "input": {"prompt": "test"}}
    validate.validate(base)
    validate.validate({**base, "parameters": {"quality": "draft", "size": "512x512"}})
    for parameters in ({"quality": "draft"}, {"size": "512x512"}, {"size": "720x1080"}, {"seconds": 1}, {"n": 2}):
        with pytest.raises(JsonValidationError):
            validate.validate({**base, "parameters": parameters})


@pytest.mark.parametrize("count", [1, 2, 4])
def test_exact_reference_slots_and_per_slot_weight_permissions(count: int) -> None:
    manifest, defaults = usage_manifest("references_to_video", count=count, weighted=True)
    usage = model_usage("ref-model", "references_to_video", manifest, defaults, {})
    validate = Draft202012Validator(usage["request_schema"])
    request = deepcopy(usage["example_request"])
    assert usage["references"]["exact_reference_count"] == count
    request["input"]["references"][0]["weight"] = 0.5
    validate.validate(request)
    request["input"]["references"].append({"asset_id": "asset_extra_id", "role": "extra"})
    with pytest.raises(JsonValidationError):
        validate.validate(request)
    if count > 1:
        request = deepcopy(usage["example_request"])
        request["input"]["references"][1]["weight"] = 0.5
        with pytest.raises(JsonValidationError):
            validate.validate(request)


def test_documentation_changes_text_and_example_without_expanding_parameters() -> None:
    manifest, defaults = usage_manifest()
    usage = model_usage("public-model", "text_to_image", manifest, defaults, {"display_name": "Public name", "description": "Plain text <script> is data.", "example_prompt": "A publisher-provided example", "notes": ["One useful note"], "tags": ["illustration"]})
    assert usage["display_name"] == "Public name" and usage["example_request"]["input"]["prompt"] == "A publisher-provided example"
    assert "seconds" not in usage["request_schema"]["properties"]["parameters"]["properties"]
    assert defaults == {"quality": "standard", "n": 1, "size": "1024x1024"}


@pytest.mark.parametrize("field,value", [("display_name", "x" * 161), ("description", "x" * 4001), ("example_prompt", "x" * 8001), ("notes", [" "]), ("notes", ["x"] * 13), ("tags", ["x" * 501])])
def test_public_documentation_has_bounded_fields(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ModelDocumentation.model_validate({field: value})
