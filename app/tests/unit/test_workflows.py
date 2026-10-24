from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from generation_domain.errors import DomainError
from generation_domain.workflows import compile_graph, relative_file, validate_manifest


def manifest(task_type: str = "references_to_image") -> dict[str, Any]:
    return {"format_version": "1.0", "task_type": task_type, "workflow_api": {"1": {"class_type": "SyntheticCondition", "inputs": {"text": "", "seed": 0, "reference_0": "", "reference_1": ""}}, "2": {"class_type": "SyntheticSave", "inputs": {"image": ["1", 0], "filename_prefix": ""}}}, "bindings": [{"source": name, "node_id": "2" if name == "output_prefix" else "1", "input_name": "filename_prefix" if name == "output_prefix" else "text" if name == "prompt" else name} for name in ("prompt", "seed", "reference_0", "reference_1", "output_prefix")], "output": {"adapter": "save-image-v1", "final_node_ids": ["2"], "expected_count": 1}, "presets": [{"quality": "standard", "size": "1024x1024", "values": {}}], "dependency_lock": {"comfy_commit": "a" * 40, "models": [], "custom_nodes": []}, "runtime_profiles": [], "supported_parameters": []}


def test_reference_compilation_preserves_order_and_never_modifies_release() -> None:
    release = manifest()
    snapshot = deepcopy(release)
    validate_manifest(release, "references_to_image")
    execution = {"parameters": {"quality": "standard", "size": "1024x1024", "seed": 17}, "input": {"prompt": "Synthetic condition", "references": [{"asset_id": "first", "role": "character"}, {"asset_id": "second", "role": "scene"}]}}
    graph = compile_graph(release, execution, {"reference_0": "inputs/first.png", "reference_1": "inputs/second.png"}, "outputs/synthetic")
    assert graph["1"]["inputs"]["reference_0"] == "inputs/first.png"
    assert graph["1"]["inputs"]["reference_1"] == "inputs/second.png"
    assert release == snapshot


def test_second_reference_cannot_be_silently_ignored() -> None:
    release = manifest()
    release["bindings"] = [b for b in release["bindings"] if b["source"] != "reference_1"]
    with pytest.raises(DomainError, match="reference_1"):
        validate_manifest(release, "references_to_image")


def test_single_frame_video_compiles_only_for_one_frozen_slot() -> None:
    release = manifest("references_to_video")
    release["output"]["adapter"] = "video-files-v1"
    release["bindings"] = [b for b in release["bindings"] if b["source"] != "reference_1"]
    validate_manifest(release, "references_to_video")
    execution = {"parameters": {"quality": "standard", "size": "1024x1024", "seed": 17}, "input": {"prompt": "Synthetic first frame", "references": [{"asset_id": "first", "role": "first_frame"}]}}
    graph = compile_graph(release, execution, {"reference_0": "inputs/first.png"}, "outputs/video")
    assert graph["1"]["inputs"]["reference_0"] == "inputs/first.png"
    execution["input"]["references"].append({"asset_id": "second", "role": "scene"})
    with pytest.raises(DomainError, match="参考数量"):
        compile_graph(release, execution, {"reference_0": "inputs/first.png", "reference_1": "inputs/second.png"}, "outputs/video")


def test_unbound_reference_weight_and_noncontiguous_slots_are_rejected() -> None:
    release = manifest()
    execution = {"parameters": {"quality": "standard", "size": "1024x1024", "seed": 17}, "input": {"prompt": "Synthetic references", "references": [{"asset_id": "first", "role": "character", "weight": 0.5}, {"asset_id": "second", "role": "scene"}]}}
    with pytest.raises(DomainError, match="参考权重"):
        compile_graph(release, execution, {"reference_0": "first.png", "reference_1": "second.png"}, "outputs/video")
    release["workflow_api"]["1"]["inputs"]["reference_3"] = ""
    release["bindings"].append({"source": "reference_3", "node_id": "1", "input_name": "reference_3"})
    with pytest.raises(DomainError, match="连续绑定"):
        validate_manifest(release, "references_to_image")


@pytest.mark.parametrize("path", ["../secret", "C:/private", "folder/../file", "folder\\file", "/root/file", "a//file", "a:stream", "./file"])
def test_paths_cannot_escape_authorized_root(path: str) -> None:
    with pytest.raises(DomainError):
        relative_file(path)
