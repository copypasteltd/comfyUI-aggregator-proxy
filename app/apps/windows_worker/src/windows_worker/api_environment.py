from __future__ import annotations

from typing import Any

from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflows import validate_manifest

from windows_worker.comfy import ComfyClient
from windows_worker.comfy_json import MAX_INVENTORY_BYTES, MAX_SYSTEM_BYTES, request_json


def observe_api_environment(base_url: str, authorization: str | None, manifest: dict[str, Any]) -> dict[str, Any]:
    """Observe the declared public API surface; never claim host-file verification."""
    validate_manifest(manifest, manifest["task_type"])
    lock = manifest["dependency_lock"]
    if lock.get("mode") != "comfy-api-only-v1":
        raise DomainError("dependency_mode_mismatch", "HTTP Worker 不能核验本地主机依赖锁")
    client = ComfyClient(base_url, authorization)
    try:
        system = request_json(client.http, "GET", "/system_stats", maximum=MAX_SYSTEM_BYTES)
        objects = request_json(client.http, "GET", "/object_info", maximum=MAX_INVENTORY_BYTES)
        if not isinstance(system.get("system"), dict) or system["system"].get("comfyui_version") != lock["comfy_version"] or any(not isinstance(objects.get(name), dict) for name in lock["node_classes"]):
            raise DomainError("api_profile_changed", "ComfyUI API 版本或所需节点已变化")
        for model in lock["model_inputs"]:
            inputs = objects[model["class_type"]].get("input", {})
            options = {**inputs.get("required", {}), **inputs.get("optional", {})}.get(model["input_name"])
            if not isinstance(options, list) or not options or not isinstance(options[0], list) or model["value"] not in options[0]:
                raise DomainError("api_model_unavailable", "冻结模型选项不在当前 ComfyUI API 目录中")
        return {"environment_verified": False, "api_profile_verified": True, "environment_digest": fingerprint({"mode": "comfy-api-only-v1", "dependency_lock": lock})}
    finally:
        client.close()
