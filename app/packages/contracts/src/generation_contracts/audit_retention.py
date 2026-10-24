from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AuditRetentionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    completed_at: int = Field(ge=0, le=4102444800)
    cutoff: int = Field(ge=0, le=4102444800)
    deleted: int = Field(ge=1, le=500)
    retention_days: int = Field(ge=1, le=3650)


class AuditRetentionView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    retention_days: int | None = Field(ge=1, le=3650)
    batch_limit: Literal[500]
    snapshot_at: int = Field(ge=0, le=4102444800)
    last_batch: AuditRetentionBatch | None

    @model_validator(mode="after")
    def configured_policy(self) -> AuditRetentionView:
        if self.enabled != (self.retention_days is not None):
            raise ValueError("Enabled retention requires explicit days")
        return self
