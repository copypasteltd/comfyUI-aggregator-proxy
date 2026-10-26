from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from heapq import heappop, heappush
from math import ceil
from time import monotonic
from uuid import UUID

from generation_domain.dispatch import DispatchCandidate, TenantService, order_candidates

MIN_SAMPLES = 10
HISTORY_SECONDS = 86400
MAX_GPUS = 64
MAX_JOBS = 500
MAX_ATTEMPTS_PER_GPU = 200
Scope = tuple[str, UUID]


@dataclass(frozen=True)
class RuntimeDistribution:
    count: int
    p50_ms: int
    p95_ms: int
    oldest_at_ms: int
    newest_at_ms: int

    @classmethod
    def observed(cls, samples: list[tuple[int, int]]) -> RuntimeDistribution:
        if not samples or any(duration <= 0 or at < 0 for duration, at in samples):
            raise ValueError("Runtime samples require positive observed spans")
        durations = sorted(duration for duration, _ in samples)
        return cls(len(samples), durations[ceil(len(samples) * .5) - 1], durations[ceil(len(samples) * .95) - 1], min(at for _, at in samples), max(at for _, at in samples))


@dataclass(frozen=True)
class WaitRoute:
    gpu_id: UUID
    channel_id: UUID
    basis: str | None
    cost_ms: int


@dataclass(frozen=True)
class WaitJob:
    id: UUID
    tenant_id: UUID
    scopes: tuple[Scope, ...]
    priority: int
    created_at: datetime
    available_at_ms: int
    deadline_ms: int
    routes: tuple[WaitRoute, ...]


@dataclass(frozen=True)
class WaitOccupancy:
    gpu_id: UUID
    scopes: tuple[Scope, ...]
    started_at_ms: int
    basis: str | None
    uncertain: bool = False


@dataclass(frozen=True)
class WaitResult:
    reason: str | None
    lower_ms: int | None
    upper_ms: int | None
    models: Mapping[str, RuntimeDistribution]


def estimate_wait(target_id: UUID, jobs: list[WaitJob], gpu_ids: set[UUID], occupancies: list[WaitOccupancy],
                  models: Mapping[str, RuntimeDistribution], limits: Mapping[Scope, int | None], counts: Mapping[Scope, int],
                  services: Mapping[UUID, TenantService], clock: int, sequence: int, now_ms: int,
                  blocked_gpus: Mapping[UUID, str] | None = None) -> WaitResult:
    """Conditional queue scenarios. Never authorize a claim or mutate the snapshot."""
    if len(jobs) > MAX_JOBS or len(gpu_ids) > MAX_GPUS or len(occupancies) > MAX_GPUS:
        return WaitResult("capacity_limit", None, None, {})
    target = next((job for job in jobs if job.id == target_id), None)
    if target is None or not target.routes:
        return WaitResult("no_ready_route", None, None, {})
    outcomes: list[int] = []
    used: dict[str, RuntimeDistribution] = {}
    deadline = monotonic() + .75
    for quantile in ("p50_ms", "p95_ms"):
        for reverse in (False, True):
            result, reason, sample_models = _scenario(target_id, jobs, gpu_ids, occupancies, models, limits, counts, services, clock, sequence, now_ms, quantile, reverse, blocked_gpus or {}, deadline)
            used.update(sample_models)
            if reason is not None:
                return WaitResult(reason, None, None, used)
            assert result is not None
            outcomes.append(result)
    return WaitResult(None, min(outcomes), max(outcomes), used)


def _scenario(target_id: UUID, jobs: list[WaitJob], gpu_ids: set[UUID], occupancies: list[WaitOccupancy],
              models: Mapping[str, RuntimeDistribution], limits: Mapping[Scope, int | None], initial_counts: Mapping[Scope, int],
              initial_services: Mapping[UUID, TenantService], clock: int, sequence: int, now_ms: int,
              quantile: str, reverse: bool, blocked_gpus: Mapping[UUID, str], budget_deadline: float) -> tuple[int | None, str | None, dict[str, RuntimeDistribution]]:
    pending = {job.id: job for job in jobs}
    counts, services, blocked = dict(initial_counts), dict(initial_services), dict(blocked_gpus)
    used: dict[str, RuntimeDistribution] = {}
    events: list[tuple[int, UUID, tuple[Scope, ...]]] = []
    busy: set[UUID] = set()
    target = pending[target_id]
    instant = now_ms
    routes_by_gpu: dict[UUID, dict[UUID, WaitRoute]] = {}
    for job in jobs:
        for route in sorted(job.routes, key=lambda item: item.channel_id.hex):
            routes_by_gpu.setdefault(route.gpu_id, {}).setdefault(job.id, route)

    def quota_available(job: WaitJob) -> bool:
        for scope in job.scopes:
            if scope not in limits:
                return False
            limit = limits[scope]
            if limit is not None and counts.get(scope, 0) >= limit:
                return False
        return True

    for occupancy in occupancies:
        busy.add(occupancy.gpu_id)
        model = models.get(occupancy.basis or "")
        if occupancy.uncertain or occupancy.started_at_ms > now_ms:
            blocked[occupancy.gpu_id] = "uncertain_execution"
        elif model is None or model.count < MIN_SAMPLES:
            blocked[occupancy.gpu_id] = "insufficient_samples"
        elif now_ms - occupancy.started_at_ms >= model.p95_ms:
            blocked[occupancy.gpu_id] = "uncertain_execution"
        elif occupancy.gpu_id not in blocked:
            used[occupancy.basis or ""] = model
            remaining = max(1, getattr(model, quantile) - (now_ms - occupancy.started_at_ms))
            heappush(events, (instant + remaining, occupancy.gpu_id, occupancy.scopes))

    # A job is dispatched once and releases once; retry readiness is also finite.
    for _ in range(3 * len(jobs) + 2 * len(gpu_ids) + 1):
        if monotonic() >= budget_deadline:
            return None, "capacity_limit", used
        while events and events[0][0] <= instant:
            _, gpu, scopes = heappop(events)
            busy.discard(gpu)
            for scope in scopes:
                if counts.get(scope, 0) <= 0:
                    return None, "execution_unavailable", used
                counts[scope] -= 1
        if instant >= target.deadline_ms or instant - now_ms > HISTORY_SECONDS * 1000:
            return None, "deadline", used
        for gpu in sorted(gpu_ids - busy - blocked.keys(), key=lambda value: value.hex, reverse=reverse):
            eligible = [job for job in pending.values() if job.available_at_ms <= instant < job.deadline_ms
                        and quota_available(job)
                        and job.id in routes_by_gpu.get(gpu, {})]
            if not eligible:
                continue
            order = order_candidates([DispatchCandidate(job.id, job.tenant_id, job.priority, job.created_at) for job in eligible], services, clock, datetime.fromtimestamp(instant / 1000, UTC))
            job = pending[order[0]]
            route = routes_by_gpu[gpu][job.id]
            model = models.get(route.basis or "")
            if model is None or model.count < MIN_SAMPLES:
                return None, "insufficient_samples", used
            used[route.basis or ""] = model
            if job.id == target_id:
                return instant - now_ms, None, used
            del pending[job.id]
            cost_start = max(clock, services.get(job.tenant_id, TenantService()).virtual_milliseconds)
            clock, sequence = cost_start, sequence + 1
            services[job.tenant_id] = TenantService(cost_start + route.cost_ms, sequence)
            for scope in job.scopes:
                counts[scope] = counts.get(scope, 0) + 1
            busy.add(gpu)
            heappush(events, (instant + getattr(model, quantile), gpu, job.scopes))
        future = [at for at, _, _ in events if at > instant]
        future.extend(job.available_at_ms for job in pending.values() if job.routes and job.available_at_ms > instant)
        if not future:
            reason = next((blocked[route.gpu_id] for route in target.routes if route.gpu_id in blocked), None)
            return None, reason or (next(iter(blocked.values())) if blocked else "quota_blocked"), used
        instant = min(future)
    return None, "capacity_limit", used
