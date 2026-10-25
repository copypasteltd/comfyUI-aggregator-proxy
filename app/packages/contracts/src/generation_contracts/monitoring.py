from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MonitoringThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    queue_age_seconds: int = Field(default=300, ge=30, le=86400)
    pending_age_seconds: int = Field(default=60, ge=10, le=3600)
    settlement_age_seconds: int = Field(default=300, ge=30, le=86400)
    node_stale_seconds: int = Field(default=30, ge=15, le=3600)
    http_error_ratio: float = Field(default=0.05, ge=0, le=1)
    minimum_requests: int = Field(default=20, ge=1, le=1000000)
    alert_for_seconds: int = Field(default=120, ge=0, le=3600)


class MonitoringConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol: Literal["monitoring-v1"] = "monitoring-v1"
    enabled: bool
    credential_slots: int = Field(ge=0, le=2)
    source_network_count: int = Field(ge=1, le=32)
    cache_seconds: Literal[5] = 5
    database_budget_seconds: Literal[2] = 2
    recent_window_seconds: Literal[3600] = 3600
    capacity_node_limit: Literal[256] = 256
    maximum_exposition_bytes: Literal[2097152] = 2097152
    thresholds: MonitoringThresholds

    @model_validator(mode="after")
    def enabled_credentials(self) -> MonitoringConfiguration:
        if self.enabled != (self.credential_slots > 0):
            raise ValueError("Monitoring requires an explicit credential")
        return self
