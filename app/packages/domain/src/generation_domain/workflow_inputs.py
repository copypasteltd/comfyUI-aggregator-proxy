from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.media_types import AUDIO_MIME_TYPES, MAX_AUDIO_BYTES, MAX_MODEL3D_BYTES

INPUT_FEATURE = "workflow-inputs-v1"
INPUT_KINDS: dict[str, tuple[int, frozenset[str]]] = {
    "image": (20 * 1024**2, frozenset({"image/png", "image/jpeg", "image/webp"})),
    "video": (512 * 1024**2, frozenset({"video/mp4", "video/webm"})),
    "audio": (MAX_AUDIO_BYTES, frozenset(AUDIO_MIME_TYPES.values())),
    "model": (MAX_MODEL3D_BYTES, frozenset({"model/gltf-binary"})),
}
INPUT_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm", "audio/wav": ".wav", "audio/flac": ".flac", "audio/mpeg": ".mp3", "audio/ogg": ".opus", "model/gltf-binary": ".glb"}
SLOT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def supports_inputs(manifest: dict[str, Any], features: list[str]) -> bool:
    return manifest.get("format_version") != "1.3" or INPUT_FEATURE in features


@dataclass(frozen=True)
class InputAsset:
    source: str
    asset_id: str
    kind: str | None = "image"
    max_bytes: int = 20 * 1024**2
    mime_types: frozenset[str] = INPUT_KINDS["image"][1]
    path_mode: str = "comfy_relative"


def declared_slots(manifest: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    return {slot["name"]: slot for slot in manifest.get(field, [])}


def input_assets(supplied: dict[str, Any], manifest: dict[str, Any] | None = None) -> list[InputAsset]:
    result = [InputAsset(name, value["asset_id"]) for name, value in supplied.items() if name in ("source_image", "mask")]
    result.extend(InputAsset(f"reference_{i}", value["asset_id"]) for i, value in enumerate(supplied.get("references", [])))
    slots = declared_slots(manifest or {}, "input_slots")
    for item in supplied.get("media", []):
        slot = slots.get(item["slot"])
        if slot:
            result.append(InputAsset("media:" + item["slot"], item["asset_id"], slot["kind"], slot["max_bytes"], frozenset(slot["mime_types"]), slot["path_mode"]))
        elif manifest is None:
            result.append(InputAsset("media:" + item["slot"], item["asset_id"], None, 512 * 1024**2, frozenset()))
        else:
            raise DomainError("unknown_input_slot", "素材槽位未在已发布工作流声明", 422)
    return result


def validate_slot_declarations(manifest: dict[str, Any], sources: set[str]) -> None:
    modern = manifest.get("format_version") == "1.3"
    for field, prefix in (("input_slots", "media:"), ("text_slots", "text:")):
        items = manifest.get(field, [])
        if not isinstance(items, list) or len(items) > 16 or items and not modern:
            raise DomainError("invalid_input_slots", "命名槽位只用于 1.3 工作流，每类最多 16 个", 422)
        names: set[str] = set()
        for slot in items:
            common = {"name", "label", "description", "required"}
            expected = common | ({"kind", "max_bytes", "mime_types", "path_mode"} if field == "input_slots" else {"max_length"})
            if not isinstance(slot, dict) or set(slot) != expected or not isinstance(slot.get("name"), str) or not SLOT_NAME.fullmatch(slot["name"]) or slot["name"] in names or type(slot.get("required")) is not bool or any(not isinstance(slot.get(key), str) or not 1 <= len(slot[key].strip()) <= bound for key, bound in (("label", 160), ("description", 1000))):
                raise DomainError("invalid_input_slots", "槽位名称、说明或必填定义无效", 422)
            names.add(slot["name"])
            if field == "input_slots":
                kind = slot["kind"]
                mimes = slot["mime_types"]
                if not isinstance(kind, str) or kind not in INPUT_KINDS or type(slot["max_bytes"]) is not int or not 0 < slot["max_bytes"] <= INPUT_KINDS[kind][0] or not isinstance(mimes, list) or not mimes or any(not isinstance(mime, str) for mime in mimes) or len(mimes) != len(set(mimes)) or not set(mimes) <= INPUT_KINDS[kind][1] or slot["path_mode"] not in ("comfy_relative", "comfy_absolute"):
                    raise DomainError("invalid_input_slots", "素材槽的类型、字节上限、MIME 或路径模式无效", 422)
            elif type(slot["max_length"]) is not int or not 1 <= slot["max_length"] <= 32000:
                raise DomainError("invalid_input_slots", "文本槽长度上限无效", 422)
        if {source.removeprefix(prefix) for source in sources if source.startswith(prefix)} != names:
            raise DomainError("unbound_input_slot", "每个命名槽位必须绑定到执行图，绑定不能引用未声明槽位", 422)


def validate_named_inputs(manifest: dict[str, Any], supplied: dict[str, Any]) -> None:
    media, texts = supplied.get("media", []), supplied.get("texts", {})
    if (media or texts) and manifest.get("format_version") != "1.3":
        raise DomainError("input_not_supported", "该版本没有声明命名素材或文本输入", 422)
    expects_context = bool((manifest.get("context") or {}).get("load"))
    if expects_context != ("context" in supplied):
        raise DomainError("workflow_context_required" if expects_context else "input_not_supported", "父任务上下文与冻结工作流要求不匹配", 422)
    if manifest.get("format_version") == "1.3":
        sources = {item["source"] for item in manifest.get("bindings", [])}
        if any(name in sources and name not in supplied for name in ("prompt", "source_image")):
            raise DomainError("missing_input_slot", "缺少工作流要求的提示词或源图片", 422)
    names = [item["slot"] for item in media]
    if len(names) != len(set(names)):
        raise DomainError("duplicate_input_slot", "每个素材槽只能提供一次", 422)
    for field, provided in (("input_slots", set(names)), ("text_slots", set(texts))):
        declared = declared_slots(manifest, field)
        if provided - declared.keys():
            raise DomainError("unknown_input_slot", "请求包含未声明的输入槽位", 422)
        if any(slot["required"] and name not in provided for name, slot in declared.items()):
            raise DomainError("missing_input_slot", "缺少工作流要求的输入槽位", 422)
        if field == "text_slots" and (sum(len(value) for value in texts.values()) > 32000 or any(not value.strip() or len(value) > declared[name]["max_length"] for name, value in texts.items())):
            raise DomainError("invalid_text_input", "文本输入为空或超过槽位长度限制", 422)
