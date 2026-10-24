from __future__ import annotations

from typing import Any

from generation_domain.errors import DomainError


def selected(selection: dict[str, Any], identifier: str) -> bool:
    return selection["mode"] == "all_authorized" or identifier in selection["selected_ids"]


def limit_value(limits: dict[str, Any], name: str) -> int | None:
    item = limits.get(name, {"mode": "bounded", "value": 0}) if name == "daily_audio_milliseconds" else limits[name]
    return None if item["mode"] == "unlimited" else int(item["value"])


def enforce_limit(limits: dict[str, Any], name: str, used: int, addition: int) -> None:
    bound = limit_value(limits, name)
    if bound is not None and used + addition > bound:
        raise DomainError("quota_exceeded", "额度不足：" + name, 429)


def allowed_content(policy: dict[str, Any], own_key: str, owner: str, project: str) -> bool:
    if own_key == owner:
        return True
    content = policy["content"]
    if project not in content["project_ids"]:
        return False
    return content["mode"] == "project_shared" or (content["mode"] == "shared_creator_keys" and owner in content["creator_key_ids"])
