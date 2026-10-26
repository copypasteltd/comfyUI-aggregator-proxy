from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.workflows import TASK_TYPES, compile_graph, validate_manifest


def scalar_manifest(task_type: str) -> dict[str, Any]:
    audio, video = task_type == "text_to_audio", task_type.endswith("video")
    sources = ["prompt", "seed", "output_prefix"]
    if task_type == "image_to_image":
        sources.append("source_image")
    if task_type.startswith("references_"):
        sources += ["reference_0", "reference_1", "reference_weight_0", "reference_weight_1"]
    return {
        "format_version": "1.1" if audio else "1.0", "task_type": task_type,
        "workflow_api": {
            "1": {"class_type": "SyntheticGenerator", "inputs": {s: None if s == "seed" else "" for s in sources if s != "output_prefix"}},
            "2": {"class_type": "SyntheticSave", "inputs": {"result": ["1", 0], "filename_prefix": ""}},
        },
        "bindings": [{"source": s, "node_id": "2" if s == "output_prefix" else "1", "input_name": "filename_prefix" if s == "output_prefix" else s} for s in sources],
        "output": {"adapter": "audio-files-v1" if audio else "video-files-v1" if video else "save-image-v1", "final_node_ids": ["2"], "history_field": "audio" if audio else "videos" if video else "images", "expected_count": 1, "max_bytes": 1048576, "mime_types": ["audio/wav" if audio else "video/mp4" if video else "image/png"]},
        "presets": [{"quality": "standard", **({"seconds": 2, "audio_format": "wav", "sample_rate": 24000, "channels": 1} if audio else {"size": "1024x1024", **({"seconds": 2} if video else {})}), "values": {}}],
        "dependency_lock": {"comfy_commit": "a" * 40, "models": [], "custom_nodes": []},
        "runtime_profiles": [], "supported_parameters": ["quality", "seed"],
    }


def scalar_execution(task_type: str) -> tuple[dict[str, Any], dict[str, str]]:
    audio, video = task_type == "text_to_audio", task_type.endswith("video")
    supplied: dict[str, Any] = {"prompt": "Synthetic scalar-target regression"}
    paths: dict[str, str] = {}
    if task_type == "image_to_image":
        supplied["source_image"] = {"asset_id": "synthetic-source"}
        paths["source_image"] = "inputs/source.png"
    if task_type.startswith("references_"):
        supplied["references"] = [{"asset_id": "synthetic-first", "role": "character", "weight": 0.25}, {"asset_id": "synthetic-second", "role": "scene", "weight": 0.75}]
        paths = {"reference_0": "inputs/first.png", "reference_1": "inputs/second.png"}
    return {"input": supplied, "parameters": {"quality": "standard", "seed": 17, **({"seconds": 2} if audio else {"size": "1024x1024", **({"seconds": 2} if video else {})})}}, paths


def overwrite_edge(manifest: dict[str, Any], kind: str) -> None:
    if kind == "preset":
        manifest["presets"][0]["values"] = {"2.result": 0}
    else:
        next(b for b in manifest["bindings"] if b["source"] == "seed").update(node_id="2", input_name="result")


def check_phase(manifest: dict[str, Any], phase: str) -> Any:
    if phase == "validate":
        return validate_manifest(manifest, manifest["task_type"])
    execution, paths = scalar_execution(manifest["task_type"])
    return compile_graph(manifest, execution, paths, "outputs/synthetic")


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
@pytest.mark.parametrize("kind", ["binding", "preset"])
@pytest.mark.parametrize("phase", ["validate", "compile"])
def test_six_capabilities_reject_graph_edge_overwrites(task_type: str, kind: str, phase: str) -> None:
    manifest = scalar_manifest(task_type)
    overwrite_edge(manifest, kind)
    WorkflowManifest.model_validate(manifest)
    snapshot = deepcopy(manifest)
    with pytest.raises(DomainError) as rejected:
        check_phase(manifest, phase)
    assert rejected.value.code == ("invalid_binding" if kind == "binding" else "invalid_preset_target")
    assert rejected.value.status == 422
    assert manifest == snapshot


@pytest.mark.parametrize("original", [[], {"synthetic": "structured input"}], ids=["array", "object"])
@pytest.mark.parametrize("kind", ["binding", "preset"])
@pytest.mark.parametrize("phase", ["validate", "compile"])
def test_structured_literals_cannot_be_scalar_write_targets(original: Any, kind: str, phase: str) -> None:
    manifest = scalar_manifest("text_to_image")
    manifest["workflow_api"]["1"]["inputs"]["structured"] = original
    if kind == "binding":
        next(b for b in manifest["bindings"] if b["source"] == "seed")["input_name"] = "structured"
    else:
        manifest["presets"][0]["values"] = {"1.structured": 17}
    with pytest.raises(DomainError) as rejected:
        check_phase(manifest, phase)
    assert rejected.value.code == ("invalid_binding" if kind == "binding" else "invalid_preset_target")


@pytest.mark.parametrize("value", [[], {"synthetic": 1}], ids=["array", "object"])
def test_legacy_preset_cannot_inject_structured_values_even_if_later_binding_masks_it(value: Any) -> None:
    manifest = scalar_manifest("text_to_image")
    manifest["presets"][0]["values"] = {"1.seed": value}
    with pytest.raises(DomainError) as rejected:
        check_phase(manifest, "compile")
    assert rejected.value.code == "invalid_preset_target"


def test_optional_binding_cannot_target_edge_even_when_request_has_no_value() -> None:
    manifest = scalar_manifest("text_to_image")
    manifest["bindings"].append({"source": "strength", "node_id": "2", "input_name": "result"})
    with pytest.raises(DomainError) as rejected:
        check_phase(manifest, "compile")
    assert rejected.value.code == "invalid_binding"


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_six_valid_scalar_overrides_keep_edges_reference_order_and_release_immutable(task_type: str) -> None:
    manifest = scalar_manifest(task_type)
    manifest["workflow_api"]["1"]["inputs"]["untouched"] = {"array": [1, 2]}
    manifest["presets"][0]["values"] = {"1.seed": 3, "1.prompt": "Synthetic preset default"}
    snapshot = deepcopy(manifest)
    WorkflowManifest.model_validate(manifest)
    assert validate_manifest(manifest, task_type)["valid"]
    graph = check_phase(manifest, "compile")
    assert graph["2"]["inputs"]["result"] == ["1", 0]
    assert graph["2"]["inputs"]["filename_prefix"] == "outputs/synthetic"
    assert graph["1"]["inputs"]["seed"] == 17
    assert graph["1"]["inputs"]["prompt"] == "Synthetic scalar-target regression"
    assert graph["1"]["inputs"]["untouched"] == {"array": [1, 2]}
    if task_type.startswith("references_"):
        assert [graph["1"]["inputs"][f"reference_{i}"] for i in range(2)] == ["inputs/first.png", "inputs/second.png"]
        assert [graph["1"]["inputs"][f"reference_weight_{i}"] for i in range(2)] == [0.25, 0.75]
    assert manifest == snapshot
