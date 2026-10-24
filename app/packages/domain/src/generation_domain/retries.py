"""Retry only a proven pre-submission transport failure within the same job."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

RETRY_CODE = "preparation_transport_failure"
RETRY_DELAYS = (5, 15)
MAX_RETRIES = len(RETRY_DELAYS)


def retry_after(*, state: str, count: int, error_code: str | None, execution_stopped: bool,
                evidence_digest: str | None, has_submission: bool, cancelled: bool,
                validation: bool, now: datetime, deadline: datetime) -> datetime | None:
    if (state not in ("leased", "preparing") or not 0 <= count < MAX_RETRIES
            or error_code != RETRY_CODE or execution_stopped is not True
            or not isinstance(evidence_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_digest)
            or has_submission or cancelled or validation):
        return None
    eligible = now + timedelta(seconds=RETRY_DELAYS[count])
    return eligible if eligible < deadline else None
