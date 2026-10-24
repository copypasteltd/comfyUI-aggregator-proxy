from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO

DeletionKind = Literal["active_jobs", "open_attempts", "pending_commands", "published_models", "gpu_occupancy", "gpu_management_hold"]
RESOURCE_PREFIXES: dict[DeletionKind, str] = {"active_jobs": "job", "open_attempts": "job", "pending_commands": "op", "published_models": "mdl", "gpu_occupancy": "job", "gpu_management_hold": "gpu"}


class ChannelDeletionBlocker(StrictDTO):
    kind: DeletionKind
    resource_ids: list[str] = Field(min_length=1, max_length=10)
    has_more: bool

    @model_validator(mode="after")
    def check_resources(self) -> ChannelDeletionBlocker:
        pattern = RESOURCE_PREFIXES[self.kind] + r"_[a-f0-9]{32}"
        if len(set(self.resource_ids)) != len(self.resource_ids) or any(re.fullmatch(pattern, value) is None for value in self.resource_ids):
            raise ValueError("Invalid or repeated deletion blocker identifier")
        return self


class ChannelDeletionCheck(StrictDTO):
    channel_id: str = Field(pattern=r"^ch_[a-f0-9]{32}$")
    node_id: str = Field(pattern=r"^node_[a-f0-9]{32}$")
    gpu_id: str = Field(pattern=r"^gpu_[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    snapshot_at: int = Field(ge=0)
    deleted: bool
    can_delete: bool
    blockers: list[ChannelDeletionBlocker] = Field(max_length=6)

    @model_validator(mode="after")
    def check_state(self) -> ChannelDeletionCheck:
        if len({blocker.kind for blocker in self.blockers}) != len(self.blockers):
            raise ValueError("Repeated deletion blocker kind")
        if self.can_delete != (not self.deleted and not self.blockers) or self.deleted and self.blockers:
            raise ValueError("Deletion state contradicts its blockers")
        return self
