"""Bounded, lossless Workflow sources shared by Worker and control plane."""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, fingerprint

MAX_WORKFLOW_BYTES = 1024 * 1024
EXPORT_FORMATS = ("comfy-api", "manifest-v1")


def invalid_source() -> DomainError:
    return DomainError("invalid_workflow_source", "Workflow 导出格式无效，请使用 API 格式或完整发布包", 422)


def export_path(value: str) -> str:
    if not 1 <= len(value) <= 512 or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise DomainError("invalid_export_path", "请输入导出目录内的相对 JSON 文件路径", 422)
    parts = value.split("/")
    if any(not p or p in (".", "..") or p[-1] in (".", " ") or re.search(r'[<>"|?*]', p) or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\..*)?", p) for p in parts) or not value.lower().endswith(".json"):
        raise DomainError("invalid_export_path", "请输入导出目录内的相对 JSON 文件路径", 422)
    return value


def bounded_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid_source()
    stack = [(value, 0)]
    count, size = 0, 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 100000 or depth > 64:
            raise invalid_source()
        if isinstance(item, dict):
            if count + len(stack) + 2 * len(item) > 100000 or any(not isinstance(k, str) for k in item):
                raise invalid_source()
            size += 2 + len(item)  # punctuation; exact bound checked below
            stack.extend((k, depth + 1) for k in item)
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            if count + len(stack) + len(item) > 100000:
                raise invalid_source()
            size += 2 + len(item)
            stack.extend((v, depth + 1) for v in item)
        elif item is None or type(item) in (str, bool, int, float):
            if type(item) is float and not math.isfinite(item):
                raise invalid_source()
            try:
                size += len(canonical_json(item).encode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise invalid_source() from exc
        else:
            raise invalid_source()
        if size > MAX_WORKFLOW_BYTES:
            raise DomainError("workflow_source_too_large", "Workflow JSON 超过 1 MiB 限额", 413)
    if len(canonical_json(value).encode("utf-8")) > MAX_WORKFLOW_BYTES:
        raise DomainError("workflow_source_too_large", "Workflow JSON 超过 1 MiB 限额", 413)
    return value


def source_format(value: Any) -> str:
    document = bounded_document(value)
    if "nodes" in document or "links" in document:
        raise DomainError("ui_workflow_not_supported", "请在 ComfyUI 导出 API 格式；当前不转换 UI 格式", 422)
    manifest = document.get("format_version") in ("1.0", "1.1", "1.2")
    graph = document.get("workflow_api") if manifest else document
    if not isinstance(graph, dict) or not 1 <= len(graph) <= 2000:
        raise invalid_source()
    for key, node in graph.items():
        if not 1 <= len(key) <= 128 or any(ord(c) < 32 for c in key) or not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not 1 <= len(node["class_type"]) <= 256 or not isinstance(node.get("inputs"), dict):
            raise invalid_source()
    return "manifest-v1" if manifest else "comfy-api"


def parse_source(raw: bytes) -> tuple[dict[str, Any], str]:
    if len(raw) > MAX_WORKFLOW_BYTES:
        raise DomainError("workflow_source_too_large", "Workflow JSON 超过 1 MiB 限额", 413)

    def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in entries:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError("non-finite JSON value")

    def number(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or Decimal(value) != Decimal(str(parsed)):
            raise ValueError("number cannot round-trip without loss")
        return parsed

    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
        return document, source_format(document)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise invalid_source() from exc


def checked_source_receipt(result: dict[str, Any]) -> dict[str, Any]:
    document = result.get("source_document")
    kind = source_format(document)
    if set(result) != {"success", "applied_revision", "source_document", "source_digest", "export_format", "source_bytes"} or result.get("success") is not True or type(result.get("applied_revision")) is not int or result["applied_revision"] < 1 or result.get("export_format") != kind or result.get("source_digest") != fingerprint(document) or type(result.get("source_bytes")) is not int or not 1 <= result["source_bytes"] <= MAX_WORKFLOW_BYTES:
        raise invalid_source()
    assert isinstance(document, dict)
    return document


def draft_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old, new = before.get("workflow_api", {}), after.get("workflow_api", {})
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    groups = {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()), "changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k])}
    return {"nodes": {name: {"count": len(ids), "ids": ids[:50], "truncated": len(ids) > 50} for name, ids in groups.items()}, "changed_fields": sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))}
