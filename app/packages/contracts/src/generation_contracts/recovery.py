from __future__ import annotations

from typing import Literal

from pydantic import Field

from generation_contracts.worker import BootIdentity


class Reconciliation(BootIdentity):
    attempt_id: str
    previous_boot_id: str
    previous_fencing_token: int = Field(ge=1)
    association_id: str | None
    prompt_id: str | None
    observation: Literal["found", "absent", "unknown", "local_outputs"]
    source: Literal["queue_running", "queue_pending", "history", "local_outputs", "unknown"]
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
