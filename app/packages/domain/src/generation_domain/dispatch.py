from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

POLICY_ID = "tenant-service-aging-v1"
AGING_STEP_SECONDS = 5
OVERDUE_TENANT_SECONDS = 300
MINIMUM_SERVICE_MILLISECONDS = 1000
MAXIMUM_SERVICE_MILLISECONDS = 7_200_000
IMAGE_BASELINE_MILLISECONDS = 60_000
VIDEO_BASELINE_MILLISECONDS = 300_000
AUDIO_BASELINE_MILLISECONDS = 300_000


@dataclass(frozen=True)
class DispatchCandidate:
    job_id: UUID
    tenant_id: UUID
    priority: int
    created_at: datetime


@dataclass(frozen=True)
class TenantService:
    virtual_milliseconds: int = 0
    last_sequence: int = 0


def waiting_seconds(created_at: datetime, now: datetime) -> int:
    return max(0, int((now - created_at).total_seconds()))


def effective_priority(priority: int, created_at: datetime, now: datetime) -> int:
    if not -100 <= priority <= 100:
        raise ValueError("Priority outside the frozen bounds")
    return min(100, priority + waiting_seconds(created_at, now) // AGING_STEP_SECONDS)


def estimated_service(task_type: str, approved_profile: Mapping[str, Any] | None) -> tuple[int, str]:
    if task_type not in {"text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"}:
        raise ValueError("Unknown generation task type")
    observed = approved_profile.get("warm_p95_milliseconds") if approved_profile else None
    if type(observed) is int and observed > 0:
        return min(MAXIMUM_SERVICE_MILLISECONDS, max(MINIMUM_SERVICE_MILLISECONDS, observed)), "approved_profile"
    if task_type == "image_to_3d":
        return 300_000, "model3d_baseline"
    if task_type.endswith("video"):
        return VIDEO_BASELINE_MILLISECONDS, "video_baseline"
    if task_type == "text_to_audio":
        return AUDIO_BASELINE_MILLISECONDS, "audio_baseline"
    return IMAGE_BASELINE_MILLISECONDS, "image_baseline"


def order_candidates(candidates: Sequence[DispatchCandidate], services: Mapping[UUID, TenantService], clock: int, now: datetime) -> list[UUID]:
    if clock < 0:
        raise ValueError("Virtual clock must be nonnegative")
    tenants: dict[UUID, list[DispatchCandidate]] = {}
    for candidate in candidates:
        tenants.setdefault(candidate.tenant_id, []).append(candidate)

    def tenant_key(identifier: UUID) -> tuple[int, int, int, datetime, str]:
        service = services.get(identifier, TenantService())
        oldest = min(item.created_at for item in tenants[identifier])
        start = max(clock, service.virtual_milliseconds)
        if waiting_seconds(oldest, now) >= OVERDUE_TENANT_SECONDS:
            return 0, service.last_sequence, start, oldest, identifier.hex
        return 1, start, service.last_sequence, oldest, identifier.hex

    return [item.job_id for tenant in sorted(tenants, key=tenant_key)
            for item in sorted(tenants[tenant], key=lambda candidate: (-effective_priority(candidate.priority, candidate.created_at, now), candidate.created_at, candidate.job_id.hex))]
