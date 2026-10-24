from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO


class WorkflowRetryTarget(StrictDTO):
    command_id: str = Field(pattern=r"^cmd_[0-9a-f]{32}$")
    channel_id: str = Field(pattern=r"^ch_[0-9a-f]{32}$")
    expected_revision: int = Field(ge=1)


class WorkflowSyncRetry(StrictDTO):
    release_id: str = Field(pattern=r"^wfr_[0-9a-f]{32}$")
    release_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    targets: list[WorkflowRetryTarget] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_targets(self) -> WorkflowSyncRetry:
        if len({t.command_id for t in self.targets}) != len(self.targets) or len({t.channel_id for t in self.targets}) != len(self.targets):
            raise ValueError("重试命令和渠道不能重复")
        return self


RetryBlockReason = Literal["operation_incomplete", "not_failed", "failure_evidence_required", "revalidation_required", "already_retried", "channel_unavailable", "configuration_changed", "superseded", "management_busy", "release_unavailable"]


class WorkflowRetryOption(StrictDTO):
    command_id: str
    channel_id: str
    channel_name: str | None
    current_revision: int | None
    status: str
    eligible: bool
    reason: RetryBlockReason | None
    retry_operation_id: str | None


class WorkflowSyncRetryOptions(StrictDTO):
    operation_id: str
    source_status: str
    release_id: str
    release_digest: str
    task_type: str | None
    snapshot_at: int
    targets: list[WorkflowRetryOption] = Field(max_length=100)
