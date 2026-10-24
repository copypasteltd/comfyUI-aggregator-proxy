from __future__ import annotations

from typing import Any


def default_limits() -> dict[str, Any]:
    values = {"rpm": 120, "burst": 10, "daily_jobs": 1000, "unfinished_jobs": 20, "generating_jobs": 2, "daily_image_count": 1000, "daily_video_milliseconds": 3600000, "daily_audio_milliseconds": 0}
    return {k: {"mode": "bounded", "value": v} for k, v in values.items()}


def default_key_policy() -> dict[str, Any]:
    return {
        "scopes": ["models:read", "capabilities:read", "jobs:create", "jobs:read", "jobs:cancel", "images:create", "videos:create", "assets:upload", "assets:read", "artifacts:read", "content:read", "content:download"],
        **{name: {"mode": "selected", "selected_ids": []} for name in ("models", "workflow_releases", "channels")},
        "spec_limits": {"image_max_pixels": 4194304, "video_max_pixels": 2073600, "video_max_milliseconds": 15000, "audio_max_milliseconds": 0, "max_references": 4, "allowed_qualities": ["standard"]},
        "content": {"mode": "owner_only", "project_ids": [], "creator_key_ids": []},
        "limits": default_limits(),
        "allowed_cidrs": {"mode": "any", "values": []},
        "download": {"mode": "authenticated_proxy", "signed_url_max_seconds": 300},
        "period_timezone": "Asia/Taipei",
    }
