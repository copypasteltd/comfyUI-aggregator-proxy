from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from generation_contracts.responses import Page
from generation_contracts.validation import ResourceLimits, StrictDTO, validate


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFC", value).strip()
    if not value or any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("名称不可为空或包含控制字符")
    return value


class ScopeLimits(ResourceLimits):
    @model_validator(mode="after")
    def check_limits(self) -> ScopeLimits:
        validate("resource-limits", self.model_dump())
        return self


class TenantCreate(StrictDTO):
    name: str = Field(min_length=1, max_length=128)
    limits: ScopeLimits
    enabled: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def name_value(cls, value: str) -> str:
        return normalized_name(value) if isinstance(value, str) else value


class ProjectCreate(TenantCreate):
    tenant_id: str = Field(pattern=r"^ten_[0-9a-f]{32}$")


class ScopePatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    limits: ScopeLimits | None = None
    enabled: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def name_value(cls, value: str | None) -> str | None:
        return normalized_name(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_change(self) -> ScopePatch:
        if self.name is None and self.limits is None and self.enabled is None:
            raise ValueError("至少指定一项租户或项目变更")
        return self


class ScopeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1, le=10_000)
    q: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    tenant_id: str | None = Field(default=None, pattern=r"^ten_[0-9a-f]{32}$")
    project_id: str | None = Field(default=None, pattern=r"^prj_[0-9a-f]{32}$")

    @field_validator("q", mode="before")
    @classmethod
    def query_value(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            value = unicodedata.normalize("NFC", value).strip()
            if any(unicodedata.category(char).startswith("C") for char in value):
                raise ValueError("搜索不可包含控制字符")
            return value or None
        return value


class ScopeUsage(BaseModel):
    period: str
    daily_jobs: int
    unfinished_jobs: int
    generating_jobs: int
    daily_image_count: int
    daily_video_milliseconds: int
    daily_audio_milliseconds: int = 0
    daily_mesh_count: int = 0


class TenantView(BaseModel):
    id: str
    name: str
    enabled: bool
    limits: ResourceLimits
    revision: int
    created_at: int
    usage: ScopeUsage
    observed_at: int


class ProjectView(TenantView):
    tenant_id: str
    tenant_name: str
    tenant_enabled: bool
    tenant_limits: ResourceLimits


class ScopeOption(BaseModel):
    tenant_id: str
    tenant_name: str
    project_id: str
    project_name: str
    tenant_enabled: bool
    project_enabled: bool


class ScopePage[T](Page[T]):
    default_limits: ResourceLimits
    observed_at: int
