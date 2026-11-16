"""Archive reviewed published presets without changing any live model binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from generation_domain.values import fingerprint

VERSION = "2026-11-16"
ALIASES = re.compile(r"^comfy-(3080|4090|5070)-[a-z0-9-]+$")
DEVICE_PATH_INPUTS = {"repo_root", "pixi_python", "pipeline_yaml"}
MEDIA_EXTENSIONS = {"image": "png", "audio": "wav", "video": "mp4", "model": "glb"}


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def category(alias: str, manifest: dict[str, Any]) -> str:
    task = manifest["task_type"]
    fixed = {
        "text_to_image": "images/text-to-image",
        "image_to_image": "images/image-edit",
        "references_to_image": "images/references-to-image",
        "text_to_video": "videos/text-to-video",
    }
    if task in fixed:
        return fixed[task]
    if task == "references_to_video":
        references = [b for b in manifest["bindings"] if b["source"].startswith("reference_")]
        return "videos/" + ("image-to-video" if len(references) == 1 else "references-to-video")
    if task == "text_to_audio":
        slots = manifest.get("input_slots", [])
        return "audio/" + ("music" if "yue2" in alias else "dialogue" if len(slots) > 1 else "speech")
    if task == "image_to_3d":
        slots = manifest.get("input_slots", [])
        kind = "mesh-refinement" if any(s["kind"] == "model" for s in slots) else "multiview-to-3d" if len(slots) > 1 else "image-to-3d"
        return "3d/" + kind
    raise ValueError("Unsupported preset task type")


def safe_write(path: Path, value: Any) -> None:
    if path.is_symlink() or any(p.is_symlink() or p.is_junction() for p in path.parents):
        raise ValueError("Preset output must not traverse links")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def replace_input(graph: dict[str, Any], binding: dict[str, Any], value: Any) -> None:
    target = graph[binding["node_id"]]["inputs"]
    name = binding["input_name"]
    pointer = binding.get("pointer")
    if not pointer:
        target[name] = value
        return
    # Existing bindings may select a leaf inside a serialized widget document.
    serialized = isinstance(target[name], str)
    document = json.loads(target[name]) if serialized else deepcopy(target[name])
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]
    current = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if isinstance(current, list):
        current[int(parts[-1])] = value
    else:
        current[parts[-1]] = value
    target[name] = json.dumps(document, ensure_ascii=False, separators=(",", ":")) if serialized else document


def portable_graph(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    manifest, usage = record["release"]["manifest"], record["usage"]
    graph = deepcopy(manifest["workflow_api"])
    alias = record["model"]["alias"]
    example = usage["example_request"]
    inputs, parameters = example.get("input", {}), example.get("parameters", {})
    media = {s["name"]: s["kind"] for s in manifest.get("input_slots", [])}
    for node in graph.values():
        node.pop("_meta", None)
    for binding in manifest["bindings"]:
        source = binding["source"]
        if source in {"output_prefix", "output_basename"}:
            value: Any = f"platform-presets/{alias}/output" if source == "output_prefix" else "output"
        elif source == "seed":
            value = 42
        elif source in {"prompt", "negative_prompt"}:
            value = inputs.get(source, "" if source == "negative_prompt" else "A small blue ceramic teapot on a plain background.")
        elif source.startswith("text:"):
            slot = source.removeprefix("text:")
            value = inputs.get("texts", {}).get(slot, "")
        elif source.startswith("media:") or source in {"source_image", "reference_0", "reference_1"}:
            slot = source.removeprefix("media:")
            extension = MEDIA_EXTENSIONS[media.get(slot, "image")]
            value = f"REPLACE_WITH_UPLOAD_{slot.upper()}.{extension}"
        elif source in {"width", "height"}:
            size = parameters.get("size", usage["defaults"].get("size"))
            if not size:
                raise ValueError("Missing advertised dimensions")
            value = int(size.split("x")[0 if source == "width" else 1])
        elif source in parameters:
            value = parameters[source]
        else:
            raise ValueError("Unreviewed binding source: " + source)
        replace_input(graph, binding, value)
    overrides = {}
    for node_id, node in graph.items():
        for name, value in node["inputs"].items():
            if isinstance(value, str) and re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\)", value):
                if name not in DEVICE_PATH_INPUTS:
                    raise ValueError("Unreviewed absolute path")
                marker = f"__DEVICE_{name.upper()}__"
                overrides[f"{node_id}/{name}"] = value
                node["inputs"][name] = marker
            if isinstance(value, str) and re.search(r"(?i)(?:https?://[^ /]+:[^ /]+@|bearer\s+[a-z0-9._-]{16}|(?:sk-|ghp_|github_pat_)[a-z0-9]{20})", value):
                raise ValueError("Credential-like value in graph")
    return graph, overrides


def archive(snapshot: Path, output: Path, private_output: Path, version: str = VERSION) -> dict[str, Any]:
    if date.fromisoformat(version).isoformat() != version:
        raise ValueError("Archive version must use YYYY-MM-DD")
    native_root = f"平台预设/{version}"
    records = json.loads(snapshot.read_bytes())
    if not isinstance(records, list) or not records:
        raise ValueError("Expected a nonempty reviewed published-model snapshot")
    output = output.absolute()
    if private_output.resolve().is_relative_to(output.resolve()):
        raise ValueError("Private frozen snapshots must stay outside the public archive")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Archive destination must be empty; existing versions are immutable")
    entries, overrides, pending = [], {}, []
    seen: set[str] = set()
    for record in records:
        model, release, usage = record["model"], record["release"], record["usage"]
        alias = model["alias"]
        match = ALIASES.fullmatch(alias)
        if not match or alias in seen or not model["published"]:
            raise ValueError("Invalid, duplicate or unpublished model")
        seen.add(alias)
        if release["id"] != model["release_id"] or fingerprint(release["manifest"]) != release["digest"] or not release["validated"]:
            raise ValueError("Frozen release identity or digest mismatch")
        if usage["example_request"]["model"] != alias or usage["task_type"] != model["task_type"]:
            raise ValueError("Usage and model identity mismatch")
        path = category(alias, release["manifest"]) + "/" + alias
        graph, private = portable_graph(record)
        overrides[alias] = private
        native = f"{native_root}/{path}.json"
        release_pin = {k: release[k] for k in ("id", "workflow_id", "version", "digest", "validated")}
        release_pin.update(digest_scope="server-frozen-manifest", snapshot_version=version)
        entry = {"alias": alias, "display_name": usage["display_name"], "task_type": model["task_type"],
                 "device": "rtx" + match[1], "directory": path, "native_workflow": native,
                 "release_id": release["id"], "release_digest": release["digest"],
                 "device_path_overrides_required": sorted(private),
                 "node_classes": sorted({n["class_type"] for n in graph.values()})}
        folder = output / path
        for name, value in {"workflow.api.json": graph, "bindings.json": release["manifest"]["bindings"],
                            "usage.json": usage, "request.json": usage["example_request"],
                            "release.json": release_pin, "preset.json": entry}.items():
            pending.append((folder / name, value))
        entries.append(entry)
    entries.sort(key=lambda e: e["directory"])
    index = {"format_version": 1, "snapshot_version": version, "source": "published immutable workflow releases",
             "native_root": native_root, "base_url": "https://gen.sub2share.xyz", "count": len(entries), "presets": entries}
    # Validate the complete input before creating any public artifact.
    for target, value in pending:
        safe_write(target, value)
    safe_write(output / "catalog.json", index)
    safe_write(private_output / "device-overrides-private.json", overrides)
    safe_write(private_output / "frozen-snapshot-private.json", records)
    return index


def seal(output: Path) -> dict[str, Any]:
    index = json.loads((output / "catalog.json").read_bytes())
    for item in index["presets"]:
        folder = output / item["directory"]
        if not (folder / "workflow.ui.json").is_file():
            raise ValueError("Native canvas conversion is incomplete")
    files = sorted(p for p in output.rglob("*") if p.is_file() and p.name != "checksums.json")
    checksums = {"format_version": 1, "algorithm": "sha256", "files": {p.relative_to(output).as_posix(): digest(p) for p in files}}
    safe_write(output / "checksums.json", checksums)
    return checksums


def complete(output: Path, canvases: Path, metadata: Path) -> dict[str, Any]:
    index = json.loads((output / "catalog.json").read_bytes())
    observed_at = datetime.now(UTC).isoformat()
    for entry in index["presets"]:
        folder = output / entry["directory"]
        conversion = json.loads((canvases / (entry["alias"] + "-canvas-private.json")).read_bytes())
        graph = json.loads((folder / "workflow.api.json").read_bytes())
        classes = json.loads((metadata / (entry["device"] + "-object-info-direct-private.json")).read_bytes())
        missing = sorted(set(entry["node_classes"]) - classes.keys())
        differences = conversion["differences"]
        entry["runtime_observation"] = {"at": observed_at, "node_classes_present": not missing,
            "missing_node_classes": missing, "weights_checked": False, "generation_executed": False}
        entry["canvas_compatibility"] = {"frontend_differences": differences,
            "authoritative_execution_template": "workflow.api.json",
            "embedded_api_graph": "extra.platform_preset.workflow_api"}
        ui = conversion["ui"]
        if len(ui["nodes"]) != len(graph):
            raise ValueError("Native canvas is missing archived execution nodes")
        ui["extra"] = {"ds": ui.get("extra", {}).get("ds", {}), "platform_preset": {
            "alias": entry["alias"], "release_id": entry["release_id"], "release_digest": entry["release_digest"],
            "workflow_api": graph, "frontend_differences": differences,
            "instructions_zh": "先替换上传素材与文本。此画布是编辑副本；API 调用继续使用原模型别名。缺失节点及前端控件差异须处理后才能运行。完整执行模板保存在此文件 extra.platform_preset.workflow_api。",
            "instructions_en": "Replace uploaded media and text first. This canvas is an editable copy; API calls retain the original alias. Resolve missing nodes and frontend widget differences before running. The complete execution template is embedded in extra.platform_preset.workflow_api."}}
        note = (f"{entry['alias']}\n\n"
                "平台预设 / Platform preset\n先上传并替换媒体输入。Upload and replace media inputs first.\n"
                "原 API 别名与冻结版本保留。Original API alias and frozen release are preserved.\n"
                f"当前缺失节点 / Currently missing nodes: {', '.join(missing) or '无 / none'}\n"
                f"前端控件差异 / Frontend widget differences: {len(differences)}\n"
                "存在差异时以 workflow.api.json / extra.platform_preset.workflow_api 为准。\n"
                "When widgets differ, use the complete API template as the reference.")
        existing = {str(n["id"]) for n in ui["nodes"]}
        note_id = max([int(n) for n in existing if n.isdigit()] + [0]) + 1
        positions = [n.get("pos", [0, 0]) for n in ui["nodes"]]
        ui["nodes"].append({"id": note_id, "type": "Note", "pos": [min(p[0] for p in positions), min(p[1] for p in positions) - 420],
            "size": [650, 340], "flags": {}, "order": 0, "mode": 0, "inputs": [], "outputs": [],
            "properties": {}, "widgets_values": [note], "title": "平台预设说明 / Preset instructions", "color": "#d2f53c", "bgcolor": "#252827"})
        ui["last_node_id"] = max(note_id, ui.get("last_node_id", 0))
        safe_write(folder / "workflow.ui.json", ui)
        safe_write(folder / "preset.json", entry)
    safe_write(output / "catalog.json", index)
    return index


def verify(output: Path) -> int:
    checksums = json.loads((output / "checksums.json").read_bytes())
    expected = checksums["files"]
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file() and p.name != "checksums.json"}
    if actual != set(expected):
        raise ValueError("Preset archive file set changed")
    for name, sha in expected.items():
        path = output / name
        if not path.resolve().is_relative_to(output.resolve()) or path.is_symlink() or path.is_junction() or digest(path) != sha:
            raise ValueError("Preset archive path or digest mismatch")
    return len(expected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["archive", "complete", "seal", "verify"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--private-output", type=Path)
    parser.add_argument("--canvases", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--version", default=VERSION, help="New archive version in YYYY-MM-DD format")
    args = parser.parse_args()
    if args.action == "archive":
        if not args.snapshot or not args.private_output:
            parser.error("archive requires --snapshot and --private-output")
        result = archive(args.snapshot, args.output, args.private_output, args.version)
        print(json.dumps({"presets": result["count"]}))
    elif args.action == "complete":
        if not args.canvases or not args.metadata:
            parser.error("complete requires --canvases and --metadata")
        print(json.dumps({"completed_presets": complete(args.output, args.canvases, args.metadata)["count"]}))
    elif args.action == "seal":
        print(json.dumps({"sealed_files": len(seal(args.output)["files"])}))
    else:
        print(json.dumps({"verified_files": verify(args.output)}))


if __name__ == "__main__":
    main()
