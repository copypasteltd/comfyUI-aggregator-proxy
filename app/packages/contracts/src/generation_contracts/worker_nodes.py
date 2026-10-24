from __future__ import annotations

import unicodedata
from typing import Annotated, Literal

from pydantic import Field, field_validator

from generation_contracts.responses import GpuView
from generation_contracts.validation import StrictDTO

NodeName = Annotated[str, Field(min_length=1, max_length=128)]


class WorkerNodeCreate(StrictDTO):
    name: NodeName

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("节点名称不能包含控制字符")
        value = unicodedata.normalize("NFC", value).strip()
        if not value:
            raise ValueError("节点名称不能为空或包含控制字符")
        return value


class WorkerNodePatch(WorkerNodeCreate):
    expected_revision: int = Field(strict=True, ge=1)


class WorkerNodeAction(StrictDTO):
    action: Literal["enable", "disable", "rotate_credential", "archive"]
    expected_revision: int = Field(strict=True, ge=1)


class WorkerNodeActivity(StrictDTO):
    open_attempts: int = Field(ge=0)
    held_gpus: int = Field(ge=0)
    uncertain_gpus: int = Field(ge=0)
    management_holds: int = Field(ge=0)
    running_commands: int = Field(ge=0)
    live_channels: int = Field(ge=0)


class WorkerNodeView(StrictDTO):
    id: str
    name: str
    enabled: bool
    archived_at: int | None
    revision: int = Field(ge=1)
    created_at: int
    observed_at: int | None
    boot_registered: bool
    credential_hint: str
    gpu_count: int = Field(ge=0)
    activity: WorkerNodeActivity
    can_disable: bool
    can_rotate: bool
    can_archive: bool


class WorkerNodePage(StrictDTO):
    snapshot_at: int
    data: list[WorkerNodeView] = Field(max_length=50)
    page: int = Field(ge=1)
    has_more: bool
    total: int = Field(ge=0)


class WorkerNodeChannel(StrictDTO):
    id: str
    name: str
    enabled: bool
    draining: bool


class WorkerNodeDetail(WorkerNodeView):
    gpus: list[GpuView] = Field(max_length=100)
    channels: list[WorkerNodeChannel] = Field(max_length=100)
    gpus_truncated: bool
    channels_truncated: bool


class WorkerNodeMutation(StrictDTO):
    node: WorkerNodeView
    secret_available: bool
    secret: str | None = None
