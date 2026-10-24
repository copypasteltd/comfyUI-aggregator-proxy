from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from generation_contracts.queue_queries import QueueQuery
from generation_infrastructure.models import Job
from generation_infrastructure.queue_queries import waiting_times


@pytest.mark.parametrize(("accepted", "first", "terminal", "state", "expected"), [(0, None, None, "queued", 120), (0, 0.5, None, "running", 0), (0, 30, 90, "succeeded", 30), (0, None, 17, "cancelled", 17), (None, None, None, "queued", None), (121, None, None, "queued", None), (0, -1, None, "running", None), (0, 121, None, "running", None), (0, 30, 17, "succeeded", None), (0, None, -1, "cancelled", None), (0, None, None, "failed", None), (0, None, None, "admission_pending", None)])
def test_actual_queue_wait_requires_consistent_evidence(accepted: float | None, first: float | None, terminal: float | None, state: str, expected: int | None) -> None:
    origin = datetime(2026, 10, 23, tzinfo=UTC)
    job = Job(state=state, release_id=uuid4(), accepted_at=origin + timedelta(seconds=accepted) if accepted is not None else None, terminal_at=origin + timedelta(seconds=terminal) if terminal is not None else None, current_attempt_id=uuid4() if first is not None else None)
    assert waiting_times(job, origin + timedelta(seconds=first) if first is not None else None, origin + timedelta(seconds=120))["queue_wait_seconds"] == expected


@pytest.mark.parametrize("data", [{"created_from": 1, "created_before": 1}, {"created_from": 2, "created_before": 1}, {"created_from": 1.1}, {"model": " padded "}, {"page": 10001}, {"unknown": "ignored"}])
def test_query_rejects_ambiguous_ranges_and_unknown_fields(data: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        QueueQuery.model_validate(data)
