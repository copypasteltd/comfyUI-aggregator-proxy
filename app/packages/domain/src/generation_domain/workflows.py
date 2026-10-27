from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.media_types import (
    AUDIO_MIME_TYPES,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_MILLISECONDS,
    task_media_kind,
)
from generation_domain.values import fingerprint

TASK_TYPES = frozenset({"text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"})


def matching_presets(manifest: dict[str, Any], parameters: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in manifest["presets"] if p.get("size") == parameters.get("size") and p["quality"] == parameters.get("quality") and p.get("seconds") == parameters.get("seconds")]


def audio_specification(manifest: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    presets = matching_presets(manifest, parameters)
    if len(presets) != 1:
        raise DomainError("unsupported_specification", "音频规格没有唯一的已冻结预设", 422)
    preset = presets[0]
    return {name: preset[name] for name in ("audio_format", "sample_rate", "channels")}


def relative_file(value: str) -> str:
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if not value or "\\" in value or ":" in value or posix.is_absolute() or windows.is_absolute() or ".." in posix.parts or any(part in ("", ".") for part in value.split("/")):
        raise DomainError("unsafe_path", "文件路径必须是授权目录内的规范相对路径", 422)
    return value


def _scalar_input(graph: dict[str, Any], node_id: Any, input_name: Any) -> bool:
    node = graph.get(node_id) if isinstance(node_id, str) else None
    inputs = node.get("inputs") if isinstance(node, dict) else None
    if not isinstance(inputs, dict) or not isinstance(input_name, str) or input_name not in inputs:
        return False
    value = inputs[input_name]
    return value is None or type(value) in (str, int, float, bool)


def _binding_target(graph: dict[str, Any], binding: dict[str, Any]) -> tuple[str, str]:
    node_id, input_name = binding.get("node_id"), binding.get("input_name")
    if not isinstance(node_id, str) or not isinstance(input_name, str) or not _scalar_input(graph, node_id, input_name):
        raise DomainError("invalid_binding", "绑定只能写入已登记的节点标量输入", 422)
    return node_id, input_name


def _preset_target(graph: dict[str, Any], target: str, value: Any) -> tuple[str, str]:
    node_id, separator, input_name = target.partition(".")
    if not separator or not _scalar_input(graph, node_id, input_name) or isinstance(value, (list, dict)):
        raise DomainError("invalid_preset_target", "预设只能写入已登记的节点标量输入", 422)
    return node_id, input_name


def validate_manifest(manifest: dict[str, Any], task_type: str) -> dict[str, Any]:
    required = {"format_version", "task_type", "workflow_api", "bindings", "output", "presets", "dependency_lock", "runtime_profiles", "supported_parameters"}
    audio = task_type == "text_to_audio"
    if not required <= manifest.keys() or manifest["format_version"] != ("1.1" if audio else "1.0") or manifest["task_type"] != task_type or task_type not in TASK_TYPES:
        raise DomainError("invalid_manifest", "发布包缺少必要字段或能力类型不匹配", 422)
    graph = manifest["workflow_api"]
    if not isinstance(graph, dict) or not graph or "nodes" in graph or "links" in graph or len(graph) > 2000:
        raise DomainError("api_graph_required", "请导入 API-format Workflow，UI-format 需先转换", 422)
    for identifier, node in graph.items():
        if not isinstance(identifier, str) or not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise DomainError("invalid_node", "Workflow 节点格式无效", 422)
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int) and value[0] not in graph:
                raise DomainError("dangling_link", "Workflow 引用了不存在的节点", 422)
    bindings = manifest["bindings"]
    if not isinstance(bindings, list) or not bindings:
        raise DomainError("bindings_required", "需要指定参数到节点输入的绑定", 422)
    sources: set[str] = set()
    targets: set[tuple[str, str]] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"source", "node_id", "input_name"}:
            raise DomainError("invalid_binding", "绑定只能包含 source、node_id、input_name", 422)
        _binding_target(graph, binding)
        source = binding["source"]
        permitted = {"prompt", "negative_prompt", "seed", "width", "height", "seconds", "fps", "strength", "source_image", "mask", "output_prefix"}
        permitted |= {f"reference_{i}" for i in range(4)} | {f"reference_weight_{i}" for i in range(4)}
        if source not in permitted or (binding["node_id"], binding["input_name"]) in targets:
            raise DomainError("invalid_binding", "绑定来源不受支持或重复写同一输入", 422)
        targets.add((binding["node_id"], binding["input_name"]))
        sources.add(source)
    needed = {"prompt", "seed", "output_prefix"}
    if task_type == "image_to_image":
        needed.add("source_image")
    if task_type.startswith("references_"):
        needed.add("reference_0")
        if task_type == "references_to_image":
            needed.add("reference_1")
    if not needed <= sources:
        raise DomainError("missing_semantic_binding", "缺少必要输入绑定：" + ", ".join(sorted(needed - sources)), 422)
    slots = {i for i in range(4) if f"reference_{i}" in sources}
    if slots and slots != set(range(len(slots))):
        raise DomainError("invalid_reference_slots", "参考槽位必须从 reference_0 开始连续绑定", 422)
    output = manifest["output"]
    if not isinstance(output, dict) or output.get("adapter") not in ("save-image-v1", "video-files-v1", "audio-files-v1") or output.get("expected_count") != 1 or not output.get("final_node_ids") or not all(n in graph for n in output["final_node_ids"]):
        raise DomainError("invalid_output_adapter", "输出适配必须选择真实的最终节点，主产物数量为 1", 422)
    if output["adapter"] != {"image": "save-image-v1", "video": "video-files-v1", "audio": "audio-files-v1"}[task_media_kind(task_type)]:
        raise DomainError("output_type_mismatch", "输出适配器与能力类型不匹配", 422)
    if audio and (output.get("history_field") != "audio" or not 0 < output.get("max_bytes", 0) <= MAX_AUDIO_BYTES or not output.get("mime_types") or not set(output["mime_types"]) <= set(AUDIO_MIME_TYPES.values()) or sources & {"width", "height", "fps", "strength", "source_image", "mask"} or any(source.startswith("reference_") for source in sources)):
        raise DomainError("invalid_audio_adapter", "音频必须声明受控 audio 文件输出，不接受图像参数绑定", 422)
    reachable: set[str] = set()
    pending = list(output["final_node_ids"])
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        for value in graph[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in graph and isinstance(value[1], int):
                pending.append(value[0])
    if any(binding["node_id"] not in reachable for binding in bindings):
        raise DomainError("disconnected_binding", "参数绑定没有连接到最终输出", 422)
    if not isinstance(manifest["presets"], list) or not manifest["presets"]:
        raise DomainError("presets_required", "至少配置一个受控规格预设", 422)
    for preset in manifest["presets"]:
        if not all(name in preset for name in (("quality", "seconds", "audio_format", "sample_rate", "channels", "values") if audio else ("quality", "size", "values"))) or not isinstance(preset["values"], dict):
            raise DomainError("invalid_preset", "预设缺少 quality、size 或固定节点参数", 422)
        if audio:
            try:
                duration = Decimal(str(preset["seconds"]))
            except InvalidOperation as exc:
                raise DomainError("invalid_audio_preset", "音频预设必须声明数值时长", 422) from exc
            if not duration.is_finite() or not 0 < duration * 1000 <= MAX_AUDIO_MILLISECONDS or duration * 1000 != int(duration * 1000) or preset.get("size") is not None or AUDIO_MIME_TYPES.get(preset["audio_format"]) not in output["mime_types"] or type(preset["sample_rate"]) is not int or not 8000 <= preset["sample_rate"] <= 96000 or type(preset["channels"]) is not int or preset["channels"] not in (1, 2) or preset["audio_format"] == "opus" and preset["sample_rate"] != 48000:
                raise DomainError("invalid_audio_preset", "音频预设的时长、格式、采样率或声道无效", 422)
        elif any(preset.get(name) is not None for name in ("audio_format", "sample_rate", "channels")):
            raise DomainError("invalid_preset", "图像/视频预设不能声明音频规格", 422)
        for target, value in preset["values"].items():
            _preset_target(graph, target, value)
    lock = manifest["dependency_lock"]
    if isinstance(lock, dict) and lock.get("mode") == "comfy-api-only-v1":
        classes, model_inputs = lock.get("node_classes"), lock.get("model_inputs")
        if set(lock) != {"mode", "comfy_version", "node_classes", "model_inputs"} or not isinstance(lock.get("comfy_version"), str) or not 1 <= len(lock["comfy_version"]) <= 128 or not isinstance(classes, list) or not all(isinstance(c, str) and 1 <= len(c) <= 256 for c in classes) or len(classes) != len(set(classes)) or set(classes) != {n["class_type"] for n in graph.values()} or not isinstance(model_inputs, list) or len(model_inputs) > 2000:
            raise DomainError("api_dependency_invalid", "API 模式必须明确记录实际节点与模型选项，不得混用本地依赖锁", 422)
        for entry in model_inputs:
            if not isinstance(entry, dict) or set(entry) != {"class_type", "input_name", "value"} or not all(isinstance(v, str) and 1 <= len(v) <= (1024 if k == "value" else 256) for k, v in entry.items()) or not any(n["class_type"] == entry["class_type"] and n["inputs"].get(entry["input_name"]) == entry["value"] for n in graph.values()):
                raise DomainError("api_dependency_invalid", "API 模型选项必须对应冻结图中的实际输入", 422)
    elif not isinstance(lock, dict) or not isinstance(lock.get("comfy_commit"), str) or len(lock["comfy_commit"]) != 40 or not isinstance(lock.get("models"), list) or not isinstance(lock.get("custom_nodes"), list):
        raise DomainError("dependency_lock_required", "必须固定 ComfyUI 提交、模型和自定义节点依赖", 422)
    for model in lock.get("models", []):
        relative_file(model["path"])
        if len(model.get("sha256", "")) != 64:
            raise DomainError("model_hash_required", "模型依赖必须包含 SHA-256", 422)
    profiles = manifest["runtime_profiles"]
    if not isinstance(profiles, list):
        raise DomainError("profiles_required", "必须声明待验证的运行画像", 422)
    return {"valid": True, "digest": fingerprint(manifest), "node_count": len(graph), "runtime_approved": any(p.get("approved") is True and p.get("evidence_id") for p in profiles), "bound_sources": sorted(sources)}


def validate_reference_inputs(manifest: dict[str, Any], supplied: dict[str, Any]) -> None:
    references = supplied.get("references", [])
    if not references:
        return
    sources = {binding["source"] for binding in manifest.get("bindings", [])}
    slots = {i for i in range(4) if f"reference_{i}" in sources}
    if slots != set(range(len(references))):
        raise DomainError("reference_count_mismatch", "参考数量必须与已发布 Workflow 的连续槽位完全一致", 422)
    if any("weight" in reference and f"reference_weight_{i}" not in sources for i, reference in enumerate(references)):
        raise DomainError("reference_weight_not_supported", "Workflow 未绑定请求指定的参考权重", 422)


def compile_graph(manifest: dict[str, Any], execution: dict[str, Any], inputs: dict[str, str], output_prefix: str) -> dict[str, Any]:
    validate_reference_inputs(manifest, execution["input"])
    parameters = execution["parameters"]
    presets = matching_presets(manifest, parameters)
    if len(presets) != 1:
        raise DomainError("unsupported_specification", "请求规格没有唯一的已冻结预设", 422)
    template = manifest["workflow_api"]
    for binding in manifest["bindings"]:
        _binding_target(template, binding)
    # Check the original graph before applying any preset or request writes.
    preset_writes = [(_preset_target(template, target, value), value) for target, value in presets[0]["values"].items()]
    graph = deepcopy(template)
    for (node_id, input_name), value in preset_writes:
        graph[node_id]["inputs"][input_name] = value
    dimensions = {}
    if manifest.get("task_type") != "text_to_audio":
        width, height = (int(v) for v in parameters["size"].split("x"))
        dimensions = {"width": width, "height": height}
    values = {**parameters, **inputs, **dimensions, "prompt": execution["input"]["prompt"], "negative_prompt": execution["input"].get("negative_prompt", ""), "output_prefix": relative_file(output_prefix)}
    for i, reference in enumerate(execution["input"].get("references", [])):
        if "weight" in reference:
            values[f"reference_weight_{i}"] = reference["weight"]
    consumed = set()
    for binding in manifest["bindings"]:
        source = binding["source"]
        if source in values:
            graph[binding["node_id"]]["inputs"][binding["input_name"]] = values[source]
            consumed.add(source)
        elif source.startswith("reference_") and not source.startswith("reference_weight_"):
            raise DomainError("missing_reference", "预设所需的参考槽位缺少输入", 422)
    for i in range(len(execution["input"].get("references", []))):
        if f"reference_{i}" not in consumed:
            raise DomainError("unconsumed_reference", "所有参考图必须实际绑定到执行图", 422)
    return graph
