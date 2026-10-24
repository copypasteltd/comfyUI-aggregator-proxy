from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from generation_contracts.validation import KeyPolicy, StrictDTO

ROLE_PERMISSIONS: dict[str, list[str]] = json.loads(files("generation_contracts").joinpath("endpoint-catalog.json").read_text(encoding="utf-8"))["role_presets"]


class Login(StrictDTO):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ChannelCreate(StrictDTO):
    tenant_id: str
    project_id: str
    node_id: str
    gpu_id: str
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(max_length=2048)
    authorization: str | None = Field(default=None, max_length=4096)


class ChannelPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=2048)
    authorization: str | None = Field(default=None, max_length=4096)


class ChannelAction(StrictDTO):
    action: Literal["enable", "disable", "drain", "resume"]
    expected_revision: int = Field(ge=1)


class KeyCreate(StrictDTO):
    tenant_id: str
    project_id: str
    name: str = Field(min_length=1, max_length=128)
    policy: KeyPolicy
    expires_at: int | None = Field(default=None, ge=0)


class KeyPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    policy: KeyPolicy | None = None
    expires_at: int | None = Field(default=None, ge=0)


class KeyAction(StrictDTO):
    action: Literal["enable", "disable", "rotate"]
    expected_revision: int = Field(ge=1)
    overlap_seconds: int = Field(default=0, ge=0, le=3600)


class WorkflowCreate(StrictDTO):
    tenant_id: str
    project_id: str
    name: str = Field(min_length=1, max_length=128)
    task_type: Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"]
    draft: dict[str, Any]


class WorkflowPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    draft: dict[str, Any] | None = None


class ExpectedRevision(StrictDTO):
    expected_revision: int = Field(ge=1)


AdminRole = Literal["administrator", "operator", "workflow_maintainer", "observer"]


class AdminUserCreate(StrictDTO):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=12, max_length=1024)
    role: AdminRole

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().casefold()
        if not value or any(ord(c) < 32 or c.isspace() for c in value):
            raise ValueError("用户名不可为空或包含空白、控制字符")
        return value


class AdminUserPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    password: SecretStr | None = Field(default=None, min_length=12, max_length=1024)
    role: AdminRole | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> AdminUserPatch:
        if not any(value is not None for value in (self.password, self.role, self.enabled)):
            raise ValueError("至少指定一项账号变更")
        return self


class JobPriority(StrictDTO):
    expected_revision: int = Field(ge=1)
    priority: int = Field(ge=-100, le=100)
