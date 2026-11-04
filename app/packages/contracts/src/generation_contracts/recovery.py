from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO
from generation_contracts.worker import BootIdentity

HOST_RESTART_PROTOCOL: Literal["windows-host-uptime-reset-v1"] = "windows-host-uptime-reset-v1"


class HostRecoveryCheckpoint(StrictDTO):
    protocol: Literal["windows-host-uptime-reset-v1"]
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    host_identity_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    uptime_ms: int = Field(strict=True, ge=1, le=2**63 - 1)
    gpu_hardware_id: str = Field(min_length=1, max_length=128)
    channel_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    locality: Literal["same-host-tcp-v1"]


class HostRestartProof(StrictDTO):
    protocol: Literal["windows-host-uptime-reset-v1"]
    checkpoint_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    host_identity_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    uptime_ms: int = Field(strict=True, ge=1, le=2**63 - 1)
    comfy_observation: Literal["absent"]


class Reconciliation(BootIdentity):
    attempt_id: str
    previous_boot_id: str
    previous_fencing_token: int = Field(ge=1)
    association_id: str | None
    prompt_id: str | None
    observation: Literal["found", "absent", "unknown", "local_outputs", "host_restarted"]
    source: Literal["queue_running", "queue_pending", "history", "local_outputs", "unknown", "native_host_uptime"]
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    host_checkpoint: HostRecoveryCheckpoint | None = None
    host_restart: HostRestartProof | None = None

    @model_validator(mode="after")
    def host_evidence(self) -> Reconciliation:
        if self.observation == "host_restarted":
            if self.source != "native_host_uptime" or self.host_restart is None or self.host_checkpoint is not None or self.manifest_digest is not None:
                raise ValueError("host restart requires its own proof and source")
        elif self.host_restart is not None or self.source == "native_host_uptime":
            raise ValueError("host restart proof cannot accompany another observation")
        if self.host_checkpoint is not None and (self.observation != "absent" or self.source != "unknown" or self.manifest_digest is not None):
            raise ValueError("checkpoint requires a successful absent observation")
        return self
