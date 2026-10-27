from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflows import relative_file

from windows_worker.api_environment import observe_api_environment
from windows_worker.comfy import ComfyClient
from windows_worker.comfy_json import (
    MAX_INVENTORY_BYTES,
    MAX_QUEUE_BYTES,
    MAX_SYSTEM_BYTES,
    checked_queue,
    invalid_response,
    request_json,
)
from windows_worker.configuration import WorkerSettings
from windows_worker.environment import verify_environment
from windows_worker.model_files import discover_models, inspect_model
from windows_worker.workflow_catalogs import list_exports
from windows_worker.workflow_exports import read_export


class CommandExecutor:
    def __init__(self, state_root: Path, settings: WorkerSettings | None = None) -> None:
        self.root = state_root
        self.settings = settings
        self.root.mkdir(parents=True, exist_ok=True)

    def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        kind, payload = command["type"], command["payload"]
        if fingerprint({"type": kind, "payload": payload}) != command["command_digest"]:
            raise DomainError("command_digest_mismatch", "管理指令摘要无效")
        base = {"success": True, "applied_revision": payload["revision"]}
        local = self.settings.channel(payload["base_url"]) if self.settings else None
        if kind == "inspect_model_file":
            if local is None or self.settings is None:
                raise DomainError("channel_not_allowlisted", "渠道尚未在 Worker 本地配置登记", 422)
            return {**base, "inspection": inspect_model(local.local_root, payload, maximum_bytes=self.settings.model_hash_maximum_bytes, seconds=self.settings.model_hash_timeout_seconds)}
        if kind == "pull_workflow":
            if local is None:
                raise DomainError("channel_not_allowlisted", "渠道尚未在 Worker 本地配置登记", 422)
            if payload.get("mode", "file") == "catalog":
                return {**base, **list_exports(local.workflow_export_root, payload["relative_directory"], payload.get("cursor"))}
            if payload.get("mode", "file") != "file":
                raise DomainError("unsupported_command", "不支持此 Workflow 读取方式", 422)
            result = read_export(local.workflow_export_root, payload["relative_path"])
            if payload.get("expected_source_digest") and result["source_digest"] != payload["expected_source_digest"]:
                raise DomainError("workflow_export_changed", "所选导出内容已变化，请刷新目录后重试", 409)
            return {**base, **result}
        if kind == "apply_workflow_release":
            if not local or fingerprint(payload["manifest"]) != payload["release_digest"]:
                raise DomainError("workflow_not_verified", "工作流摘要或本地渠道配置不符")
            checked = observe_api_environment(local.base_url, command.get("authorization"), payload["manifest"]) if local.connection_mode == "remote_http" else verify_environment(local.local_root, payload["manifest"], self.settings.git_executable if self.settings else None)
            target = self.root / (relative_file(payload["release_id"]) + ".json")
            temporary = target.with_suffix(".staged")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(payload["manifest"], file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(target)
            return {**base, **checked, "release_digest": payload["release_digest"]}
        if kind == "apply_channel_config":
            # No network, shell, plugin/model installation, or process launch.
            channel = relative_file(payload["channel_id"])
            config = {"revision": payload["revision"], "base_url": payload["base_url"], "gpu_id": payload["gpu_id"]}
            target = self.root / (channel + ".json")
            if target.exists():
                existing = json.loads(target.read_text(encoding="utf-8"))
                if existing["revision"] > config["revision"] or (existing["revision"] == config["revision"] and existing != config):
                    raise DomainError("configuration_conflict", "已应用版本更高或同版内容冲突")
            temporary = target.with_suffix(".staged")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(config, file)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(target)
            return base
        client = ComfyClient(payload["base_url"], command.get("authorization"))
        try:
            if kind == "probe_channel":
                data = request_json(client.http, "GET", "/system_stats", maximum=MAX_SYSTEM_BYTES)
                if not isinstance(data.get("system"), dict):
                    raise DomainError("invalid_comfy_response", "目标没有返回有效 ComfyUI 系统信息")
                return {**base, "comfy_reachable": True}
            if kind == "scan_inventory":
                data = request_json(client.http, "GET", "/object_info", maximum=MAX_INVENTORY_BYTES)
                if len(data) > 10000 or any(not key or len(key) > 256 or not isinstance(value, dict) for key, value in data.items()):
                    raise invalid_response()
                result = {**base, "inventory": {"node_classes": sorted(data), "node_schema_digest": fingerprint(data)}}
                if local is not None and local.connection_mode == "local":
                    result["model_catalog"] = discover_models(local.local_root)
                return result
            if kind == "collect_diagnostics":
                data = checked_queue(request_json(client.http, "GET", "/queue", maximum=MAX_QUEUE_BYTES))
                return {**base, "queue_running_count": len(data.get("queue_running", [])), "queue_pending_count": len(data.get("queue_pending", []))}
            raise DomainError("unsupported_command", "当前 Worker 不支持此管理指令")
        finally:
            client.close()
