from __future__ import annotations

from typing import Any

from pydantic import Field

from generation_contracts.admin import ExpectedRevision
from generation_contracts.validation import StrictDTO


class MemoryPolicyPatch(ExpectedRevision):
    enabled: bool
    idle_seconds: int = Field(default=600, ge=600, le=86400)


class MemoryOperationView(StrictDTO):
    id: str
    stage: str
    created_at: int
    updated_at: int
    completed_at: int | None
    error_code: str | None
    evidence: dict[str, Any]


class MemoryPolicyView(StrictDTO):
    id: str | None
    channel_id: str
    channel_ids: list[str]
    enabled: bool
    idle_seconds: int
    revision: int
    status: str
    idle_since: int | None
    observed_at: int | None
    released: bool
    active_operation_id: str | None
    operations: list[MemoryOperationView]
