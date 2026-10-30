from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from generation_contracts.catalog import ModelView
from generation_contracts.channel_queries import SEARCH_PATTERN
from generation_contracts.collections import TaskType
from generation_contracts.validation import StrictDTO, validate

ProjectId = Annotated[str, Field(pattern=r"^prj_[a-f0-9]{32}$")]
WorkflowId = Annotated[str, Field(pattern=r"^wf_[a-f0-9]{32}$")]
ReleaseId = Annotated[str, Field(pattern=r"^wfr_[a-f0-9]{32}$")]
ModelId = Annotated[str, Field(pattern=r"^mdl_[a-f0-9]{32}$")]
MappingKind = Literal["workflow", "release", "channel", "preset"]


class CatalogQueryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(1, ge=1, le=1000)
    q: str | None = Field(None, min_length=1, max_length=128, pattern=SEARCH_PATTERN)

    @field_validator("page", mode="before")
    @classmethod
    def integer_page(cls, value: Any) -> int:
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            return int(value)
        raise ValueError("Use integer page values")


class ModelCatalogQuery(CatalogQueryBase):
    project_id: ProjectId | None = None
    task_type: TaskType | None = None
    status: Literal["published", "unpublished"] | None = None
    model_id: ModelId | None = None


class ModelMappingQuery(CatalogQueryBase):
    kind: MappingKind
    project_id: ProjectId
    ids: list[str] = Field(default_factory=list, max_length=50)
    workflow_id: WorkflowId | None = None
    release_id: ReleaseId | None = None
    model_id: ModelId | None = None
    task_type: TaskType | None = None

    @model_validator(mode="after")
    def context(self) -> Self:
        if len(self.ids) != len(set(self.ids)) or (self.ids and self.page != 1):
            raise ValueError("Use unique exact IDs on page one")
        prefix = {"workflow": "wf", "release": "wfr", "channel": "ch"}.get(self.kind)
        if self.ids and (prefix is None or any(not re.fullmatch(prefix + r"_[a-f0-9]{32}", value) for value in self.ids)):
            raise ValueError("IDs must match the selected kind")
        if self.workflow_id is not None and self.kind != "release":
            raise ValueError("Workflow context is only valid for releases")
        if self.release_id is not None and self.kind != "preset":
            raise ValueError("Release context is only valid for presets")
        if self.model_id is not None and self.kind != "channel":
            raise ValueError("Model context is only valid for channels")
        if self.task_type is not None and self.kind not in ("workflow", "release"):
            raise ValueError("Task type is only valid for workflows and releases")
        if self.kind == "release" and not self.workflow_id and not self.ids:
            raise ValueError("Release browsing requires a workflow")
        if self.kind == "preset" and (not self.release_id or self.q is not None):
            raise ValueError("Presets require an exact release without a text search")
        return self


class MappingWorkflow(StrictDTO):
    kind: Literal["workflow"]
    id: WorkflowId
    name: str = Field(min_length=1, max_length=128)
    task_type: TaskType
    archived: bool


class MappingRelease(StrictDTO):
    kind: Literal["release"]
    id: ReleaseId
    workflow_id: WorkflowId
    task_type: TaskType
    version: int = Field(ge=1)
    validated: bool


class MappingChannel(StrictDTO):
    kind: Literal["channel"]
    id: str = Field(pattern=r"^ch_[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=128)
    enabled: bool
    draining: bool
    revision: int = Field(ge=1)
    gpu_id: str = Field(pattern=r"^gpu_[a-f0-9]{32}$")


class MappingDefaults(StrictDTO):
    n: int = Field(ge=1, le=1)
    quality: Literal["draft", "standard", "high"]
    size: str | None = Field(None, pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")
    seconds: int | float | None = Field(None, ge=0.001, le=600)


class MappingAudio(StrictDTO):
    format: Literal["wav", "flac", "mp3", "opus"]
    sample_rate: int = Field(ge=8000, le=96000)
    channels: int = Field(ge=1, le=2)


class MappingPreset(StrictDTO):
    kind: Literal["preset"]
    id: str = Field(pattern=r"^preset_[0-9]{1,5}$")
    release_id: ReleaseId
    task_type: TaskType
    defaults: MappingDefaults
    usable: bool
    audio: MappingAudio | None = None

    @model_validator(mode="after")
    def media(self) -> Self:
        audio = self.task_type == "text_to_audio"
        video = self.task_type in ("text_to_video", "references_to_video")
        model3d = self.task_type == "image_to_3d"
        if (audio and (not self.audio or self.defaults.size is not None)) or (not audio and (self.audio is not None or (self.defaults.size is None) != model3d)):
            raise ValueError("Preset metadata must match the media type")
        if (audio or video) != (self.defaults.seconds is not None):
            raise ValueError("Preset duration must match the media type")
        return self


MappingOption = Annotated[MappingWorkflow | MappingRelease | MappingChannel | MappingPreset, Field(discriminator="kind")]


class ModelMappingOptions(StrictDTO):
    kind: MappingKind
    project_id: ProjectId
    snapshot_at: int = Field(ge=0)
    page: int = Field(ge=1, le=1000)
    total: int = Field(ge=0)
    has_more: bool
    data: list[MappingOption] = Field(max_length=50)

    @model_validator(mode="after")
    def kinds_match(self) -> Self:
        if any(item.kind != self.kind for item in self.data):
            raise ValueError("Options must match the requested kind")
        validate("admin-model-mapping-options", self.model_dump(exclude_unset=True))
        return self


class ModelCatalogPage(BaseModel):
    snapshot_at: int
    page: int
    total: int
    has_more: bool
    data: list[ModelView]
