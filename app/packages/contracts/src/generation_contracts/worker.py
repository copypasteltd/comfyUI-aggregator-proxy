from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from generation_contracts.capacity import WorkerCapacity
from generation_contracts.validation import StrictDTO
from generation_contracts.workflow_catalogs import WorkflowExportObservation


class GpuInventory(StrictDTO):
    hardware_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    memory_bytes: int | None = Field(default=None, ge=1)


class Inventory(StrictDTO):
    gpus: list[GpuInventory] = Field(max_length=32)
    features: list[Literal["audio-output-v1", "remote-http-v1"]] = Field(default_factory=list, max_length=2)

    @field_validator("features")
    @classmethod
    def distinct_features(cls, value: list[Literal["audio-output-v1", "remote-http-v1"]]) -> list[Literal["audio-output-v1", "remote-http-v1"]]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate Worker features")
        return value


class BootIdentity(StrictDTO):
    boot_id: str

    @field_validator("boot_id")
    @classmethod
    def valid_boot(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("boot_id must be a canonical UUID")
        return value


class Register(BootIdentity):
    protocol_version: Literal["0.4.0"]
    boot_id: str
    inventory: Inventory


class QueueObservation(StrictDTO):
    reachable: bool
    queue_running: int | None = Field(default=None, ge=0, le=1000)
    queue_pending: int | None = Field(default=None, ge=0, le=1000)
    error_code: Literal["timeout", "unreachable", "response_too_large", "invalid_response"] | None = None

    @model_validator(mode="after")
    def complete_counts(self) -> QueueObservation:
        if self.reachable and (self.queue_running is None or self.queue_pending is None or self.error_code is not None):
            raise ValueError("reachable observations require both counts and no error")
        if not self.reachable and (self.queue_running is not None or self.queue_pending is not None):
            raise ValueError("unreachable observations cannot report counts")
        return self


class ChannelObservation(StrictDTO):
    channel_id: str = Field(pattern=r"^ch_[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    observed_ago_ms: int = Field(ge=0, le=60000)
    observation: QueueObservation
    workflow_export: WorkflowExportObservation | None = None


class Heartbeat(BootIdentity):
    protocol_version: Literal["0.4.0"]
    boot_id: str
    observations: list[ChannelObservation] = Field(default_factory=list, max_length=100)
    capacity: WorkerCapacity | None = None


class Claim(BootIdentity):
    protocol_version: Literal["0.4.0"]
    boot_id: str
    request_id: str = Field(min_length=16, max_length=128, pattern=r"^[\x21-\x7e]+$")


class Renew(BootIdentity):
    protocol_version: Literal["0.4.0"]
    boot_id: str
    fencing_token: int = Field(ge=1)


class AttemptStatus(StrictDTO):
    protocol_version: Literal["0.4.0"]
    server_time: int
    job_id: str
    attempt_id: str
    node_id: str
    boot_id: str
    fencing_token: int
    status: str
    updated_revision: int
    last_journal_seq: int
    is_current: bool
    lease_expires_at: int
    lease_valid: bool
    cancel_requested: bool
    closed: bool
    generation_released: bool
    gpu_occupied: bool
    occupancy_uncertain: bool
    reconciliation_required: bool
