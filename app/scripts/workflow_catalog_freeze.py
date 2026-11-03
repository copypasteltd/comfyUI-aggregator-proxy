"""Build private manifest drafts, never mark validation/approval or publish."""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path

from generation_contracts.model_usage import ModelDocumentation
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflows import validate_manifest
from workflow_catalog_metadata import describe_media_slots, public_documentation

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--audit-dir", type=Path, required=True)
parser.add_argument("--dependency-dir", type=Path, required=True)
parser.add_argument("--plan", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
AUDIT = args.audit_dir.resolve()
DEST = args.output.resolve()
PLAN = json.loads(args.plan.read_bytes())
PLATFORM = json.loads((AUDIT / "platform-private.json").read_bytes())
LOCKS = {
    gpu: json.loads((args.dependency_dir / (gpu + "-native-locks-private.json")).read_bytes())
    for gpu in ["3080", "4090"]
}
INFO = {gpu: json.loads((AUDIT / gpu / "object_info.json").read_bytes()) for gpu in LOCKS}
ROWS = {
    gpu: {
        row["path"]: row for row in json.loads((AUDIT / gpu / "analysis-private.json").read_bytes())
    }
    for gpu in LOCKS
}
DEST.mkdir(parents=True, exist_ok=True)


def scalar(graph, value):
    if not isinstance(value, list):
        return value
    key, position = value
    node = graph[key]
    values = node["inputs"]
    cls = node["class_type"]
    if cls == "MiniMaxH3MotionContext" and position == 1:
        if "context_latent" not in values and "context_frames" not in values:
            return 0
        frames = int(values["context_length"])
        if frames not in (5, 22, 39, 56):
            raise ValueError("motion_context_window_not_frozen")
        return frames
    if cls in ["ResolutionSelector", "ResolutionSelectorLive"]:
        w, h = map(int, values["aspect_ratio"].split(" ")[0].split(":"))
        scale = math.sqrt(values["megapixels"] * 1024 * 1024 / (w * h))
        multiple = values["multiple"]
        return round([w, h][position] * scale / multiple) * multiple
    if cls == "ComfyMathExpression":
        variables = {
            name.split(".")[-1]: scalar(graph, item)
            for name, item in values.items()
            if name.startswith("values.")
        }

        def visit(node):
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in [int, float]:
                return node.value
            if isinstance(node, ast.Name) and node.id in variables:
                return variables[node.id]
            if isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                operations = {
                    ast.Add: lambda: left + right,
                    ast.Sub: lambda: left - right,
                    ast.Mult: lambda: left * right,
                    ast.Div: lambda: left / right,
                    ast.Mod: lambda: left % right,
                }
                if type(node.op) in operations:
                    return operations[type(node.op)]()
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                return -visit(node.operand)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ["max", "min", "round"]
                and not node.keywords
            ):
                return {"max": max, "min": min, "round": round}[node.func.id](
                    *[visit(arg) for arg in node.args]
                )
            raise ValueError("unsupported_static_expression")

        return visit(ast.parse(values["expression"].strip(), mode="eval"))
    for name in ["value", "int", "float"]:
        if name in values:
            return scalar(graph, values[name])
    raise ValueError("dynamic_scalar:" + cls)


def specs(item, graph):
    kind = item["output_kind"]
    preset = {"quality": "standard", "values": {}}
    if kind == "model":
        return preset
    if kind == "audio":
        names = {n["class_type"] for n in graph.values()}
        source = (
            "yue2"
            if "YuE2GenerateMusic" in names
            else "soulx"
            if "SoulXPodcastInputParser" in names
            else "native"
        )
        rate = 48000 if source == "yue2" else 24000
        channels = 2 if source == "yue2" else 1
        form = "mp3" if item["output_class"] == "SaveAudioMP3" else "flac"
        preset.update(seconds=30, audio_format=form, sample_rate=rate, channels=channels)
        # Enforce the advertised upper bound inside the graph for spoken content.
        if source != "yue2":
            final = graph[item["final_node_id"]]["inputs"]
            audio = final["audio"]
            if graph[audio[0]]["class_type"] != "TrimAudioDuration":
                graph["rc_audio_trim"] = {
                    "class_type": "TrimAudioDuration",
                    "inputs": {"audio": audio, "start_index": 0, "duration": 30},
                }
                final["audio"] = ["rc_audio_trim", 0]
        return preset
    if kind == "image":
        choices = [
            node["inputs"]
            for node in graph.values()
            if node["class_type"] in ["EmptyLatentImage", "EmptySD3LatentImage", "ImageScale"]
        ]
        choices = [values for values in choices if "width" in values and "height" in values]
        if choices:
            try:
                w, h = (int(scalar(graph, choices[-1][name])) for name in ["width", "height"])
            except ValueError:
                w, h = 1024, 1024
        else:
            w, h = 1024, 1024
        # Freeze a deterministic delivery size for graphs whose latent size follows input.
        final = graph[item["final_node_id"]]["inputs"]
        source = final.get("images")
        if not choices or any(n["class_type"] == "QwenRegionSelect" for n in graph.values()):
            graph["rc_delivery_size"] = {
                "class_type": "ImageScale",
                "inputs": {
                    "image": source,
                    "upscale_method": "lanczos",
                    "width": w,
                    "height": h,
                    "crop": "center",
                },
            }
            final["images"] = ["rc_delivery_size", 0]
        preset["size"] = f"{w}x{h}"
        return preset
    timelines = [n for n in graph.values() if n["class_type"] == "easy multiTrackEditor"]
    if timelines:
        node = timelines[-1]
        values = node["inputs"]
        if "resolution.width" in values:
            w, h = [
                int(scalar(graph, values["resolution." + name])) for name in ["width", "height"]
            ]
        else:
            wr, hr = map(int, values["resolution.aspect_ratio"].split(" ")[0].split(":"))
            scale = math.sqrt(values["resolution.megapixels"] * 1024 * 1024 / (wr * hr))
            w, h = round(wr * scale / 32) * 32, round(hr * scale / 32) * 32
        # Original community execution evidence determines whole-timeline rounding.
        seconds = 14.792 if "02c_" in item["source"] else 34.157
        preset.update(size=f"{w}x{h}", seconds=seconds)
        return preset
    generators = [
        (key, node)
        for key, node in graph.items()
        if node["class_type"]
        in [
            "MiniMaxH3ReferenceToVideo",
            "MiniMaxH3ImageToVideo",
            "MiniMaxH3AudioConditioningT8",
            "MiniMaxH3Easy",
            "ViggleAnimateConditioning",
            "easy minimaxH3ToVideo",
        ]
    ]
    if not generators:
        raise ValueError("video_generator_not_identified")
    key, node = generators[-1]
    values = node["inputs"]
    fps = 24
    w, h = (int(scalar(graph, values[name])) for name in ["width", "height"])
    try:
        length = scalar(graph, values.get("length", values.get("seconds", 5) * 24))
    except ValueError as error:
        if "VHS_VideoInfo" not in str(error):
            raise
        # These source-conditioned community graphs now publish an explicit 15s profile.
        length = 362
        values["length"] = length
        for n in graph.values():
            if n["class_type"] in ["VHS_LoadVideo", "VHS_LoadVideoFFmpeg"]:
                n["inputs"].update(force_rate=24, frame_load_cap=362)
    seconds = length / fps
    if node["class_type"] == "MiniMaxH3Easy":
        seconds = values["seconds"]
    # Formal postprocessing, including original DLSS/resize, affects delivered dimensions.
    for n in graph.values():
        if n["class_type"] in ["ImageResizeKJv2", "ImageScale"]:
            v = n["inputs"]
            if (
                type(v.get("width")) is int
                and type(v.get("height")) is int
                and v["width"] > 0
                and v["height"] > 0
            ):
                w, h = v["width"], v["height"]
    trim = [n for n in graph.values() if n["class_type"] == "MiniMaxH3MotionContextTrim"]
    if trim:
        frames = scalar(graph, trim[-1]["inputs"].get("trim_frames", 0))
        if type(frames) is not int:
            raise ValueError("video_trim_not_resolved")
        seconds = (length - frames) / fps
    if not 0 < seconds <= 120:
        raise ValueError("video_duration_unresolved")
    preset.update(size=f"{w}x{h}", seconds=round(seconds, 3))
    return preset


def dependency_lock(item, graph):
    gpu = item["gpu"]
    snapshot = LOCKS[gpu]
    available = snapshot["models"]
    chosen = {}
    models = ROWS[gpu][item["source"]]["models"]
    for model in models:
        if not any(
            node["class_type"] == model["class"]
            and node["inputs"].get(model["field"]) == model["value"]
            for node in graph.values()
        ):
            continue
        value = model["value"].replace("\\", "/")
        if model["field"] in ["upscale_method", "upscale_mode"] or value in [
            "无",
            "None",
            "none",
            "disabled",
            "None (disabled)",
        ]:
            continue
        if model["class"] == "VisualLoRALoader":
            if model["field"] != "lora_data":
                continue
            for lora in json.loads(model["value"]):
                if not lora.get("enabled", True):
                    continue
                value = lora["name"].replace("\\", "/").lstrip("/")
                found = [
                    m for m in available if m["path"].endswith("/" + value) or m["path"] == value
                ]
                if not found:
                    raise ValueError("visual_lora_unresolved:" + value)
                for m in found:
                    chosen[m["path"]] = {k: m[k] for k in ["path", "sha256", "bytes"]}
            continue
        found = [m for m in available if m["path"] == value or m["path"].endswith("/" + value)]
        if not found:
            found = [
                m
                for m in available
                if (m["path"].startswith("TTS/" + value + "/") or ("/" + value + "/") in m["path"])
                and not m["path"].endswith(".verified.json")
            ]
        if not found and model["class"] == "NativeWindows3D":
            continue
        if not found:
            raise ValueError(
                "model_file_unresolved:" + model["class"] + "." + model["field"] + ":" + value
            )
        for m in found:
            chosen[m["path"]] = {k: m[k] for k in ["path", "sha256", "bytes"]}
    directories = {
        INFO[gpu][node["class_type"]]["python_module"].split(".")[1]
        for node in graph.values()
        if INFO[gpu][node["class_type"]].get("python_module", "").startswith("custom_nodes.")
    }
    locked_nodes = []
    files = []
    for directory in sorted(directories):
        found = next((n for n in snapshot["nodes"] if n["directory"] == directory), None)
        if not found:
            raise ValueError("node_directory_unresolved:" + directory)
        if found["commit"] and not found["tracked_changes"]:
            locked_nodes.append({"directory": directory, "commit": found["commit"]})
        else:
            files.extend(found["files"])
    return {
        "comfy_commit": snapshot["comfy_commit"],
        "models": sorted(chosen.values(), key=lambda m: m["path"]),
        "custom_nodes": locked_nodes,
        "files": files,
    }


def audio_policy(item, graph, seconds):
    if item["output_kind"] != "video":
        return None
    source_video = next((s["name"] for s in item["input_slots"] if s["kind"] == "video"), None)
    if Path(item["source"]).name == "09a_t8_lock_source_audio.json":
        binding = next(b for b in item["bindings"] if b["source"] == "media:" + source_video)
        components = [
            (key, node)
            for key, node in graph.items()
            if node["class_type"] == "GetVideoComponents"
            and node["inputs"].get("video") == [binding["node_id"], 0]
        ]
        muxers = [n for n in graph.values() if n["class_type"] == "CreateVideo"]
        if len(components) != 1 or len(muxers) != 1:
            raise ValueError("source_audio_mux_not_identified")
        graph["rc_source_audio_trim"] = {
            "class_type": "TrimAudioDuration",
            "inputs": {"audio": [components[0][0], 1], "start_index": 0, "duration": seconds},
        }
        muxers[0]["inputs"]["audio"] = ["rc_source_audio_trim", 0]
        return {
            "mode": "source_video",
            "source_slot": source_video,
            "description": "源视频音轨直接送入最终封装，并按片段时长裁剪。Original source-video audio is muxed directly, trimmed to clip duration.",
        }
    # Viggle's original graph passes extracted source audio directly to its muxer.
    if any(n["class_type"] == "ViggleAnimateConditioning" for n in graph.values()) and source_video:
        return {
            "mode": "source_video",
            "source_slot": source_video,
            "description": "保留源表演视频音轨；人物动作迁移不重新生成声音。Original performance audio is passed to the final muxer.",
        }
    mux = [n for n in graph.values() if n["class_type"] in ["CreateVideo", "VHS_VideoCombine"]]
    has_audio = any(isinstance(n["inputs"].get("audio"), list) for n in mux) or any(
        n["class_type"] == "MiniMaxH3Easy"
        or n["class_type"] == "easy saveVideo"
        and isinstance(n["inputs"].get("input_mode.audio"), list)
        for n in graph.values()
    )
    return {
        "mode": "generated" if has_audio else "silent",
        "description": "模型生成声音；参考音频属于条件，不能视为原声无损保留。Model-generated audio; references condition generation."
        if has_audio
        else "该工作流输出静音视频。This workflow delivers silent video.",
    }


def freeze(key, original):
    item = deepcopy(original)
    describe_media_slots(item)
    graph = item["workflow_api"]
    kind = item["output_kind"]
    images = sum(s["kind"] == "image" for s in item["input_slots"])
    task = (
        "image_to_3d"
        if kind == "model"
        else "text_to_audio"
        if kind == "audio"
        else ("references_to_video" if item["input_slots"] else "text_to_video")
        if kind == "video"
        else "references_to_image"
        if images > 1
        else "image_to_image"
        if images
        else "text_to_image"
    )
    adapter = {
        "model": "model3d-glb-v1",
        "image": "save-image-v1",
        "video": "video-files-v1",
        "audio": "audio-files-v1",
    }[kind]
    mime = {
        "model": "model/gltf-binary",
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/mpeg" if item["output_class"] == "SaveAudioMP3" else "audio/flac",
    }[kind]
    output = {
        "adapter": adapter,
        "final_node_ids": [item["final_node_id"]],
        "expected_count": 1,
        "history_field": item["history_field"],
        "max_bytes": 512 * 1024**2
        if kind == "model"
        else 32 * 1024**2
        if kind == "image"
        else 256 * 1024**2
        if kind == "audio"
        else 2 * 1024**3,
        "mime_types": [mime],
    }
    # Mesh-only selectors must not retain a frozen customer/source input filename.
    for node in graph.values():
        if node["class_type"] == "UTXLoadMeshV2":
            node["inputs"].update(mesh_file="[custom path]", blender_path="")
    preset = specs(item, graph)
    if policy := audio_policy(item, graph, preset.get("seconds")):
        output["audio_policy"] = policy
    old = next(
        v["release"]["manifest"]["runtime_profiles"][0]
        for a, v in PLATFORM["details"].items()
        if a.startswith("comfy-" + item["gpu"] + "-")
    )
    context_nodes = [
        n for n in graph.values() if n["class_type"] == "MiniMaxH3MotionContextLoadLatent"
    ]
    context = None
    if context_nodes or item["context_save_nodes"]:
        for node in context_nodes:
            node["inputs"].update(folder="input", clip_index=1)
        compatibility = fingerprint(
            {
                "family": "h3-av-v1",
                "weights": [
                    m
                    for m in ROWS[item["gpu"]][item["source"]]["models"]
                    if m["class"] in ["UNETLoader", "VAELoader"]
                ],
                "size": preset["size"],
            }
        )
        context = {
            "family": "h3-av-v1",
            "compatibility": compatibility,
            "load": bool(context_nodes),
            "save_node_id": next(iter(item["context_save_nodes"]), None),
        }
    supported = ["quality", "n"] + (
        ["seed"] if any(b["source"] == "seed" for b in item["bindings"]) else []
    )
    if kind in ["image", "video"]:
        supported += ["size"]
    if kind in ["audio", "video"]:
        supported += ["seconds"]
    if any(b["source"] == "negative_prompt" for b in item["bindings"]):
        supported += ["negative_prompt"]
    manifest = {
        "format_version": "1.3",
        "task_type": task,
        "workflow_api": graph,
        "bindings": item["bindings"],
        "input_slots": item["input_slots"],
        "text_slots": item["text_slots"],
        "output": output,
        "presets": [preset],
        "dependency_lock": dependency_lock(item, graph),
        "runtime_profiles": [
            {
                "id": "native-" + key,
                "gpu_hardware_id": old["gpu_hardware_id"],
                "approved": False,
                "execution_timeout_seconds": 7200,
            }
        ],
        "supported_parameters": supported,
    }
    if context:
        manifest["context"] = context
    WorkflowManifest.model_validate(manifest)
    validate_manifest(manifest, task)
    slug = Path(item["source"]).stem.removesuffix(".api").removesuffix(".workflow")
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:65]
    if not slug:
        slug = "preset"
    alias = f"comfy-{item['gpu']}-{slug}-{key.split('-')[1][:6]}"[:96]
    documentation = public_documentation(item, manifest)
    ModelDocumentation.model_validate(documentation)
    return {
        "key": key,
        "alias": alias,
        "display_name": documentation["display_name"],
        "documentation": documentation,
        "manifest": manifest,
        "source_files": item["source_files"],
        "source_ids": item["source_ids"],
        "review": item["review"],
        "defaults": {k: v for k, v in preset.items() if k in ["quality", "size", "seconds"]}
        | {"n": 1},
        "state": "manifest_validated_locally",
    }


frozen = {}
failures = {}
for key, item in PLAN["models"].items():
    try:
        result = freeze(key, item)
        frozen[key] = result
        (DEST / (key + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
    except Exception as error:
        failures[key] = {
            "source": item["source"],
            "code": error.code if isinstance(error, DomainError) else str(error)[:800],
        }
report = {
    "frozen": len(frozen),
    "adapter_pending": len(failures),
    "by_kind": dict(Counter(v["manifest"]["task_type"] for v in frozen.values())),
    "failures": failures,
}
(DEST / "freeze-report-private.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8"
)
print(json.dumps(report, ensure_ascii=False, indent=2))
