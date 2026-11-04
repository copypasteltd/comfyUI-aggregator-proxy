"""Add observed native dependencies to new private drafts; never publish or mutate releases."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from generation_contracts.workflows import WorkflowManifest
from generation_domain.workflows import validate_manifest

COMFY_AUXILIARY_NODES = frozenset({
    "UltraTex-Modular", "ComfyUI-SAM3D-Native-3080", "ComfyUI-MVSAM3D-Native-3080",
    "ComfyUI-UltraShape1", "ComfyUI-UniForge3D",
})


def with_native_dependencies(item: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    if observation.get("passed") is not True or observation.get("errors"):
        raise ValueError("completed_auxiliary_hash_observation_required")
    result = deepcopy(item)
    manifest = result["manifest"]
    lock = manifest["dependency_lock"]
    graph = manifest["workflow_api"]
    roots: dict[str, list[dict[str, Any]]] = {}

    def observed(name: str) -> list[dict[str, Any]]:
        rows = observation.get("roots", {}).get(name)
        if not isinstance(rows, list) or not rows:
            raise ValueError("auxiliary_root_observation_missing:" + name)
        return deepcopy(rows)

    native = [node for node in graph.values() if node["class_type"] == "NativeWindows3D"]
    if native:
        families = {node["inputs"]["model"] for node in native}
        if not families.issubset({"TripoSG", "PartCrafter"}):
            raise ValueError("unknown_native_3d_family")
        models = set(families)
        if any(node["inputs"].get("remove_background", True) for node in native):
            models.add("RMBG-1.4")
        rows = observed("native_3d")
        roots["native_3d"] = [row for row in rows if
            row["path"] in {"run_external.py", "requirements-runtime.txt"}
            or row["path"].startswith("python311/")
            or any(row["path"].startswith("models/" + family + "/") for family in models)
            or any(row["path"].startswith("sources/" + family + "/") for family in families)]
        for family in families:
            if not any(row["path"].startswith("models/" + family + "/") and Path(row["path"]).suffix in {".bin", ".safetensors"} for row in roots["native_3d"]):
                raise ValueError("native_3d_weights_missing:" + family)
        if not any(row["path"] == "run_external.py" for row in roots["native_3d"]) or not any(row["path"] == "python311/python.exe" for row in roots["native_3d"]):
            raise ValueError("native_3d_runtime_missing")

    if any(node["class_type"] == "PodcastTrioNative" for node in graph.values()):
        roots["podcast_runtime"] = observed("podcast_runtime")
        if not any(row["path"] == "worker.py" for row in roots["podcast_runtime"]):
            raise ValueError("podcast_worker_missing")
        rows = observed("comfy_files")
        if any(not row["path"].startswith("models/TTS/") for row in rows):
            raise ValueError("podcast_model_code_outside_tts")
        roots["podcast_model_code"] = [{**row, "path": row["path"].removeprefix("models/TTS/")} for row in rows]

    directories = {node["directory"] for node in lock.get("custom_nodes", [])}
    directories.update(row["path"].split("/")[1] for row in lock.get("files", []) if row["path"].startswith("custom_nodes/") and len(row["path"].split("/")) > 2)
    if directories & COMFY_AUXILIARY_NODES:
        roots["comfy_aux"] = observed("comfy_aux")
    if roots:
        if lock.get("native_roots"):
            raise ValueError("draft_already_contains_native_dependencies")
        lock["native_roots"] = [{"name": name, "files": rows} for name, rows in sorted(roots.items())]
        result["review"] = [reason for reason in result.get("review", []) if reason != "native_auxiliary_dependencies"]
        result["state"] = "native_dependencies_frozen_locally_runtime_pending"
    WorkflowManifest.model_validate(manifest)
    validate_manifest(manifest, manifest["task_type"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifests", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_bytes())
    observation = json.loads(args.observation.read_bytes())
    args.output.mkdir(parents=True, exist_ok=False)
    changed = []
    for key in plan["models"]:
        original = json.loads((args.manifests / (key + ".json")).read_bytes())
        result = with_native_dependencies(original, observation) if key.startswith("3080-") else original
        if result != original:
            changed.append(key)
        (args.output / (key + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"drafts": len(plan["models"]), "updated_native_dependencies": len(changed), "changed_keys": changed, "published": False}))


if __name__ == "__main__":
    main()
