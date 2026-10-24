from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from generation_contracts.collections import TaskType
from generation_contracts.model_catalog import CatalogQueryBase, ProjectId, WorkflowId
from generation_contracts.validation import StrictDTO, validate


class WorkflowDirectoryQuery(CatalogQueryBase):
    project_id: ProjectId | None = None
    task_type: TaskType | None = None
    status: Literal["active", "archived"] | None = None
    workflow_id: WorkflowId | None = None

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-catalog-query", self.model_dump())
        return self


class ManagedWorkflow(StrictDTO):
    id: WorkflowId
    tenant_id: str = Field(pattern=r"^ten_[a-f0-9]{32}$")
    project_id: ProjectId
    name: str = Field(min_length=1, max_length=128)
    task_type: TaskType
    revision: int = Field(ge=1)
    archived: bool
    created_at: int = Field(ge=0)


class WorkflowDirectoryPage(StrictDTO):
    data: list[ManagedWorkflow] = Field(max_length=50)
    page: int = Field(ge=1, le=1000)
    has_more: bool
    snapshot_at: int = Field(ge=0)
    total: int = Field(ge=0)
    filters: dict[str, str | None]

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-catalog-page", self.model_dump())
        return self


class WorkflowSyncOptionsQuery(CatalogQueryBase):
    kind: Literal["release", "channel"]
    project_id: ProjectId
    workflow_id: WorkflowId
    ids: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-sync-options-query", self.model_dump())
        return self


class SyncReleaseOption(StrictDTO):
    kind: Literal["release"]
    id: str = Field(pattern=r"^wfr_[a-f0-9]{32}$")
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    validated: bool


class SyncChannelOption(StrictDTO):
    kind: Literal["channel"]
    id: str = Field(pattern=r"^ch_[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    enabled: bool
    draining: bool


class WorkflowSyncOptions(StrictDTO):
    kind: Literal["release", "channel"]
    project_id: ProjectId
    workflow_id: WorkflowId
    page: int = Field(ge=1, le=1000)
    snapshot_at: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool
    data: list[Annotated[SyncReleaseOption | SyncChannelOption, Field(discriminator="kind")]] = Field(max_length=50)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-sync-options", self.model_dump())
        return self
