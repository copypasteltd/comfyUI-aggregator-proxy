"""Public descriptions derived from a frozen graph, without exposing that graph."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def media_consumers(graph: dict, source: str) -> list[tuple[str, str]]:
    """Follow image transforms; keep semantic consumers and their actual field names."""
    pending, seen, result = [source], set(), []
    transforms = (
        "Resize",
        "Scale",
        "Crop",
        "Batch",
        "RemoveBackground",
        "ImageFrom",
        "ImageToMask",
        "GetVideoComponents",
        "MaskToImage",
        "InvertMask",
    )
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        for child_id, node in graph.items():
            for field, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2 and value[0] == parent:
                    if any(word in node["class_type"] for word in transforms):
                        pending.append(child_id)
                    else:
                        result.append((node["class_type"], field))
    return sorted(set(result))


def describe_media_slots(item: dict) -> None:
    graph = item["workflow_api"]
    renames, assigned = {}, set()
    labels = {
        "first_frame": ("首帧", "First frame"),
        "last_frame": ("尾帧", "Last frame"),
        "first_last_frame": ("首尾共用图", "Shared first/last frame"),
        "source_image": ("输入图像", "Source image"),
        "source_video": ("源视频", "Source video"),
        "source_mesh": ("输入网格", "Source mesh"),
        "coarse_mesh": ("待精化网格", "Coarse mesh"),
        "texture_reference": ("贴图参考图", "Texture reference"),
        "front": ("正视图", "Front view"),
        "back": ("背视图", "Back view"),
        "left": ("左视图", "Left view"),
        "right": ("右视图", "Right view"),
    }
    counters = {"image": 0, "audio": 0, "video": 0, "model": 0}
    for slot in item["input_slots"]:
        kind, old = slot["kind"], slot["name"]
        counters[kind] += 1
        binding = next(b for b in item["bindings"] if b["source"] == "media:" + old)
        uses = media_consumers(graph, binding["node_id"]) if not binding.get("pointer") else []
        fields = {field for _, field in uses}
        role = old
        if kind == "audio":
            role = (
                "speaker_1"
                if any(f.startswith("S1") for f in fields)
                else (
                    "speaker_2"
                    if any(f.startswith("S2") for f in fields)
                    else f"reference_audio_{counters[kind]}"
                )
            )
        elif kind == "image" and not old.startswith("timeline_image_"):
            if {"first_frame", "last_frame"} <= fields:
                role = "first_last_frame"
            elif "first_frame" in fields:
                role = "first_frame"
            elif "last_frame" in fields:
                role = "last_frame"
            elif any(cls == "UTXPrepareTextureV25" and field == "reference" for cls, field in uses):
                role = "texture_reference"
            elif not (item["output_kind"] == "model" and old in labels):
                reference = next(
                    (
                        re.search(r"(?:ref_image_|image[_]?)(\d+)$", f)
                        for f in sorted(fields)
                        if re.search(r"(?:ref_image_|image[_]?)(\d+)$", f)
                    ),
                    None,
                )
                if reference:
                    number = int(reference[1]) + (
                        1 if any(f.startswith("ref_images.") for f in fields) else 0
                    )
                    role = f"reference_image_{number}"
                elif len([s for s in item["input_slots"] if s["kind"] == "image"]) == 1:
                    role = "source_image"
                else:
                    role = f"reference_image_{counters[kind]}"
        base = role
        suffix = 2
        while role in assigned:
            role = f"{base}_{suffix}"
            suffix += 1
        assigned.add(role)
        renames[old] = role
        slot["name"] = role
        zh, en = labels.get(role, (role.replace("_", " "), role.replace("_", " ").title()))
        slot["label"] = f"{zh} / {en}"
        if kind == "audio":
            slot["description"] = (
                f"上传 {role} 的参考录音；若要求转写，必须逐字匹配该录音，不能填待生成台词。"
                "Upload this speaker/reference recording. A required transcript must match the recording; generated dialogue is separate."
            )
        elif kind == "video":
            slot["description"] = (
                "上传动作、场景或内容参考视频；声音按模型的声音策略处理。参考视频应覆盖所声明的输出时长。"
                "Upload a performance/content reference covering the advertised duration. The model audio policy determines the delivered soundtrack."
            )
        elif kind == "model":
            slot["description"] = (
                "上传自包含静态 GLB；此槽位用于几何精化或贴图。外部纹理、脚本与文件路径不受理。"
                "Upload a self-contained static GLB for refinement or texturing. External resources and caller file paths are not accepted."
            )
        elif role.startswith("timeline_image_"):
            slot["description"] = (
                "时间线内各段共用此图；所有出现位置均绑定同一素材。"
                "This image is shared by its declared timeline segments; all occurrences use this asset."
            )
        else:
            slot["description"] = (
                f"上传{zh}。多视图须是同一物体、相同外观和一致背景；首尾帧按标签匹配。"
                f"Upload the {en.lower()}. Multi-view images must depict the same object consistently; first/last anchors follow their labels."
            )
    for binding in item["bindings"]:
        if binding["source"].startswith("media:"):
            binding["source"] = "media:" + renames[binding["source"][6:]]


def public_documentation(item: dict, manifest: dict) -> dict:
    graph = manifest["workflow_api"]
    classes = {node["class_type"] for node in graph.values()}
    families = (
        ("MiniMax H3", "h3", "MiniMaxH3"),
        ("YuE2", "yue2", "YuE2"),
        ("SoulX Podcast", "soulx", "SoulX"),
        ("Podcast Trio", "podcast", "PodcastTrio"),
        ("Qwen Image", "qwen", "Qwen"),
        ("Pixal3D", "pixal3d", "Pixal3D"),
        ("Trellis2", "trellis2", "Trellis2"),
        ("Hunyuan3D", "hunyuan3d", "Hunyuan3D"),
        ("SAM 3D", "sam3d", "SAM3D"),
        ("UniForge3D", "uniforge", "UniForge3D"),
        ("UltraShape", "ultrashape", "UltraShape"),
        ("UniTexture", "unitexture", "UTX"),
        ("Native 3D", "native3d", "NativeWindows3D"),
        ("Viggle", "viggle", "Viggle"),
    )
    family, tag = next(
        ((name, slug) for name, slug, prefix in families if any(prefix in c for c in classes)),
        ("ComfyUI", "comfy"),
    )
    if "easy multiTrackEditor" in classes:
        family, tag = "MiniMax H3 Timeline", "h3-timeline"
    if family == "Podcast Trio":
        models = {
            node["inputs"].get("model") for node in graph.values()
            if node["class_type"] == "PodcastTrioNative"
            and isinstance(node["inputs"].get("model"), str)
        }
        podcast_names = {
            "MOSS-TTSD-v1.0": ("MOSS-TTSD v1.0", "moss-ttsd"),
            "FireRedTTS2": ("FireRedTTS2", "fireredtts2"),
        }
        if len(models) == 1 and (model := next(iter(models))) in podcast_names:
            family, tag = podcast_names[model]
    if family == "ComfyUI":
        model_names = str(manifest["dependency_lock"]["models"]).lower()
        for marker, name, slug in (("illustrious", "Illustrious", "illustrious"), ("flux", "FLUX", "flux"), ("qwen", "Qwen Image", "qwen"), ("z_image", "Z Image", "z-image")):
            if marker in model_names:
                family, tag = name, slug
                break
    if family == "Native 3D":
        for marker in ("PartCrafter", "TripoSG", "Hunyuan3D", "InstantMesh"):
            if marker.lower() in str(graph).lower():
                family, tag = marker, marker.lower()
                break
    kind = item["output_kind"]
    if kind == "image":
        checkpoints = [
            model["path"].replace("\\", "/").rsplit("/", 1)[-1].lower()
            for model in manifest.get("dependency_lock", {}).get("models", [])
            if any(
                "/" + category + "/" in "/" + model["path"].replace("\\", "/").lower()
                for category in ("checkpoints", "diffusion_models", "unet")
            )
        ]
        if len(checkpoints) == 1:
            for prefix, name, slug in (
                ("noctq_v4", "NoctQ v4", "noctq"),
                ("lustifynsfwcheckpoint_v10krea2", "Lustify v10 Krea2", "lustify"),
                ("waiillustrioussdxl_v170", "WAI Illustrious SDXL v17", "illustrious"),
                ("qwen_image_2.1", "Qwen Image 2.1", "qwen"),
                ("dasiwaminimaxh3_dasiwahybridv2", "Dasiwa H3 Hybrid v2", "h3"),
            ):
                if checkpoints[0].startswith(prefix):
                    family, tag = name, slug
                    break
    types = {
        "model": "图像/网格转三维 / Image or mesh to 3D",
        "video": "视频生成 / Video generation",
        "image": "图像生成/编辑 / Image generation/editing",
        "audio": "音乐/语音生成 / Music or speech generation",
    }
    title = (
        Path(item["source"])
        .name.removesuffix(".json")
        .removesuffix(".api")
        .removesuffix(".workflow")
    )
    title = re.sub(r"作者原始参数|【原生成参数·原生出片】", "", title).strip("_-")
    slots = manifest.get("input_slots", [])
    roles = {slot["name"] for slot in slots}
    kinds = {slot["kind"] for slot in slots}
    operation, operation_en = {
        "text_to_image": ("文生图", "Text to image"),
        "image_to_image": ("图像编辑", "Image editing"),
        "references_to_image": ("多参考生图", "Reference image generation"),
        "text_to_video": ("文生视频", "Text to video"),
        "references_to_video": ("参考生视频", "Reference video generation"),
        "text_to_audio": ("音乐生成" if tag == "yue2" else "语音生成", "Music generation" if tag == "yue2" else "Speech generation"),
        "image_to_3d": ("图像转三维", "Image to 3D"),
    }[manifest["task_type"]]
    if kind == "video":
        if (manifest.get("context") or {}).get("load"):
            operation, operation_en = "视频续拍", "Video continuation"
        elif "video" in kinds:
            operation, operation_en = "视频参考生成", "Video reference generation"
        elif {"first_frame", "last_frame"} <= roles:
            operation, operation_en = "首尾帧生视频", "First/last frame video"
        elif "first_last_frame" in roles:
            operation, operation_en = "首尾共图生视频", "Shared first/last frame video"
        elif sum(slot["kind"] == "image" for slot in slots) > 1:
            operation, operation_en = "多图参考生视频", "Multiple image reference video"
        elif "image" in kinds:
            operation, operation_en = "图生视频", "Image to video"
        elif "audio" in kinds:
            operation, operation_en = "音频参考生视频", "Audio reference video"
    elif kind == "model":
        if "model" in kinds:
            operation, operation_en = ("网格贴图", "Mesh texturing") if tag == "unitexture" else ("网格精化", "Mesh refinement")
        elif len(slots) > 1:
            operation, operation_en = "多视图转三维", "Multiple views to 3D"
    elif kind == "audio" and {"speaker_1", "speaker_2"} <= roles:
        operation, operation_en = "双人对话语音", "Two speaker dialogue"
    elif kind == "audio" and roles & {"speaker_1", "speaker_2"}:
        operation, operation_en = "单人语音", "Single speaker speech"
    preset = manifest["presets"][0]
    specification = " / ".join(str(preset[field]) + ("s" if field == "seconds" else "") for field in ("size", "seconds") if field in preset)
    variant = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:6]
    notes = [
        "异步调用：上传素材 → 按槽位提交任务 → 查询状态/排队 → 下载结果。Async: upload assets, submit named slots, poll status/queue, download content.",
        "仅接受声明的固定规格；n=1，quality=standard。Only advertised frozen presets are accepted; n=1 and quality=standard.",
    ]
    notes.append("源工作流 / Source workflow: " + title)
    if specification:
        notes.append("冻结输出规格 / Frozen output specification: " + specification)
    if kind == "image":
        final_nodes = manifest["output"].get("final_node_ids", [])
        delivery_scales = []
        for node_id in final_nodes:
            link = graph[node_id]["inputs"].get("images")
            node = graph.get(link[0]) if isinstance(link, list) and len(link) == 2 and isinstance(link[0], str) else None
            if node and node["class_type"] == "ImageScale":
                values = node["inputs"]
                delivery_scales.append(
                    values.get("upscale_method") == "lanczos" and values.get("crop") == "center"
                    and f"{values.get('width')}x{values.get('height')}" == preset.get("size")
                )
            else:
                delivery_scales.append(False)
        if delivery_scales and all(delivery_scales):
            notes.append(
                "声明尺寸指最终交付：末端使用 Lanczos 缩放与居中裁切；上游原生采样尺寸可能跟随参考图。"
                "The declared size is the delivered canvas: Lanczos resize and center crop are applied at output; native sampling size may follow the reference."
            )
        else:
            notes.append(
                "声明尺寸是冻结的交付规格；此版本未声明末端 Lanczos 缩放或居中裁切。"
                "The declared size is the frozen delivery specification; this version does not declare a final Lanczos resize or center crop."
            )
    if manifest.get("input_slots"):
        notes.append(
            "素材槽位 / Media slots: "
            + ", ".join(s["name"] + " (" + s["kind"] + ")" for s in manifest["input_slots"])
        )
    if manifest.get("text_slots"):
        notes.append(
            "文本槽位 / Text slots: "
            + ", ".join(s["name"] + ("*" if s["required"] else "?") for s in manifest["text_slots"])
            + "; *=required / 必填, ?=optional / 选填."
        )
    if kind == "audio":
        notes.append(
            "seconds 是最大输出时长；自然结束可以更短。seconds caps output duration; natural speech/music may end earlier."
        )
    if any("Podcast" in c for c in classes):
        speakers = [label for role, label in (("speaker_1", "[S1]"), ("speaker_2", "[S2]")) if role in roles]
        supported = ", ".join(speakers) if speakers else "已声明的说话者 / declared speakers"
        notes.append(
            "此模型的说话者标记 / Speaker labels for this model: " + supported + ". "
            "每行只使用已提供参考录音的说话者；参考音频和实际转写按说话者配对，生成台词另填。"
            "Each dialogue line must use a speaker with a supplied reference recording. Pair each reference voice with its exact transcript; supply generated dialogue separately."
        )
    if any("YuE2" in c for c in classes):
        notes.append(
            "style 填风格；lyrics 填歌词或留空生成纯音乐；abc 为 ABC 记谱，带自动作谱节点的版本由风格/歌词生成曲谱。Supply style and optional lyrics; abc uses ABC notation. Score-generator variants derive the score from style/lyrics."
        )
    if policy := manifest["output"].get("audio_policy"):
        notes.append("声音策略 / Audio policy: " + policy["mode"] + ". " + policy["description"])
    if context := manifest.get("context"):
        notes.append(
            "续拍须传 input.context.job_id，使用同一 Key、项目和兼容规格的已成功父任务；上下文保留8小时。Continuation needs a successful compatible parent from the same Key/project via input.context.job_id; context retention is 8 hours."
            if context["load"]
            else "此版本保存续拍上下文，可作为同规格续拍模型的父任务，保留8小时。This version saves context for a compatible continuation model for 8 hours."
        )
    if kind == "model":
        notes.append(
            "输出自包含 GLB；几何、顶点色和 PBR 材质版本按独立模型区分，不保证未声明的贴图。Returns a self-contained GLB; geometry, vertex-color and PBR variants are separate models."
        )
        active = set()
        pending = list(manifest["output"]["final_node_ids"])
        while pending:
            node_id = pending.pop()
            if node_id in active:
                continue
            active.add(node_id)
            pending.extend(
                value[0] for value in graph[node_id]["inputs"].values()
                if isinstance(value, list) and len(value) == 2
                and isinstance(value[0], str) and value[0] in graph
            )
        exported = {graph[node_id]["class_type"] for node_id in active}
        if {"ApplyTextureToMesh", "BakeTextureFromVoxel"} <= exported:
            operation += " · UV/PBR GLB"
            notes.append(
                "此变体烘焙 UV 基础色和 PBR 通道；原生节点在缺少 PBR 场时会输出黑色金属度/粗糙度，不能保证材质质量。"
                "This variant bakes UV base color and PBR channels. Missing source PBR fields bake black metallic/roughness maps; material quality is not guaranteed."
            )
        elif "PaintMesh" in exported:
            operation += " · 顶点色 GLB"
            notes.append(
                "此变体通过 PaintMesh 输出顶点色，不承诺 UV 贴图或 PBR 材质。"
                "This variant exports PaintMesh vertex colors; UV textures and PBR materials are not promised."
            )
        elif "UTXFastSurfaceTextureBaker" in exported:
            operation += " · UV 贴图 GLB"
            notes.append(
                "此变体把生成视图烘焙到输入网格的 UV 贴图；不承诺额外 PBR 通道。"
                "This variant bakes generated views onto the input mesh's UV texture; additional PBR channels are not promised."
            )
        elif "NativeWindows3D" in exported or "VAEDecodeHunyuan3D" in exported or "UltraShapeConvertToGLB" in exported:
            operation += " · 几何 GLB"
            notes.append(
                "此变体提供几何网格；不承诺顶点色、UV 贴图或 PBR 材质。"
                "This variant provides mesh geometry; vertex colors, UV textures and PBR materials are not promised."
            )
    return {
        "display_name": f"{family} · {operation}" + (" · " + specification if specification else "") + f" · RTX {item['gpu']} · {variant}",
        "description": f"{operation} / {operation_en}；{family}，RTX {item['gpu']}。{types[kind]}。{title}。按公开 usage 中的槽位、规格与示例调用。Use this model's public usage schema for exact slots, presets and examples.",
        "notes": [note[:500] for note in notes],
        "tags": [tag, kind, "rtx-" + item["gpu"], "async", "workflow-inputs-v1"],
        "example_prompt": "A small blue ceramic teapot on a plain background."
        if any(b["source"] == "prompt" for b in manifest["bindings"])
        else "",
    }
