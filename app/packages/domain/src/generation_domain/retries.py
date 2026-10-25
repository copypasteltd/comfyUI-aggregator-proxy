"""Bounded retries of proven failures; ambiguous execution never creates a retry."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from generation_domain.values import fingerprint

RETRY_CODE = "preparation_transport_failure"
OOM_CODE = "comfy_oom"
RETRY_CODES = frozenset({RETRY_CODE, OOM_CODE})
RETRY_POLICY = "failure-retry-v1"
OOM_TYPES = frozenset({"torch.OutOfMemoryError", "torch.cuda.OutOfMemoryError"})
RETRY_DELAYS = (5, 15)
MAX_RETRIES = len(RETRY_DELAYS)


def confirmed_oom_evidence(payload: dict[str, Any], *, prompt_id: str | None, association_id: str | None) -> bool:
    proof = payload.get("retry_evidence")
    if (payload.get("error_code") != OOM_CODE or payload.get("execution_stop_confirmed") is not True
            or not prompt_id or not association_id or not isinstance(proof, dict)
            or set(proof) != {"kind", "prompt_id", "association_id", "exception_type", "history_digest", "queue_digest", "queue_absent"}):
        return False
    return bool(proof.get("kind") == "comfy-oom-v1" and proof.get("prompt_id") == prompt_id
                and proof.get("association_id") == association_id and proof.get("queue_absent") is True
                and isinstance(proof.get("exception_type"), str) and proof["exception_type"] in OOM_TYPES
                and all(isinstance(proof.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", proof[key]) for key in ("history_digest", "queue_digest"))
                and payload.get("evidence_digest") == fingerprint(proof))


def retry_after(*, state: str, count: int, error_code: str | None, execution_stopped: bool,
                evidence_digest: str | None, has_submission: bool, cancelled: bool,
                validation: bool, now: datetime, deadline: datetime,
                oom_failures: int = 0, oom_evidence: bool = False, alternative_gpu: bool = False) -> datetime | None:
    if (not 0 <= count < MAX_RETRIES or execution_stopped is not True
            or not isinstance(evidence_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_digest)
            or cancelled or validation):
        return None
    preparation = error_code == RETRY_CODE and state in ("leased", "preparing") and not has_submission
    oom = (error_code == OOM_CODE and state in ("running", "reconciling") and has_submission
           and oom_failures == 0 and oom_evidence is True and alternative_gpu is True)
    if not (preparation or oom):
        return None
    eligible = now + timedelta(seconds=RETRY_DELAYS[count])
    return eligible if eligible < deadline else None
