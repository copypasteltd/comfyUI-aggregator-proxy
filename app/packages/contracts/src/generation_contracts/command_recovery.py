from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO
from generation_contracts.worker import BootIdentity


class CommandReconciliation(BootIdentity):
    protocol: Literal["command-reconciliation-v1"]
    command_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation: Literal["completed", "unknown"]
    result: dict[str, Any] | None

    @model_validator(mode="after")
    def proof_matches_observation(self) -> CommandReconciliation:
        if self.observation == "unknown":
            if self.result is not None:
                raise ValueError("Unknown execution cannot supply a completed result")
        elif self.result is None or type(self.result.get("success")) is not bool or "_control_recovery" in self.result:
            raise ValueError("A completed result requires a boolean success and cannot supply server recovery metadata")
        return self


class CommandReconciliationResult(StrictDTO):
    protocol: Literal["command-reconciliation-v1"]
    command_id: str = Field(pattern=r"^cmd_[a-f0-9]{32}$")
    acknowledged: bool
    resolution: Literal["already_completed", "result_requires_revalidation", "evidence_required"]
    command_status: Literal["running", "succeeded", "failed"]
    server_time: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> CommandReconciliationResult:
        if self.resolution == "evidence_required":
            if self.acknowledged or self.command_status != "running":
                raise ValueError("Unknown execution remains unacknowledged and running")
        elif not self.acknowledged or self.command_status == "running":
            raise ValueError("Acknowledgement requires a closed command")
        if self.resolution == "result_requires_revalidation" and self.command_status != "failed":
            raise ValueError("Reconciliation does not restore an old environment approval")
        return self


class CommandReconciliationTarget(StrictDTO):
    command_id: str = Field(pattern=r"^cmd_[a-f0-9]{32}$")
    command_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: Literal["expired", "previous_boot", "unknown_lease"]


class CommandNext(StrictDTO):
    command: dict[str, Any] | None
    reconciliation: CommandReconciliationTarget | None = None

    @model_validator(mode="after")
    def one_action(self) -> CommandNext:
        if self.command is not None and self.reconciliation is not None:
            raise ValueError("A command and recovery target cannot be issued together")
        return self
