from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from generation_contracts.validation import StrictDTO


class DeviceRouting(StrictDTO):
    policy: Literal["first_ready", "balanced_ready"] = "first_ready"
    weights: dict[str, Annotated[StrictInt, Field(ge=1, le=1000)]] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def known_policy(self) -> DeviceRouting:
        if self.policy == "first_ready" and self.weights:
            raise ValueError("原领取策略不接受设备权重")
        if any(not key.startswith("gpu_") or len(key) != 36 or any(c not in "0123456789abcdef" for c in key[4:]) for key in self.weights):
            raise ValueError("权重必须使用有效的物理GPU编号")
        return self


class DevicePoolMemberView(StrictDTO):
    gpu_id: str
    weight: int
    assigned_count: int
    last_sequence: int


class DevicePoolView(StrictDTO):
    model_id: str
    model_revision: int
    policy: Literal["first_ready", "balanced_ready"]
    pool_id: str | None
    sequence: int
    members: list[DevicePoolMemberView]
