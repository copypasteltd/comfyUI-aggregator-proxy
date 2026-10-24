from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from generation_contracts.admin import ExpectedRevision
from generation_contracts.validation import JobCreate, StrictDTO


class ModelCreate(StrictDTO):
    tenant_id: str
    project_id: str
    alias: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    release_id: str
    channel_ids: list[str] = Field(min_length=1, max_length=100)
    defaults: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ModelPatch(ExpectedRevision):
    release_id: str
    channel_ids: list[str] = Field(min_length=1, max_length=100)
    defaults: dict[str, str | int | float | bool]


class ModelAction(ExpectedRevision):
    action: Literal["publish", "unpublish"]


class BindRelease(ExpectedRevision):
    release_id: str


class ApproveRuntime(ExpectedRevision):
    release_id: str
    profile_id: str
    evidence_id: str


class ValidationJob(StrictDTO):
    channel_id: str
    request: JobCreate


class ReleaseValidation(ValidationJob):
    model_id: str
    expected_model_revision: int = Field(ge=1)
    expected_channel_revision: int = Field(ge=1)
    sample_budget: Literal[1] = 1


class SyncTarget(StrictDTO):
    channel_id: str
    expected_revision: int = Field(ge=1)


class WorkflowSync(StrictDTO):
    direction: Literal["push"]
    release_id: str
    release_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    targets: list[SyncTarget] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_targets(self) -> WorkflowSync:
        if len({target.channel_id for target in self.targets}) != len(self.targets):
            raise ValueError("目标渠道不能重复")
        return self


class OperationAccepted(BaseModel):
    operation_id: str
    status: str


class ValidationAccepted(BaseModel):
    id: str
    object: Literal["generation.job"]
    model: str
    task_type: str
    status: str
    created_at: int
    updated_revision: int
    error: dict[str, str] | None
    completed_at: int | None = None


class FrozenReleaseView(BaseModel):
    id: str
    workflow_id: str
    version: int
    digest: str
    manifest: dict[str, Any]
    validated: bool
    validation: dict[str, Any]


class RuntimeApprovalView(BaseModel):
    approved: Literal[True]
    revision: int


class ModelView(BaseModel):
    id: str
    tenant_id: str
    project_id: str
    alias: str
    task_type: str
    release_id: str
    channel_ids: list[str]
    defaults: dict[str, Any]
    revision: int
    published: bool


class BindingView(BaseModel):
    release_id: str
    desired_digest: str
    applied_digest: str | None
    verified_digest: str | None
    observed_at: int | None
