from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from generation_contracts.queue_queries import ProjectId, TenantId
from generation_contracts.validation import StrictDTO, validate


class KeyPolicyOptionQuery(StrictDTO):
    mode: Literal["browse", "resolve"]
    kind: Literal["project", "creator_key"]
    tenant_id: TenantId
    project_ids: list[ProjectId] = Field(default_factory=list, max_length=1000)
    ids: list[str] = Field(default_factory=list, max_length=1000)
    page: int = Field(default=1, ge=1, le=10000)
    q: str = Field(default="", max_length=128)

    @field_validator("q", mode="before")
    @classmethod
    def canonical_query(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        value = unicodedata.normalize("NFC", value).strip()
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("搜索不可包含控制字符")
        return value

    @model_validator(mode="after")
    def frozen_shape(self) -> KeyPolicyOptionQuery:
        validate("key-policy-options-query", self.model_dump())
        return self


class KeyPolicyOption(BaseModel):
    id: str
    name: str
    tenant_id: str
    tenant_name: str
    project_id: str
    project_name: str
    tenant_enabled: bool
    project_enabled: bool
    enabled: bool
    lifecycle: Literal["active", "disabled", "expired", "archived"]
    origin: Literal["local", "gateway"] | None


class KeyPolicyOptions(BaseModel):
    data: list[KeyPolicyOption]
    unresolved_ids: list[str]
    page: int
    has_more: bool
    observed_at: int
