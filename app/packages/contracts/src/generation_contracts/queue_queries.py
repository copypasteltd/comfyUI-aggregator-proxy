from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QueueState = Literal["admission_pending", "queued", "leased", "preparing", "submitting", "running", "collecting", "uploading", "verifying", "reconciling", "retry_wait", "cancel_requested", "succeeded", "failed", "cancelled", "expired"]
TenantId = Annotated[str, Field(pattern=r"^ten_[a-f0-9]{32}$")]
ProjectId = Annotated[str, Field(pattern=r"^prj_[a-f0-9]{32}$")]
WorkflowId = Annotated[str, Field(pattern=r"^wf_[a-f0-9]{32}$")]


class QueueQuery(BaseModel):
    # HTTP query strings are parsed as numbers; frozen JSON fixtures use numeric values.
    model_config = ConfigDict(extra="forbid")
    page: int = Field(1, ge=1, le=10000)
    tenant_id: TenantId | None = None
    project_id: ProjectId | None = None
    model: str | None = Field(None, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    workflow_id: WorkflowId | None = None
    release_id: str | None = Field(None, pattern=r"^wfr_[a-f0-9]{32}$")
    channel_id: str | None = Field(None, pattern=r"^ch_[a-f0-9]{32}$")
    key_id: str | None = Field(None, pattern=r"^key_[a-f0-9]{32}$")
    state: QueueState | None = None
    created_from: int | None = Field(None, ge=0, le=4102444800)
    created_before: int | None = Field(None, ge=0, le=4102444800)

    @model_validator(mode="after")
    def time_order(self) -> QueueQuery:
        if self.created_from is not None and self.created_before is not None and self.created_from >= self.created_before:
            raise ValueError("created_from must precede created_before")
        return self

    def filters(self) -> dict[str, str | int]:
        return self.model_dump(exclude={"page"}, exclude_none=True)


class QueueOptionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tenant", "project", "model", "workflow", "release", "channel"]
    page: int = Field(1, ge=1, le=10000)
    q: str = Field("", max_length=128)
    tenant_id: TenantId | None = None
    project_id: ProjectId | None = None
    workflow_id: WorkflowId | None = None


class QueueOption(BaseModel):
    value: str
    label: str


class QueueOptions(BaseModel):
    data: list[QueueOption]
    page: int
    has_more: bool
