from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO

CancellationReason = Literal["operator_request", "key_access_changed", "maintenance"]
CancellationOutcome = Literal["pending", "cancelled", "cancel_requested", "unchanged", "failed"]
CancellationError = Literal["not_found", "database_busy", "cancellation_rejected"]


class JobCancellationBatch(StrictDTO):
    job_ids: list[str] = Field(min_length=1, max_length=50)
    reason: CancellationReason

    @model_validator(mode="after")
    def explicit_jobs(self) -> JobCancellationBatch:
        import re

        if len(set(self.job_ids)) != len(self.job_ids) or any(not re.fullmatch(r"job_[0-9a-f]{32}", job) for job in self.job_ids):
            raise ValueError("任务 ID 必须有效且不能重复")
        return self


class CancellationTarget(StrictDTO):
    job_id: str = Field(pattern=r"^job_[0-9a-f]{32}$")
    outcome: CancellationOutcome = "pending"
    recorded_status: str | None = None
    processed_at: int | None = None
    transient_attempts: int = Field(default=0, ge=0, le=5)
    error_code: CancellationError | None = None


class CancellationDocument(StrictDTO):
    version: Literal[1] = 1
    reason: CancellationReason
    request_id: str = Field(min_length=1, max_length=128)
    targets: list[CancellationTarget] = Field(min_length=1, max_length=50)
    completed_at: int | None = None

    @model_validator(mode="after")
    def unique_targets(self) -> CancellationDocument:
        if len({t.job_id for t in self.targets}) != len(self.targets):
            raise ValueError("重复取消目标")
        return self


class CancellationTargetView(CancellationTarget):
    current_status: str | None


class JobCancellationView(StrictDTO):
    operation_id: str
    status: str
    reason: CancellationReason
    created_at: int
    completed_at: int | None
    snapshot_at: int
    processing_complete: bool
    targets: list[CancellationTargetView]
