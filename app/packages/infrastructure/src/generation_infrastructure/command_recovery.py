from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from generation_domain.errors import DomainError
from generation_domain.values import public_id

from generation_infrastructure.models import ManagementOperation, WorkerCommand

RECOVERY_KEY = "_control_recovery"


def recovery_reason(command: WorkerCommand, current_boot: UUID | None, now: datetime) -> str | None:
    if command.state != "running":
        return None
    if command.boot_id is None or command.lease_expires_at is None:
        return "unknown_lease"
    if command.boot_id != current_boot:
        return "previous_boot"
    return "expired" if command.lease_expires_at <= now else None


def recovery_proof(operation: ManagementOperation, command: WorkerCommand) -> dict[str, Any] | None:
    records = operation.summary.get(RECOVERY_KEY)
    value = records.get(public_id("cmd", command.id)) if isinstance(records, dict) else None
    return value if isinstance(value, dict) and value.get("protocol") == "command-reconciliation-v1" else None


def save_recovery_proof(operation: ManagementOperation, command: WorkerCommand, value: dict[str, Any]) -> None:
    records = operation.summary.get(RECOVERY_KEY, {})
    if not isinstance(records, dict) or (len(records) >= 100 and public_id("cmd", command.id) not in records):
        raise DomainError("command_recovery_limit", "该操作的恢复记录达到受控上限，请核对后处理", 409)
    operation.summary = {**operation.summary, RECOVERY_KEY: {**records, public_id("cmd", command.id): value}}
