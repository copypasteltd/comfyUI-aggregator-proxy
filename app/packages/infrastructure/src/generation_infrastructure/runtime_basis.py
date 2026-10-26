from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_domain.values import fingerprint


def wait_basis(*, release_digest: str, specification: str, gpu_id: UUID, hardware_id: str, channel_id: UUID,
               revision: int, approval: dict[str, Any] | None, profile: dict[str, Any] | None,
               validation: bool, test_only: bool) -> str | None:
    if validation or test_only or not approval or not profile or profile.get("id") != approval.get("profile_id") or profile.get("gpu_hardware_id") != hardware_id:
        return None
    return fingerprint({"version": "wait-basis-v1", "release": release_digest, "specification": specification,
                        "gpu": str(gpu_id), "channel": str(channel_id), "revision": revision,
                        "approval": approval, "profile": profile})
