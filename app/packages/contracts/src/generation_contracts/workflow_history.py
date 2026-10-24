from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from generation_contracts.model_catalog import ProjectId, ReleaseId, WorkflowId
from generation_contracts.validation import StrictDTO, validate


class HistoryPageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1, le=1000)

    @field_validator("page", mode="before")
    @classmethod
    def integer_page(cls, value: Any) -> int:
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            return int(value)
        raise ValueError("Use integer page values")


class WorkflowHistoryQuery(HistoryPageQuery):
    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-history-query", self.model_dump())
        return self


class WorkflowDiffQuery(HistoryPageQuery):
    base_release_id: ReleaseId
    target_release_id: ReleaseId | None = None

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-diff-query", self.model_dump())
        return self


class WorkflowReleaseReference(StrictDTO):
    kind: Literal["release"]
    id: ReleaseId
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    validated: bool
    created_at: int = Field(ge=0)


class WorkflowDraftReference(StrictDTO):
    kind: Literal["draft"]
    revision: int = Field(ge=1)


class HistoryEnvelope(StrictDTO):
    workflow_id: WorkflowId
    project_id: ProjectId
    workflow_revision: int = Field(ge=1)
    page: int = Field(ge=1, le=1000)
    has_more: bool
    total: int = Field(ge=0)
    snapshot_at: int = Field(ge=0)


class WorkflowHistoryPage(HistoryEnvelope):
    data: list[WorkflowReleaseReference] = Field(max_length=50)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-history-page", self.model_dump())
        return self


JsonKind = Literal["object", "array", "string", "number", "boolean", "null"]


class WorkflowDifference(StrictDTO):
    path: str = Field(min_length=1, max_length=512)
    path_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    path_truncated: bool
    change: Literal["added", "removed", "changed"]
    before_type: JsonKind | None
    after_type: JsonKind | None
    before_preview: str | None = Field(max_length=2048)
    after_preview: str | None = Field(max_length=2048)
    before_truncated: bool
    after_truncated: bool


class WorkflowDiffCounts(StrictDTO):
    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    changed: int = Field(ge=0)


class WorkflowDiff(HistoryEnvelope):
    base: WorkflowReleaseReference
    target: Annotated[WorkflowReleaseReference | WorkflowDraftReference, Field(discriminator="kind")]
    counts: WorkflowDiffCounts
    data: list[WorkflowDifference] = Field(max_length=50)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-diff", self.model_dump())
        return self
