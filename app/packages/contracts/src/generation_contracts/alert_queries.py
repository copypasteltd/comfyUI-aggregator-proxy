from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from generation_contracts.collections import TaskType
from generation_contracts.validation import StrictDTO

AlertKind = Literal["gpu_occupancy_uncertain", "job_reconciling", "node_heartbeat_stale", "channel_observation_stale", "channel_config_unverified", "job_waiting_over_5m", "operation_failed", "gateway_settlement_unacked", "worker_storage_pressure", "worker_memory_pressure", "worker_upload_backlog", "worker_capacity_unavailable", "spec_isolation", "job_oom_recent", "job_output_invalid_recent"]
AlertSeverity = Literal["critical", "warning"]


class AlertQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(1, ge=1, le=1000)
    kind: AlertKind | None = None
    severity: AlertSeverity | None = None
    resource_id: str | None = Field(None, pattern=r"^(?:gpu|job|node|ch|op|evt|iso)_[a-f0-9]{32}$")

    @field_validator("page", mode="before")
    @classmethod
    def integers(cls, value: Any) -> int:
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            return int(value)
        raise ValueError("Use integer query values")


class AlertFailure(StrictDTO):
    code: Literal["comfy_oom", "comfy_output_invalid"]
    task_type: TaskType
    model: str = Field(min_length=1, max_length=128)
    release_id: str = Field(pattern=r"^wfr_[a-f0-9]{32}$")
    attempt_id: str = Field(pattern=r"^att_[a-f0-9]{32}$")
    channel_id: str = Field(pattern=r"^ch_[a-f0-9]{32}$")
