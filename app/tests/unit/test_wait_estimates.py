from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from generation_contracts.wait_estimates import WaitEstimate
from generation_domain import wait_estimates as implementation
from generation_domain.dispatch import TenantService
from generation_domain.wait_estimates import (
    RuntimeDistribution,
    WaitJob,
    WaitOccupancy,
    WaitRoute,
    estimate_wait,
)
from generation_infrastructure.runtime_basis import wait_basis
from pydantic import ValidationError

NOW = datetime(2026, 10, 26, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
GPU, CHANNEL, TENANT = UUID(int=100), UUID(int=101), UUID(int=102)
SCOPE = ("1_tenant", TENANT)
MODEL = RuntimeDistribution(20, 60000, 100000, NOW_MS - 3600000, NOW_MS - 1000)
ROUTE = WaitRoute(GPU, CHANNEL, "measured", 60000)


def job(identifier: int, *, tenant: UUID = TENANT, age: int = 10, priority: int = 0) -> WaitJob:
    return WaitJob(UUID(int=identifier), tenant, (("1_tenant", tenant),), priority, NOW - timedelta(seconds=age), NOW_MS, NOW_MS + 3600000, (ROUTE,))


def forecast(target: WaitJob, ahead: list[WaitJob] | None = None, **overrides):
    jobs = [*(ahead or []), target]
    options = {"gpu_ids": {route.gpu_id for item in jobs for route in item.routes}, "occupancies": [], "models": {"measured": MODEL},
               "limits": {scope: None for item in jobs for scope in item.scopes}, "counts": {}, "services": {}, "clock": 0, "sequence": 0, "now_ms": NOW_MS}
    options.update(overrides)
    return estimate_wait(target.id, jobs, **options)


def test_busy_gpu_uses_observed_slot_span_and_subtracts_elapsed():
    target = job(1)
    result = forecast(target, occupancies=[WaitOccupancy(GPU, (SCOPE,), NOW_MS - 20000, "measured")], counts={SCOPE: 1}, limits={SCOPE: 1})
    assert (result.reason, result.lower_ms, result.upper_ms) == (None, 40000, 80000)


def test_shared_gpu_channels_do_not_create_extra_capacity():
    target = replace(job(2), routes=(ROUTE, replace(ROUTE, channel_id=UUID(int=999))))
    result = forecast(target, [job(1, age=20)])
    assert (result.lower_ms, result.upper_ms) == (60000, 100000)


def test_parallel_gpus_and_arbitration_orders_form_scenario_interval():
    other_route = replace(ROUTE, gpu_id=UUID(int=200), channel_id=UUID(int=201), basis="slow")
    target = replace(job(3), routes=(ROUTE, other_route))
    earlier = [replace(job(i, age=20 + i), routes=target.routes) for i in (1, 2)]
    result = forecast(target, earlier, models={"measured": MODEL, "slow": replace(MODEL, p50_ms=120000, p95_ms=240000)})
    assert (result.lower_ms, result.upper_ms) == (60000, 100000)


def test_fair_service_history_changes_dispatch_and_inputs_remain_unchanged():
    target = job(1)
    other = job(2, tenant=UUID(int=202), age=20)
    services = {TENANT: TenantService(500000, 10), other.tenant_id: TenantService(0, 9)}
    counts = {SCOPE: 0}
    result = forecast(target, [other], services=services, counts=counts)
    assert (result.lower_ms, result.upper_ms) == (60000, 100000)
    assert services[TENANT] == TenantService(500000, 10) and counts == {SCOPE: 0}


def test_overdue_tenant_and_priority_aging_use_the_actual_dispatch_policy():
    target = job(1, age=400, priority=-100)
    other = job(2, tenant=UUID(int=202), age=10, priority=100)
    result = forecast(target, [other], services={TENANT: TenantService(900000, 4), other.tenant_id: TenantService(0, 5)})
    assert result.lower_ms == result.upper_ms == 0
    result = forecast(job(3, age=1000, priority=-100), [job(4, age=0, priority=99)])
    assert result.lower_ms == result.upper_ms == 0


def test_retry_readiness_and_deadline_are_not_ignored():
    target = replace(job(1), available_at_ms=NOW_MS + 15000)
    assert forecast(target).lower_ms == forecast(target).upper_ms == 15000
    result = forecast(replace(target, deadline_ms=NOW_MS + 10000))
    assert result.reason == "deadline" and result.upper_ms is None


@pytest.mark.parametrize("change,reason", [({"uncertain": True}, "uncertain_execution"), ({"basis": None}, "insufficient_samples"), ({"started_at_ms": NOW_MS + 1}, "uncertain_execution"), ({"started_at_ms": NOW_MS - 100000}, "uncertain_execution")])
def test_unknown_or_overrun_occupancy_is_never_assumed_free(change, reason):
    occupancy = replace(WaitOccupancy(GPU, (SCOPE,), NOW_MS - 1000, "measured"), **change)
    result = forecast(job(1), occupancies=[occupancy], counts={SCOPE: 1})
    assert result.reason == reason and result.lower_ms is result.upper_ms is None


def test_insufficient_samples_ahead_and_stale_observations_refuse_numeric_eta():
    assert forecast(job(1), models={"measured": replace(MODEL, count=9)}).reason == "insufficient_samples"
    assert forecast(job(1), blocked_gpus={GPU: "stale_observation"}).reason == "stale_observation"
    preceding = replace(job(2, age=20), routes=(replace(ROUTE, basis=None),))
    assert forecast(job(1), [preceding]).reason == "insufficient_samples"


def test_zero_concurrency_and_unknown_release_do_not_borrow_capacity():
    assert forecast(job(1), limits={SCOPE: 0}).reason == "quota_blocked"
    result = forecast(job(1), occupancies=[WaitOccupancy(GPU, (SCOPE,), NOW_MS, None)], counts={SCOPE: 1}, limits={SCOPE: 1})
    assert result.reason == "insufficient_samples"


def test_expired_competitor_does_not_delay_a_ready_job():
    earlier = replace(job(2, age=100), deadline_ms=NOW_MS - 1)
    assert forecast(job(1), [earlier]).upper_ms == 0


def test_missing_routes_and_snapshot_bounds_return_unknown():
    assert forecast(replace(job(1), routes=())).reason == "no_ready_route"
    assert forecast(job(1), gpu_ids={UUID(int=i + 1) for i in range(65)}).reason == "capacity_limit"
    assert forecast(job(1000), [job(i + 1) for i in range(500)]).reason == "capacity_limit"


def test_compute_budget_returns_unknown_instead_of_stalling(monkeypatch):
    calls = iter([0.0, 1.0])
    monkeypatch.setattr(implementation, "monotonic", lambda: next(calls))
    result = forecast(job(1))
    assert result.reason == "capacity_limit" and result.upper_ms is None


def test_runtime_distribution_uses_discrete_observed_quantiles():
    distribution = RuntimeDistribution.observed([(i * 1000, NOW_MS - i) for i in range(1, 21)])
    assert (distribution.count, distribution.p50_ms, distribution.p95_ms) == (20, 10000, 19000)
    with pytest.raises(ValueError):
        RuntimeDistribution.observed([(0, NOW_MS)])


@pytest.mark.parametrize("field,changed", [("release_digest", "b" * 64), ("specification", "b" * 64), ("gpu_id", UUID(int=2)), ("channel_id", UUID(int=2)), ("revision", 2), ("approval", {"profile_id": "p", "evidence_id": "new"}), ("profile", {"id": "p", "gpu_hardware_id": "g", "warm_p95_milliseconds": 9000})])
def test_runtime_basis_invalidates_each_material_change(field, changed):
    args = {"release_digest": "a" * 64, "specification": "a" * 64, "gpu_id": GPU, "hardware_id": "g", "channel_id": CHANNEL, "revision": 1, "approval": {"profile_id": "p", "evidence_id": "old"}, "profile": {"id": "p", "gpu_hardware_id": "g"}, "validation": False, "test_only": False}
    assert wait_basis(**args) != wait_basis(**{**args, field: changed})


@pytest.mark.parametrize("change", [{"validation": True}, {"test_only": True}, {"approval": None}, {"profile": None}, {"hardware_id": "other"}])
def test_validation_synthetic_or_unapproved_runs_have_no_runtime_basis(change):
    args = {"release_digest": "a" * 64, "specification": "a" * 64, "gpu_id": GPU, "hardware_id": "g", "channel_id": CHANNEL, "revision": 1, "approval": {"profile_id": "p"}, "profile": {"id": "p", "gpu_hardware_id": "g"}, "validation": False, "test_only": False}
    assert wait_basis(**{**args, **change}) is None


@pytest.mark.parametrize("change", [{"lower_wait_ms": 5, "upper_wait_ms": 4}, {"minimum_route_samples": 9}, {"newest_sample_at_ms": NOW_MS + 1}, {"valid_until_ms": NOW_MS}, {"status": "unknown", "reason": "insufficient_samples"}, {"reason": "not_queued"}, {"sample_count": True}])
def test_response_rejects_invented_inconsistent_or_stale_intervals(change):
    value = {"job_id": "job_" + "1" * 32, "snapshot_at_ms": NOW_MS, "valid_until_ms": NOW_MS + 15000, "status": "estimated", "reason": None, "lower_wait_ms": 0, "upper_wait_ms": 1000, "sample_count": 20, "minimum_route_samples": 20, "oldest_sample_at_ms": NOW_MS - 100000, "newest_sample_at_ms": NOW_MS - 1000}
    with pytest.raises(ValidationError):
        WaitEstimate.model_validate({**value, **change})
