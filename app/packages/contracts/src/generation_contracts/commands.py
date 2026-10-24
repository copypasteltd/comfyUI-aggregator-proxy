from __future__ import annotations

from typing import Any

from pydantic import Field

from generation_contracts.worker import BootIdentity


class CommandClaim(BootIdentity):
    boot_id: str


class CommandReceipt(BootIdentity):
    boot_id: str
    command_digest: str = Field(pattern="^[a-f0-9]{64}$")
    result: dict[str, Any]
