from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from generation_contracts.dispatch import DispatchPolicy
from generation_contracts.responses import QueueView
from generation_contracts.validation import JobCreate
from generation_domain.dispatch import (
    DispatchCandidate,
    TenantService,
    effective_priority,
    estimated_service,
    order_candidates,
)
from jsonschema import Draft202012Validator
from pydantic import ValidationError

NOW = datetime(2026, 10, 24, tzinfo=UTC)
CONTRACTS = Path(__file__).resolve().parents[3] / "docs/contracts"
CASES = [item for item in json.loads((CONTRACTS / "fixtures.json").read_text(encoding="utf-8"))["cases"] if item["schema"] == "dispatch-policy.schema.json"]


def candidate(identifier, tenant=1, priority=0, seconds=0):
    return DispatchCandidate(UUID(int=identifier), UUID(int=tenant), priority, NOW - timedelta(seconds=seconds))


def test_service_balances_estimated_work_instead_of_job_count():
    rows = [candidate(1), candidate(2, tenant=2)]
    states = {row.tenant_id: TenantService() for row in rows}
    clock = 0
    counts = {1: 0, 2: 0}
    costs = {1: 60_000, 2: 300_000}
    for sequence in range(1, 121):
        identifier = order_candidates(rows, states, clock, NOW)[0].int
        tenant = UUID(int=identifier)
        clock = max(clock, states[tenant].virtual_milliseconds)
        states[tenant] = TenantService(clock + costs[identifier], sequence)
        counts[identifier] += 1
    assert counts == {1: 100, 2: 20}
    assert states[UUID(int=1)].virtual_milliseconds == states[UUID(int=2)].virtual_milliseconds


def test_new_or_idle_tenant_joins_the_current_clock_without_historical_catchup():
    rows = [candidate(1), candidate(2, tenant=2)]
    states = {UUID(int=1): TenantService(9_000_000, 42), UUID(int=2): TenantService(1_000, 3)}
    assert order_candidates(rows, states, 9_000_000, NOW) == [UUID(int=2), UUID(int=1)]
    assert order_candidates(rows, {UUID(int=1): states[UUID(int=1)]}, 9_000_000, NOW)[0] == UUID(int=2)


@pytest.mark.parametrize(("priority", "wait", "expected"), [(-100, 0, -100), (0, 4.999, 0), (0, 5, 1), (99, 5, 100), (100, 10_000, 100), (-100, 1000, 100), (5, -60, 5)])
def test_aging_boundaries_and_future_dates(priority, wait, expected):
    assert effective_priority(priority, NOW - timedelta(seconds=wait), NOW) == expected


def test_aged_cold_work_can_overtake_new_high_priority_work_in_its_tenant():
    old = candidate(2, priority=-100, seconds=1000)
    new = candidate(1, priority=100)
    assert order_candidates([new, old], {}, 0, NOW) == [old.job_id, new.job_id]


def test_priority_cannot_buy_more_tenant_service_share():
    rows = [candidate(1, priority=100), candidate(2, tenant=2, priority=-100)]
    states = {UUID(int=1): TenantService(300_000, 1)}
    assert order_candidates(rows, states, 0, NOW)[0] == UUID(int=2)


def test_overdue_tenants_rotate_and_fresh_arrivals_eventually_join():
    rows = [candidate(1, seconds=300), candidate(2, tenant=2, seconds=300), candidate(3, tenant=3)]
    states = {UUID(int=1): TenantService(7_200_000, 10), UUID(int=2): TenantService(6_000_000, 8), UUID(int=3): TenantService()}
    assert order_candidates(rows, states, 0, NOW)[0] == UUID(int=2)
    states[UUID(int=2)] = TenantService(6_300_000, 11)
    assert order_candidates(rows, states, 0, NOW)[0] == UUID(int=1)
    states[UUID(int=1)] = TenantService(7_500_000, 12)
    assert order_candidates(rows, states, 0, NOW + timedelta(seconds=300))[0] == UUID(int=3)


def test_overdue_threshold_is_inclusive_and_order_is_stable():
    rows = [candidate(2, tenant=2, seconds=299.999), candidate(1)]
    states = {UUID(int=2): TenantService(7_200_000, 1)}
    assert order_candidates(rows, states, 0, NOW)[0] == UUID(int=1)
    assert order_candidates(rows, states, 0, NOW + timedelta(milliseconds=1))[0] == UUID(int=2)
    assert order_candidates([candidate(2), candidate(1)], {}, 0, NOW) == [UUID(int=1), UUID(int=2)]


@pytest.mark.parametrize("task_type", ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video"])
def test_class_baselines_are_explicit_estimates(task_type):
    expected = (300_000, "video_baseline") if task_type.endswith("video") else (60_000, "image_baseline")
    assert estimated_service(task_type, None) == expected


@pytest.mark.parametrize(("observed", "expected"), [(1, 1000), (1000, 1000), (12345, 12345), (7_200_001, 7_200_000)])
def test_approved_profile_estimate_is_bounded(observed, expected):
    assert estimated_service("text_to_image", {"warm_p95_milliseconds": observed}) == (expected, "approved_profile")


@pytest.mark.parametrize("invalid", [None, 0, -1, True, "1000", 1.5, float("inf"), float("nan")])
def test_invalid_profile_observation_falls_back_without_inventing_measurements(invalid):
    assert estimated_service("text_to_video", {"warm_p95_milliseconds": invalid}) == (300_000, "video_baseline")


@pytest.mark.parametrize("priority", [-101, 101])
def test_invalid_priority_is_rejected(priority):
    with pytest.raises(ValueError):
        effective_priority(priority, NOW, NOW)


def test_unknown_capability_and_negative_clock_are_rejected():
    with pytest.raises(ValueError):
        estimated_service("unknown", None)
    with pytest.raises(ValueError):
        order_candidates([], {}, -1, NOW)


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_frozen_policy_fixtures_match_dto(case):
    schema = json.loads((CONTRACTS / case["schema"]).read_text(encoding="utf-8"))
    assert Draft202012Validator(schema).is_valid(case["data"]) == case["valid"]
    if case["valid"]:
        assert DispatchPolicy.model_validate(case["data"]).model_dump() == DispatchPolicy.current().model_dump()
    else:
        with pytest.raises(ValidationError):
            DispatchPolicy.model_validate(case["data"])


def test_policy_requires_every_field_and_queue_extension_remains_optional():
    with pytest.raises(ValidationError):
        DispatchPolicy.model_validate({})
    fields = {"snapshot_id": "synthetic", "snapshot_at": 1, "server_time": 1, "observed_at": 1, "stale": False, "filters": {}, "global_unassigned_queued": 0, "unfinished": 0, "occupied_physical_gpus": 0, "states": {}, "channels": [], "channels_page": 1, "channels_has_more": False, "candidate_counts_overlap": True}
    assert QueueView.model_validate(fields).dispatch_policy is None
    assert QueueView.model_validate({**fields, "dispatch_policy": DispatchPolicy.current().model_dump()}).dispatch_policy == DispatchPolicy.current()


@pytest.mark.parametrize("field", ["priority", "tenant_shares", "estimated_service_milliseconds"])
def test_business_request_cannot_set_scheduling_privileges(field):
    with pytest.raises(ValidationError):
        JobCreate.model_validate({"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic policy boundary"}, field: 100})
