from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from generation_contracts.operational import AttemptTimingView, DurationView, QueueStatisticsQuery
from generation_infrastructure.models import Attempt, Job
from generation_infrastructure.operational import attempt_timing, duration, job_timing, milliseconds

ORIGIN = datetime(2026, 10, 24, tzinfo=UTC)
NOW = ORIGIN + timedelta(seconds=120)


@pytest.mark.parametrize(("start", "end", "opened", "status", "measured", "reason"), [
    (0, 0, False, "complete", 0, None), (0.0009, 0.0011, False, "complete", 1, None),
    (0, 12.345, False, "complete", 12345, None), (0, None, True, "open", 120000, None),
    (None, None, True, "unknown", None, "missing_record"), (0, None, False, "unknown", None, "missing_record"),
    (3, 2, False, "unknown", None, "clock_order"), (121, None, True, "unknown", None, "clock_order"),
    (0, 121, False, "unknown", None, "clock_order"),
])
def test_duration_requires_observed_ordered_endpoints(start: float | None, end: float | None, opened: bool, status: str, measured: int | None, reason: str | None) -> None:
    result = DurationView.model_validate(duration(ORIGIN + timedelta(seconds=start) if start is not None else None, ORIGIN + timedelta(seconds=end) if end is not None else None, NOW, open_span=opened))
    assert (result.status, result.milliseconds, result.reason) == (status, measured, reason)


@pytest.mark.parametrize("data", [
    {"status": "unknown"}, {"status": "unknown", "reason": "unmeasured", "milliseconds": 0},
    {"status": "complete", "milliseconds": 0, "started_at_ms": 100},
    {"status": "complete", "milliseconds": 1, "started_at_ms": 100, "ended_at_ms": 100},
    {"status": "complete", "milliseconds": -1, "started_at_ms": 100, "ended_at_ms": 99},
    {"status": "open", "milliseconds": 0, "started_at_ms": 100, "ended_at_ms": 100},
    {"status": "open", "milliseconds": 0, "started_at_ms": 100, "reason": "missing_record"},
])
def test_duration_contract_rejects_invented_or_inconsistent_measurements(data: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DurationView.model_validate(data)


def test_job_clock_keeps_retries_and_legacy_unknowns_distinct() -> None:
    job = Job(state="running", accepted_at=ORIGIN, current_attempt_id=uuid4())
    result = job_timing(job, ORIGIN + timedelta(seconds=10), NOW)
    assert result["elapsed"]["milliseconds"] == 120000 and result["elapsed"]["status"] == "open"
    assert result["queue"]["milliseconds"] == 10000 and result["snapshot_at_ms"] == milliseconds(NOW)
    job.state = "succeeded"
    assert job_timing(job, None, NOW)["elapsed"] == {"status": "unknown", "reason": "missing_record"}
    job.terminal_at = ORIGIN + timedelta(seconds=60)
    assert job_timing(job, None, NOW)["queue"]["milliseconds"] == 60000
    job.accepted_at = None
    assert job_timing(job, ORIGIN, NOW)["queue"]["status"] == "unknown"


def test_attempt_phase_observations_and_current_open_spans() -> None:
    attempt = Attempt(id=uuid4(), created_at=ORIGIN)
    job = Job(state="preparing", current_attempt_id=attempt.id)
    initial = AttemptTimingView.model_validate(attempt_timing(attempt, job, NOW))
    assert initial.preparing.status == initial.elapsed.status == "open"
    assert initial.submission.status == initial.generation_collection.status == initial.upload_verification.status == "unknown"
    attempt.submit_intent_at = ORIGIN + timedelta(seconds=3)
    assert attempt_timing(attempt, job, NOW)["submission"]["status"] == "open"
    attempt.submitted_at = ORIGIN + timedelta(seconds=5)
    attempt.outputs_collected_at = ORIGIN + timedelta(seconds=80)
    attempt.generation_released_at = ORIGIN + timedelta(seconds=81)
    result = AttemptTimingView.model_validate(attempt_timing(attempt, job, NOW))
    assert result.preparing.milliseconds == 3000 and result.submission.milliseconds == 2000
    assert result.generation_collection.milliseconds == 75000 and result.upload_verification.milliseconds == 39000
    assert result.upload_verification.status == "open" and result.model_loading.reason == "unmeasured"
    attempt.closed_at = ORIGIN + timedelta(seconds=100)
    assert attempt_timing(attempt, job, NOW)["upload_verification"]["milliseconds"] == 19000
    attempt.outputs_collected_at = None
    assert attempt_timing(attempt, job, NOW)["generation_collection"]["milliseconds"] == 76000
    attempt.submitted_at = None
    assert attempt_timing(attempt, job, NOW)["generation_collection"]["status"] == "unknown"


@pytest.mark.parametrize("state", ["succeeded", "failed", "admission_pending"])
def test_job_wait_never_outlives_terminal_or_invents_admission(state: str) -> None:
    job = Job(state=state, accepted_at=ORIGIN, terminal_at=ORIGIN + timedelta(seconds=60) if state != "admission_pending" else None)
    result = job_timing(job, ORIGIN + timedelta(seconds=61) if state != "admission_pending" else None, NOW)
    assert result["queue"] == {"status": "unknown", "reason": "missing_record" if state == "admission_pending" else "clock_order"}
    if state == "admission_pending":
        assert result["elapsed"]["status"] == "unknown"


@pytest.mark.parametrize("values", [{"window": "30d"}, {"task_type": "speech"}, {"page": 10001}, {"created_from": 9, "created_before": 9}, {"prompt": "must not be a filter"}])
def test_statistics_query_is_bounded_and_strict(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        QueueStatisticsQuery.model_validate(values)
