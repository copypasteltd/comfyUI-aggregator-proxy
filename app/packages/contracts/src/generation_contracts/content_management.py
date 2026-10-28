from __future__ import annotations

import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from generation_contracts.queue_queries import ProjectId, TenantId, WorkflowId
from generation_contracts.validation import StrictDTO

ContentState = Literal["available", "unverified", "expired", "pending_reclaim", "reclaimed"]
Tag = Annotated[str, Field(min_length=1, max_length=64)]


def canonical_tag(value: str) -> str:
    if value != value.strip() or unicodedata.normalize("NFC", value) != value or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ValueError("Tags must be NFC, have no boundary whitespace or control characters")
    return value


class ContentQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["input", "output"] = "output"
    page: int = Field(1, ge=1, le=10000)
    tenant_id: TenantId | None = None
    project_id: ProjectId | None = None
    key_id: str | None = Field(None, pattern=r"^key_[a-f0-9]{32}$")
    job_id: str | None = Field(None, pattern=r"^job_[a-f0-9]{32}$")
    model: str | None = Field(None, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    workflow_id: WorkflowId | None = None
    release_id: str | None = Field(None, pattern=r"^wfr_[a-f0-9]{32}$")
    tag: Tag | None = None
    media_kind: Literal["image", "video", "audio", "model"] | None = None
    state: ContentState | None = None
    created_from: int | None = Field(None, ge=0, le=4102444800)
    created_before: int | None = Field(None, ge=0, le=4102444800)

    @field_validator("tag")
    @classmethod
    def tag_format(cls, value: str | None) -> str | None:
        return canonical_tag(value) if value is not None else None

    @model_validator(mode="after")
    def time_order(self) -> ContentQuery:
        if self.created_from is not None and self.created_before is not None and self.created_from >= self.created_before:
            raise ValueError("created_from must precede created_before")
        return self

    def filters(self) -> dict[str, str | int]:
        return self.model_dump(exclude={"page"}, exclude_none=True)


class ContentTags(StrictDTO):
    expected_revision: int = Field(ge=1)
    tags: list[Tag] = Field(max_length=16)

    @field_validator("tags")
    @classmethod
    def tag_values(cls, values: list[str]) -> list[str]:
        for value in values:
            canonical_tag(value)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate tags are not allowed")
        return values


class ContentOptionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tenant", "project", "key", "model", "workflow", "release"]
    page: int = Field(1, ge=1, le=10000)
    q: str = Field("", max_length=128)
    tenant_id: TenantId | None = None
    project_id: ProjectId | None = None
    workflow_id: WorkflowId | None = None
