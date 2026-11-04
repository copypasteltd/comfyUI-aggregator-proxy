from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/workflow_catalog_native.py"
SPEC = importlib.util.spec_from_file_location("native_catalog_tool", SCRIPT)
assert SPEC and SPEC.loader
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def file(path: str) -> dict[str, Any]:
    return {"path": path, "sha256": "f" * 64, "bytes": 10}


def recipe(manifest: dict[str, Any], node: str) -> dict[str, Any]:
    manifest = deepcopy(manifest)
    manifest["workflow_api"]["auxiliary"] = {"class_type": node, "inputs": {"model": "TripoSG", "remove_background": False}}
    return {"alias": "stable-public-alias", "display_name": "Existing descriptive model name", "manifest": manifest, "review": ["native_auxiliary_dependencies"]}


def test_native_catalog_keeps_model_identity_and_source_draft_immutable(multimodal_manifest: dict[str, Any]) -> None:
    item = recipe(multimodal_manifest, "NativeWindows3D")
    original = deepcopy(item)
    observation = {"passed": True, "errors": [], "roots": {"native_3d": [file(name) for name in ("run_external.py", "python311/python.exe", "models/TripoSG/model.safetensors", "models/PartCrafter/model.safetensors")]}}
    updated = TOOL.with_native_dependencies(item, observation)
    assert item == original and updated["alias"] == original["alias"] and updated["display_name"] == original["display_name"]
    assert not updated["review"] and updated["manifest"]["dependency_lock"]["native_roots"]
    locked = updated["manifest"]["dependency_lock"]["native_roots"][0]["files"]
    assert any(row["path"] == "models/TripoSG/model.safetensors" for row in locked)
    assert all("PartCrafter" not in row["path"] for row in locked)


@pytest.mark.parametrize("problem", ["unverified", "missing_runtime", "metadata_without_weights", "already_frozen"])
def test_native_catalog_refuses_incomplete_or_previously_frozen_dependencies(multimodal_manifest: dict[str, Any], problem: str) -> None:
    item = recipe(multimodal_manifest, "NativeWindows3D")
    rows = [file(name) for name in ("run_external.py", "python311/python.exe", "models/TripoSG/model.safetensors")]
    observation = {"passed": problem != "unverified", "errors": [], "roots": {"native_3d": rows}}
    if problem == "missing_runtime":
        rows.pop(0)
    elif problem == "metadata_without_weights":
        rows[-1]["path"] = "models/TripoSG/config.json"
    elif problem == "already_frozen":
        item["manifest"]["dependency_lock"]["native_roots"] = [{"name": "existing", "files": [file("worker.py")]}]
    with pytest.raises(ValueError):
        TOOL.with_native_dependencies(item, observation)


def test_podcast_auxiliary_code_uses_explicit_native_roots(multimodal_manifest: dict[str, Any]) -> None:
    item = recipe(multimodal_manifest, "PodcastTrioNative")
    observation = {"passed": True, "errors": [], "roots": {"podcast_runtime": [file("worker.py")], "comfy_files": [file("models/TTS/MOSS-TTSD-v1.0/modeling_moss.py")]}}
    updated = TOOL.with_native_dependencies(item, observation)
    lock = updated["manifest"]["dependency_lock"]
    assert {root["name"] for root in lock["native_roots"]} == {"podcast_runtime", "podcast_model_code"}
    assert next(root for root in lock["native_roots"] if root["name"] == "podcast_model_code")["files"][0]["path"] == "MOSS-TTSD-v1.0/modeling_moss.py"
    assert lock.get("files") == item["manifest"]["dependency_lock"].get("files")
