"""Prepare reviewable private recipes; this command never publishes or generates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--audit-dir",
    type=Path,
    required=True,
    help="Private per-device object_info, API exports and analysis snapshots",
)
parser.add_argument(
    "--output",
    type=Path,
    required=True,
    help="Private destination beneath an operator-controlled directory",
)
args = parser.parse_args()
AUDIT = args.audit_dir.resolve()
DEST = args.output.resolve()
DEST.mkdir(parents=True, exist_ok=True)
MEDIA_CLASSES = {
    "LoadImage": ("image", "image"),
    "LoadAudio": ("audio", "audio"),
    "LoadVideo": ("video", "file"),
    "VHS_LoadVideo": ("video", "video"),
    "VHS_LoadVideoFFmpeg": ("video", "video"),
    "UltraShapeLoadCoarseMesh": ("model", "mesh_path"),
    "UTXLoadMeshV2": ("model", "file_path"),
}
TEXT_FIELDS = {
    "CLIPTextEncode": ["text"],
    "TextEncodeQwenImage21": ["prompt", "negative_prompt"],
    "TextEncodeQwenImageEditPlus": ["prompt"],
    "MiniMaxH3ImageToVideo": ["prompt"],
    "MiniMaxH3AudioConditioningT8": ["prompt"],
    "MiniMaxH3ReferenceToVideo": ["prompt"],
    "MiniMaxH3Easy": ["prompt"],
    "YuE2GenerateMusic": ["style", "lyrics", "abc"],
    "YuE2GenerateABC": ["style", "lyrics"],
    "SoulXPodcastInputParser": ["dialogue_script"],
    "PodcastTrioNative": ["dialogue_text", "S1_text", "S2_text"],
    "PrimitiveStringMultiline": ["value"],
    "Text": ["text"],
    "CR Prompt Text": ["prompt"],
    "easy promptLine": ["prompt"],
    "easy matchLine": ["text"],
    "UTXTextEncode": ["text"],
}
OUTPUTS = {
    "SaveVideo": ("video", "videos"),
    "VHS_VideoCombine": ("video", "gifs"),
    "SaveAudio": ("audio", "audio"),
    "SaveAudioMP3": ("audio", "audio"),
    "SaveAudioOpus": ("audio", "audio"),
    "Save3DAdvanced": ("model", "3d"),
    "SaveGLB": ("model", "3d"),
    "SaveRawGLB3080": ("model", "3d"),
    "MVSAM3DExport": ("model", "3d"),
    "SaveImage": ("image", "images"),
    "SaveImageAdvanced": ("image", "images"),
}


def sha(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_numbers(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_numbers(item) for item in value]
    return value


def ancestors(graph, nodes):
    reached, pending = set(), list(nodes)
    while pending:
        key = pending.pop()
        if key in reached:
            continue
        reached.add(key)
        for value in graph[key]["inputs"].values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in graph
                and type(value[1]) is int
            ):
                pending.append(value[0])
    return reached


def output_nodes(graph):
    options = [
        (key, node["class_type"], OUTPUTS[node["class_type"]][0])
        for key, node in graph.items()
        if node["class_type"] in OUTPUTS
    ]
    for kind in ["model", "video", "audio", "image"]:
        chosen = [row for row in options if row[2] == kind]
        if not chosen:
            continue
        if kind == "model":
            return chosen
        if kind == "audio":
            preferred = [row for row in chosen if row[1] == "SaveAudioMP3"]
            return (preferred or chosen)[-1:]
        if kind == "video":
            preferred = [row for row in chosen if row[1] == "SaveVideo"]
            return (
                preferred or sorted(chosen, key=lambda row: int(row[0]) if row[0].isdigit() else 0)
            )[-1:]
        return chosen[-1:]
    return []


def recipe(gpu, graph, source, final):
    final_id, output_class, kind = final
    save_context = (
        [
            key
            for key, node in graph.items()
            if node["class_type"] == "MiniMaxH3MotionContextSaveLatent"
        ]
        if kind == "video"
        else []
    )
    reached = ancestors(graph, [final_id, *save_context])
    frozen = {
        key: {"class_type": graph[key]["class_type"], "inputs": deepcopy(graph[key]["inputs"])}
        for key in graph
        if key in reached
    }
    bindings, media, texts, review = [], [], [], []

    def bind(source, key, field):
        bindings.append({"source": source, "node_id": key, "input_name": field})
        frozen[key]["inputs"][field] = "" if source != "seed" else 0

    def text(name, key, field, label, description):
        name = name[:32]
        if not any(slot["name"] == name for slot in texts):
            texts.append(
                {
                    "name": name,
                    "label": label,
                    "description": description,
                    "required": True,
                    "max_length": 8000,
                }
            )
        bind("text:" + name, key, field)

    def nested_text(name, key, field, pointer, label, description):
        texts.append(
            {
                "name": name,
                "label": label,
                "description": description,
                "required": True,
                "max_length": 8000,
            }
        )
        bindings.append(
            {"source": "text:" + name, "node_id": key, "input_name": field, "pointer": pointer}
        )

    for key, node in frozen.items():
        cls, values = node["class_type"], node["inputs"]
        if cls in MEDIA_CLASSES:
            media_kind, field = MEDIA_CLASSES[cls]
            if field not in values or isinstance(values[field], list):
                review.append("input_requires_explicit_adapter:" + cls + "." + field)
                continue
            roles = []
            for child in frozen.values():
                for name, link in child["inputs"].items():
                    if isinstance(link, list) and len(link) == 2 and link[0] == key:
                        roles.append(name)
            role = next(
                (
                    name
                    for name in ["first_frame", "last_frame", "front", "back", "left", "right"]
                    if name in roles
                ),
                None,
            )
            if media_kind == "model":
                role = "coarse_mesh" if cls.startswith("UltraShape") else "source_mesh"
            elif media_kind == "video":
                role = (
                    "source_video"
                    if not any(slot["kind"] == "video" for slot in media)
                    else "reference_video_" + key
                )
            elif media_kind == "audio":
                role = "voice_" + key
            elif kind == "model":
                if "Pixal3D-MV" in source:
                    role = {"1": "front", "60": "left", "63": "back", "66": "right"}.get(key)
                elif "MV-SAM3D" in source or "UniForge3D" in source:
                    role = {"10": "front", "12": "right", "14": "back", "16": "left"}.get(key)
                else:
                    role = "source_image"
            role = role or ("reference_image_" + key)
            if any(slot["name"] == role for slot in media):
                role += "_" + key
            from generation_domain.workflow_inputs import INPUT_KINDS

            media.append(
                {
                    "name": role,
                    "kind": media_kind,
                    "label": role.replace("_", " "),
                    "description": " / ".join(roles)[:600]
                    + "；"
                    + {
                        "image": "上传此角色的静态图像。",
                        "video": "上传动作或内容参考视频；声音以本模型正式策略为准。",
                        "audio": "上传该说话者的参考音频；需与文字转写对应。",
                        "model": "上传自包含静态 GLB，作为网格精化或贴图的输入。",
                    }[media_kind],
                    "required": True,
                    "max_bytes": INPUT_KINDS[media_kind][0],
                    "mime_types": sorted(INPUT_KINDS[media_kind][1]),
                    "path_mode": "comfy_absolute" if media_kind == "model" else "comfy_relative",
                }
            )
            bind("media:" + role, key, field)
        for field in ["seed", "noise_seed"]:
            if type(values.get(field)) is int:
                bind("seed", key, field)
        for field in TEXT_FIELDS.get(cls, []):
            if not isinstance(values.get(field), str):
                continue
            if (
                cls == "PodcastTrioNative"
                and field in ("S1_text", "S2_text")
                and not isinstance(values.get(field[:2] + "_audio"), list)
            ):
                values.pop(field, None)
                continue
            if cls == "CLIPTextEncode":
                consumers = [
                    name
                    for child in frozen.values()
                    for name, value in child["inputs"].items()
                    if isinstance(value, list) and value == [key, 0]
                ]
                if consumers and all(name == "negative" for name in consumers):
                    bind("negative_prompt", key, field)
                    continue
            if field == "negative_prompt":
                bind("negative_prompt", key, field)
            elif field in (
                "style",
                "lyrics",
                "abc",
                "dialogue_text",
                "dialogue_script",
                "S1_text",
                "S2_text",
            ):
                role = {"dialogue_text": "dialogue", "dialogue_script": "dialogue"}.get(
                    field, field.lower()
                )
                text(
                    role,
                    key,
                    field,
                    role,
                    "曲谱采用 ABC 记谱；歌词与风格分别传入。"
                    if field == "abc"
                    else "传入该工作流的" + role + "文本；不作为文件路径或命令执行。",
                )
            elif field in ("prompt", "text", "value"):
                text(
                    "prompt_" + key,
                    key,
                    field,
                    "提示文本 " + key,
                    "传入此段或此编码器的提示文本；按本模型说明分别填写。",
                )
        if cls == "SoulXPodcastInputParser":
            values["input_mode"] = "json"
            speakers = {}
            for speaker in ["S1", "S2"]:
                if isinstance(values.get(speaker + "_prompt_audio"), list):
                    speakers[speaker] = {"prompt_text": ""}
                    nested_text(
                        "speaker_" + speaker[-1] + "_transcript",
                        key,
                        "json_config",
                        "/speakers/" + speaker + "/prompt_text",
                        speaker + " 参考录音转写 / Reference transcript",
                        "逐字填写该说话者上传参考录音的实际内容。This transcript must match the reference audio; generation dialogue is supplied separately.",
                    )
            values["json_config"] = json.dumps({"speakers": speakers})
        if cls == "MVSAM3DRunMultiView":
            bind("output_basename", key, "filename_prefix")
        if cls == "easy multiTrackEditor":
            timeline = json.loads(values["track_data"])
            paths = {}
            count = 0
            for track_index, track in enumerate(timeline.get("tracks", [])):
                for segment_index, segment in enumerate(track.get("segments", [])):
                    content = segment.get("content", {})
                    pointer = f"/tracks/{track_index}/segments/{segment_index}/content"
                    if isinstance(content.get("user_prompt"), str):
                        count += 1
                        content["user_prompt"] = ""
                        nested_text(
                            "segment_" + str(count) + "_prompt",
                            key,
                            "track_data",
                            pointer + "/user_prompt",
                            f"第 {count} 段 / Segment {count}",
                            f"第 {count} 个冻结时间段的生成提示。Text for the corresponding frozen timeline segment.",
                        )
                    for image_index, image in enumerate(content.get("images", [])):
                        path = image.get("file_path")
                        if not isinstance(path, str):
                            raise ValueError("timeline_image_path_required")
                        if path not in paths:
                            role = "timeline_image_" + str(len(paths) + 1)
                            paths[path] = role
                            media.append(
                                {
                                    "name": role,
                                    "kind": "image",
                                    "label": f"时间线参考图 {len(paths)} / Timeline reference",
                                    "description": "所有段落共用此角色或场景参考图；每个出现位置均实际绑定。Shared reference across the declared timeline segments.",
                                    "required": True,
                                    "max_bytes": 20 * 1024**2,
                                    "mime_types": ["image/png", "image/jpeg", "image/webp"],
                                    "path_mode": "comfy_relative",
                                }
                            )
                        role = paths[path]
                        image["source_type"] = "input"
                        image["file_path"] = ""
                        bindings.append(
                            {
                                "source": "media:" + role,
                                "node_id": key,
                                "input_name": "track_data",
                                "pointer": pointer + f"/images/{image_index}/file_path",
                            }
                        )
                        if "file_name" in image:
                            image["file_name"] = ""
                            bindings.append(
                                {
                                    "source": "media:" + role,
                                    "node_id": key,
                                    "input_name": "track_data",
                                    "pointer": pointer + f"/images/{image_index}/file_name",
                                }
                            )
            values["track_data"] = json.dumps(timeline, ensure_ascii=False)
        if cls in ("UTXLoadMeshV2", "NativeWindows3D"):
            review.append("native_auxiliary_dependencies")
        if cls == "MiniMaxH3MotionContextLoadLatent":
            bind("context_latent", key, "latent_path")
    for key in [final_id, *save_context]:
        values = frozen[key]["inputs"]
        field = next(
            (
                field
                for field in ["filename_prefix", "output_prefix", "prefix"]
                if isinstance(values.get(field), str)
            ),
            None,
        )
        if field:
            bind("output_prefix", key, field)
        elif frozen[key]["class_type"] != "MVSAM3DExport":
            review.append("output_prefix_adapter_required:" + frozen[key]["class_type"])
    if kind != "model":
        primary = [slot for slot in texts if slot["name"].startswith("prompt_")]
        if len(primary) == 1:
            name = primary[0]["name"]
            texts = [slot for slot in texts if slot["name"] != name]
            for binding in bindings:
                if binding["source"] == "text:" + name:
                    binding["source"] = "prompt"
    for slot in texts:
        if slot["name"] in ("lyrics", "abc"):
            slot["required"] = False
    # Normalize only declared request values; graph topology, weights and presets stay significant.
    frozen = normalize_numbers(frozen)
    bindings.sort(key=lambda item: (item["node_id"], item["input_name"]))
    media.sort(key=lambda item: item["name"])
    texts.sort(key=lambda item: item["name"])
    semantic = sha(
        {"graph": frozen, "bindings": bindings, "media": media, "texts": texts, "final": final_id}
    )
    return {
        "gpu": gpu,
        "semantic_sha256": semantic,
        "workflow_api": frozen,
        "bindings": bindings,
        "input_slots": media,
        "text_slots": texts,
        "output_kind": kind,
        "final_node_id": final_id,
        "history_field": OUTPUTS[output_class][1],
        "output_class": output_class,
        "source": source,
        "review": sorted(set(review)),
        "context_save_nodes": save_context,
    }


items, mapping, blocked = {}, [], []
for gpu in ["3080", "4090"]:
    directory = AUDIT / gpu
    records = json.loads((directory / "analysis-private.json").read_bytes())
    for row in records:
        if row.get("not_a_workflow"):
            continue
        source_id = gpu + ":" + sha({"path": row["path"], "sha256": row["source_sha256"]})
        if not row["dependency_check_passed"]:
            blocked.append(
                {
                    "gpu": gpu,
                    "source_id": source_id,
                    "path": row["path"],
                    "reason": {
                        name: row[name]
                        for name in [
                            "missing_classes",
                            "invalid_choices",
                            "missing_required",
                            "invalid_links",
                        ]
                        if row.get(name)
                    },
                }
            )
            mapping.append(
                {
                    "gpu": gpu,
                    "source_id": source_id,
                    "path": row["path"],
                    "status": "source_blocked",
                    "targets": [],
                }
            )
            continue
        path = directory / "api-exports" / row["path"]
        if not path.exists() and row["export"] == "paired_api_audited_separately":
            path = path.with_suffix(".api.json")
        graph = json.loads(path.read_bytes())
        targets = []
        for final in output_nodes(graph):
            candidate = recipe(gpu, graph, row["path"], final)
            key = gpu + "-" + candidate["semantic_sha256"][:16]
            value = items.setdefault(
                key, {**candidate, "source_files": [], "source_ids": [], "status": "recipe_review"}
            )
            value["source_files"].append(row["path"])
            value["source_ids"].append(source_id)
            targets.append(key)
        mapping.append(
            {
                "gpu": gpu,
                "source_id": source_id,
                "path": row["path"],
                "status": "recipe_review" if targets else "adapter_review",
                "targets": targets,
            }
        )

plan = {
    "source_workflows": len(mapping),
    "static_pass_files": sum(row["status"] != "source_blocked" for row in mapping),
    "source_blocked_files": len(blocked),
    "candidate_recipes": len(items),
    "mapping": mapping,
    "models": items,
    "blocked": blocked,
}
(DEST / "plan-private.json").write_text(
    json.dumps(plan, ensure_ascii=False, indent=2) + "\n", "utf-8"
)
print(
    json.dumps(
        {
            key: plan[key]
            for key in [
                "source_workflows",
                "static_pass_files",
                "source_blocked_files",
                "candidate_recipes",
            ]
        },
        ensure_ascii=False,
    )
)
print("Kinds", dict(Counter(item["output_kind"] for item in items.values())))
print("Reviews", dict(Counter(reason for item in items.values() for reason in item["review"])))
for key, item in items.items():
    print(
        key,
        item["source"],
        item["output_kind"],
        len(item["source_files"]),
        [(slot["name"], slot["kind"]) for slot in item["input_slots"]],
        "text",
        [slot["name"] for slot in item["text_slots"]],
        "review",
        item["review"],
    )
