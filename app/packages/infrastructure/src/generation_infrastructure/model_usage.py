from __future__ import annotations

from copy import deepcopy
from typing import Any

from generation_contracts.discovery import PublicPreset
from generation_contracts.model_usage import ModelDocumentation, ModelUsage
from generation_contracts.validation import JobCreate, validator
from generation_domain.media_types import task_media_kind
from generation_domain.workflows import matching_presets, validate_reference_inputs
from jsonschema import Draft202012Validator

TASK_DESCRIPTIONS = {
    "text_to_image": "根据文字提示生成图片 / Generate an image from text.",
    "image_to_image": "根据一张输入图片和文字重绘 / Edit one source image using a text prompt.",
    "references_to_image": "根据有序参考图片和文字生成图片 / Generate an image using ordered reference images and text.",
    "text_to_video": "根据文字提示生成视频 / Generate a video from text.",
    "references_to_video": "根据有序参考图片和文字生成视频 / Generate a video using ordered reference images and text.",
    "image_to_3d": "根据一张物体图片生成自包含 GLB 三维网格 / Generate a self-contained GLB mesh from one image.",
    "text_to_audio": "通过 ComfyUI Workflow 生成音频 / Generate audio through a ComfyUI Workflow.",
}
PARAMETER_DESCRIPTIONS = {
    "prompt": "必填文字提示；音乐/音频使用该模型说明约定的文本格式。",
    "negative_prompt": "反向提示词；仅在本模型声明支持时传入 input。",
    "size": "宽x高，例如 720x1080；只能选择 presets 中完整的规格组合。",
    "quality": "质量档位；必须与尺寸/时长匹配同一条已发布 preset。",
    "seconds": "预设的生成时长，单位秒；不能指定 presets 以外的时长。",
    "seed": "随机种子；省略由服务固定生成，幂等重放保持同一生成请求。",
    "n": "每次任务的主产物数量，目前固定为 1。",
    "fps": "Workflow 支持时可指定的帧率；最终媒体规格以实际产物为准。",
    "strength": "Workflow 已绑定的图像变换强度，范围 0～1。",
}
PUBLIC_PRESET_FIELDS = {"quality", "size", "seconds", "audio_format", "sample_rate", "channels"}


def model_usage(alias: str, task_type: str, manifest: dict[str, Any], defaults: dict[str, Any], documentation: dict[str, Any]) -> dict[str, Any]:
    """Project only public contract values; never copy graph or dependency metadata."""
    text = ModelDocumentation.model_validate(documentation)
    sources = {binding["source"] for binding in manifest["bindings"]}
    modern = manifest.get("format_version") == "1.3"
    supported = set(manifest["supported_parameters"])
    presets = [PublicPreset.model_validate({k: v for k, v in preset.items() if k in PUBLIC_PRESET_FIELDS}).model_dump(exclude_none=True) for preset in manifest["presets"]]
    if set(defaults) - {"size", "quality", "seconds", "n"}:
        raise ValueError("Unexpected model defaults")
    if len(matching_presets(manifest, defaults)) != 1:
        raise ValueError("Public model defaults do not select a unique frozen preset")
    source = validator("job-create").schema
    if not isinstance(source, dict):
        raise ValueError("Native request schema must be an object")
    schema: dict[str, Any] = deepcopy(source)
    schema.pop("contract_version", None)
    schema["title"] = alias + " generation request"
    properties = schema["properties"]
    properties["model"] = {"const": alias}
    properties["task_type"] = {"const": task_type}
    inputs = properties["input"]
    input_names = ({"prompt"} if "prompt" in sources else set()) if modern else set() if task_type == "image_to_3d" else {"prompt"}
    inputs["required"] = sorted(input_names)
    if "negative_prompt" in supported:
        input_names.add("negative_prompt")
    example_input: dict[str, Any] = {"prompt": text.example_prompt.strip() or ("A quiet garden in soft morning light" if task_type != "text_to_audio" else "An original gentle instrumental melody")}
    if task_type == "image_to_3d" or modern and "prompt" not in sources:
        example_input = {}
    slots = []
    reference_count = sum(f"reference_{i}" in sources for i in range(4))
    if task_type in ("image_to_image", "image_to_3d") and (not modern or "source_image" in sources):
        input_names.add("source_image")
        inputs["required"].append("source_image")
        example_input["source_image"] = {"asset_id": "asset_REPLACE_WITH_UPLOADED_ID"}
        slots.append({"path": "input.source_image", "required": True, "description": "生成依据的源图片；先上传并使用返回的 asset_id。"})
        if "mask" in supported and "mask" in sources:
            input_names.add("mask")
            slots.append({"path": "input.mask", "required": False, "description": "可选遮罩图片；仅此模型的绑定支持时传入。"})
    if task_type.startswith("references_") and (not modern or reference_count):
        if not reference_count:
            raise ValueError("Reference workflow has no bound reference slots")
        input_names.add("references")
        inputs["required"].append("references")
        references = inputs["properties"]["references"]
        item = references.pop("items")
        references.update(minItems=reference_count, maxItems=reference_count, prefixItems=[], items=False)
        example_input["references"] = []
        for index in range(reference_count):
            weight_supported = f"reference_weight_{index}" in sources
            slot_schema = deepcopy(item)
            if not weight_supported:
                slot_schema["properties"].pop("weight")
            first_frame = task_type == "references_to_video" and reference_count == 1
            if first_frame:
                slot_schema["properties"]["role"]["const"] = "first_frame"
            references["prefixItems"].append(slot_schema)
            slots.append({"path": f"input.references[{index}]", "required": True, "description": "视频首帧图片，role 必须为 first_frame。" if first_frame else f"第 {index + 1} 张参考图，数组顺序与 Workflow 槽位一致；role 为语义标签，不改变槽位顺序。", "weight_supported": weight_supported})
            example_input["references"].append({"asset_id": "asset_REPLACE_WITH_UPLOADED_ID_" + str(index + 1), "role": "first_frame" if first_frame else "reference_" + str(index)})
    if modern and manifest.get("input_slots"):
        input_names.add("media")
        media = inputs["properties"]["media"]
        media["maxItems"] = len(manifest["input_slots"])
        media["items"]["properties"]["slot"]["enum"] = [slot["name"] for slot in manifest["input_slots"]]
        media["allOf"] = [{"contains": {"properties": {"slot": {"const": slot["name"]}}, "required": ["slot"]}, "minContains": 1 if slot["required"] else 0, "maxContains": 1} for slot in manifest["input_slots"]]
        required_media = [slot for slot in manifest["input_slots"] if slot["required"]]
        if required_media:
            inputs["required"].append("media")
            example_input["media"] = [{"slot": slot["name"], "asset_id": "asset_REPLACE_WITH_UPLOADED_" + slot["name"].upper()} for slot in required_media]
    if modern and manifest.get("text_slots"):
        input_names.add("texts")
        texts = inputs["properties"]["texts"]
        texts["additionalProperties"] = False
        texts["properties"] = {slot["name"]: {"type": "string", "minLength": 1, "maxLength": slot["max_length"]} for slot in manifest["text_slots"]}
        texts["required"] = [slot["name"] for slot in manifest["text_slots"] if slot["required"]]
        if texts["required"]:
            inputs["required"].append("texts")
            example_input["texts"] = {slot["name"]: (text.example_prompt.strip() or "Example text")[:slot["max_length"]] for slot in manifest["text_slots"] if slot["required"]}
    context = manifest.get("context")
    if modern and context and context["load"]:
        input_names.add("context")
        inputs["required"].append("context")
        example_input["context"] = {"job_id": "job_" + "0" * 32}
    inputs["properties"] = {k: v for k, v in inputs["properties"].items() if k in input_names}
    if "mask" not in input_names:
        inputs.pop("dependentRequired", None)
    parameters = properties["parameters"]
    allowed = supported | set(defaults) | ({"n"} if modern else {"seed", "n"})
    parameters["properties"] = {k: v for k, v in parameters["properties"].items() if k in allowed}
    for name, rules in parameters["properties"].items():
        if name in defaults:
            rules["default"] = defaults[name]
        values = list(dict.fromkeys(p[name] for p in presets if name in p))
        if values:
            rules["enum"] = values
    combinations = []
    for preset in presets:
        fixed = {k: {"const": v} for k, v in preset.items() if k in ("size", "quality", "seconds")}
        branch: dict[str, Any] = {"properties": fixed}
        required = [k for k, v in preset.items() if k in fixed and defaults.get(k) != v]
        if required:
            branch["required"] = required
        combinations.append(branch)
    parameters["anyOf"] = combinations
    example_parameters = {**defaults, **({"seed": 42} if "seed" in allowed else {}), "n": 1}
    example = {"model": alias, "task_type": task_type, "input": example_input, "parameters": example_parameters}
    JobCreate.model_validate(example)
    Draft202012Validator.check_schema(schema)
    from generation_contracts.validation import ContractValidator

    ContractValidator(schema).validate(example)
    validate_reference_inputs(manifest, example_input)
    for preset in presets:
        sample = {**example, "parameters": {**{k: v for k, v in preset.items() if k in ("size", "quality", "seconds")}, **({"seed": 42} if "seed" in allowed else {}), "n": 1}}
        JobCreate.model_validate(sample)
    detail = [{"path": "input." + name, "required": name in inputs["required"], "description": PARAMETER_DESCRIPTIONS[name], "rules": deepcopy(rules)} for name, rules in inputs["properties"].items() if name in PARAMETER_DESCRIPTIONS]
    detail += [{"path": "parameters." + name, "required": False, "description": PARAMETER_DESCRIPTIONS[name], "rules": deepcopy(rules)} for name, rules in parameters["properties"].items()]
    scopes = ["models:read", "capabilities:read", "jobs:create", {"image": "images:create", "video": "videos:create", "audio": "audio:create", "model": "meshes:create"}[task_media_kind(task_type)], "jobs:read", "artifacts:read", "content:read", "content:download"]
    if slots or manifest.get("input_slots"):
        scopes.append("assets:upload")
    return ModelUsage.model_validate({
        "schema_version": "1.1" if modern else "1.0",
        "display_name": text.display_name.strip() or alias,
        "description": text.description.strip() or TASK_DESCRIPTIONS[task_type],
        "notes": text.notes, "tags": text.tags, "task_type": task_type,
        "required_scopes": scopes, "defaults": defaults, "parameter_details": detail,
        "input_slots": [{name: value for name, value in slot.items() if name != "path_mode"} for slot in manifest.get("input_slots", [])],
        "text_slots": manifest.get("text_slots", []),
        "context": {"family": context["family"], "required": context["load"], "produces": context.get("save_node_id") is not None, "compatibility": context["compatibility"]} if context else None,
        "references": {"supported": bool(slots), "exact_reference_count": reference_count,
                       "slots": slots, "media_types": ["image/png", "image/jpeg", "image/webp"] if slots else [],
                       "max_upload_bytes": 20 * 1024**2,
                       "note": "上传静态图片获得 asset_id；与生成使用同一个业务 Key。参考数组必须完整且有序，不能传图片 URL、本地路径或视频文件。"},
        "presets": presets, "request_schema": schema, "example_request": example,
        "output": {"media_types": manifest["output"]["mime_types"], "expected_count": manifest["output"]["expected_count"], "max_bytes": manifest["output"]["max_bytes"], "audio_policy": manifest["output"].get("audio_policy")},
        "availability_note": "目录表示当前 Key 可发现的已发布能力；设备在线状态、当前容量、额度及提交准入仍需实时检查。",
    }).model_dump(exclude_none=True)
