from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from generation_contracts.capacity import WorkerCapacity
from pydantic import ValidationError


def capacity_snapshot(inventory: dict[str, Any], boot_id: UUID | None, now: datetime) -> WorkerCapacity | None:
    entry = inventory.get("capacity")
    if not isinstance(entry, dict) or entry.get("boot_id") != str(boot_id):
        return None
    at = entry.get("observed_at_ms")
    if type(at) is not int or not 0 <= int(now.timestamp() * 1000) - at < 15000:
        return None
    try:
        return WorkerCapacity.model_validate(entry.get("sample"))
    except ValidationError:
        return None


def capacity_ready(inventory: dict[str, Any], boot_id: UUID | None, now: datetime, *, next_reserve: int | None = None) -> bool:
    sample = capacity_snapshot(inventory, boot_id, now)
    return bool(sample is not None and sample.status == "ready" and not sample.derived_reasons(next_reserve))
