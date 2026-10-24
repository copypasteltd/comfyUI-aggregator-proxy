from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from generation_contracts.collections import TaskType
from generation_contracts.model_catalog import ProjectId, WorkflowId
from generation_contracts.validation import StrictDTO, validate

GenerationKind = Literal["models", "workflow_releases", "channels"]
DirectoryMode = Literal["browse", "resolve"]


class KeyGenerationQuery(StrictDTO):
    kind: GenerationKind
    mode: DirectoryMode
    project_id: ProjectId
    page: int = Field(1, ge=1, le=1000)
    q: str = Field("", max_length=128)
    ids: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("key-generation-options-query", self.model_dump())
        return self


class KeyGenerationModel(StrictDTO):
    kind: Literal["models"]
    value: str = Field(min_length=1, max_length=128)
    id: str = Field(pattern=r"^mdl_[a-f0-9]{32}$")
    task_type: TaskType
    published: bool


class KeyGenerationRelease(StrictDTO):
    kind: Literal["workflow_releases"]
    value: str = Field(pattern=r"^wfr_[a-f0-9]{32}$")
    workflow_id: WorkflowId
    name: str = Field(min_length=1, max_length=128)
    task_type: TaskType
    version: int = Field(ge=1)
    validated: bool
    archived: bool


class KeyGenerationChannel(StrictDTO):
    kind: Literal["channels"]
    value: str = Field(pattern=r"^ch_[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=128)
    enabled: bool
    draining: bool
    archived: bool


KeyGenerationOption = Annotated[KeyGenerationModel | KeyGenerationRelease | KeyGenerationChannel, Field(discriminator="kind")]


class KeyGenerationOptions(StrictDTO):
    kind: GenerationKind
    mode: DirectoryMode
    project_id: ProjectId
    page: int = Field(ge=1, le=1000)
    snapshot_at: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool
    data: list[KeyGenerationOption] = Field(max_length=1000)
    unresolved_ids: list[str] = Field(max_length=1000)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("key-generation-options", self.model_dump())
        return self
