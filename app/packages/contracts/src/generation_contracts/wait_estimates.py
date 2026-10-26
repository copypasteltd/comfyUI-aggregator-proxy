from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegative = Annotated[int, Field(ge=0, strict=True)]
WaitReason = Literal["not_queued", "validation_task", "no_ready_route", "stale_observation", "uncertain_execution", "insufficient_samples", "quota_blocked", "deadline", "capacity_limit", "execution_unavailable"]


class WaitEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    method: Literal["queue-runtime-scenarios-v1"] = "queue-runtime-scenarios-v1"
    job_id: str = Field(pattern=r"^job_[0-9a-f]{32}$")
    snapshot_at_ms: NonNegative
    valid_until_ms: NonNegative
    status: Literal["estimated", "unknown", "not_waiting"]
    reason: WaitReason | None
    lower_wait_ms: Annotated[int, Field(ge=0, le=86400000, strict=True)] | None = None
    upper_wait_ms: Annotated[int, Field(ge=0, le=86400000, strict=True)] | None = None
    sample_count: Annotated[int, Field(ge=0, le=12800, strict=True)] = 0
    minimum_samples: Literal[10] = 10
    minimum_route_samples: Annotated[int, Field(ge=0, le=200, strict=True)] = 0
    oldest_sample_at_ms: NonNegative | None = None
    newest_sample_at_ms: NonNegative | None = None
    history_window_seconds: Literal[86400] = 86400
    queued_jobs: Annotated[int, Field(ge=0, le=500, strict=True)] = 0
    physical_gpus: Annotated[int, Field(ge=0, le=64, strict=True)] = 0

    @model_validator(mode="after")
    def coherent(self) -> WaitEstimate:
        if self.valid_until_ms != self.snapshot_at_ms + 15000:
            raise ValueError("Estimate validity must be a fifteen-second snapshot")
        if self.status == "estimated":
            if self.reason is not None or self.lower_wait_ms is None or self.upper_wait_ms is None or self.lower_wait_ms > self.upper_wait_ms:
                raise ValueError("An estimated interval requires ordered bounds")
            if self.sample_count < 10 or self.minimum_route_samples < 10 or self.oldest_sample_at_ms is None or self.newest_sample_at_ms is None:
                raise ValueError("An estimate requires sufficient measured samples")
        elif self.reason is None or self.lower_wait_ms is not None or self.upper_wait_ms is not None:
            raise ValueError("Unknown estimates cannot invent numerical bounds")
        if (self.status == "not_waiting") != (self.reason == "not_queued"):
            raise ValueError("Only nonqueued jobs use not_waiting")
        if (self.oldest_sample_at_ms is None) != (self.newest_sample_at_ms is None):
            raise ValueError("Sample timestamps must be paired")
        if self.sample_count < self.minimum_route_samples or (self.sample_count > 0) != (self.oldest_sample_at_ms is not None):
            raise ValueError("Sample counts and timestamps must describe the same observations")
        if self.oldest_sample_at_ms is not None and self.newest_sample_at_ms is not None and not max(0, self.snapshot_at_ms - 86400000) <= self.oldest_sample_at_ms <= self.newest_sample_at_ms <= self.snapshot_at_ms:
            raise ValueError("Samples must belong to the current history window")
        return self
