from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from generation_contracts.capacity import output_reservation
from generation_domain.dispatch import (
    POLICY_ID,
    DispatchCandidate,
    TenantService,
    effective_priority,
    estimated_service,
    order_candidates,
)
from generation_domain.errors import DomainError
from generation_domain.policies import enforce_limit
from generation_domain.spec_isolation import GLOBAL_UNFINISHED_JOBS, spec_digest
from generation_domain.values import public_id
from sqlalchemy import String, and_, cast, exists, func, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from generation_infrastructure.models import (
    ApiKey,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    Project,
    QuotaBucket,
    SpecIsolation,
    Tenant,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.retry_routes import failed_oom_gpus
from generation_infrastructure.transactions import GLOBAL_ID
from generation_infrastructure.worker_capacity import capacity_ready

DISPATCH_LOCK = 634720183
MAX_ROUTE_MATCHES = GLOBAL_UNFINISHED_JOBS * 100


def quota_candidates(session: Session, jobs: Sequence[Any]) -> tuple[list[Any], dict[tuple[str, UUID], QuotaBucket]]:
    if not jobs:
        return [], {}
    tenant_ids, project_ids, key_ids = ({getattr(job, name) for job in jobs} for name in ("tenant_id", "project_id", "key_id"))
    tenant_limits: dict[UUID, dict[str, Any]] = {identifier: limits for identifier, limits in session.execute(select(Tenant.id, Tenant.limits).where(Tenant.id.in_(tenant_ids)))}
    project_limits: dict[UUID, dict[str, Any]] = {identifier: limits for identifier, limits in session.execute(select(Project.id, Project.limits).where(Project.id.in_(project_ids)))}
    key_limits: dict[UUID, dict[str, Any]] = {identifier: limits for identifier, limits in session.execute(select(ApiKey.id, ApiKey.policy["limits"]).where(ApiKey.id.in_(key_ids)))}
    counters = {(row.scope_kind, row.scope_id): row for row in session.scalars(select(QuotaBucket).where(QuotaBucket.period == "active", QuotaBucket.scope_id.in_({GLOBAL_ID, *tenant_ids, *project_ids, *key_ids})))}
    available = []
    for job in jobs:
        try:
            for kind, identifier, limits in (("1_tenant", job.tenant_id, tenant_limits.get(job.tenant_id)), ("2_project", job.project_id, project_limits.get(job.project_id)), ("3_key", job.key_id, key_limits.get(job.key_id))):
                if limits is None:
                    raise DomainError("dispatch_scope_unavailable", "任务作用域不可用", 409)
                bucket = counters.get((kind, identifier))
                enforce_limit(limits, "generating_jobs", bucket.generating_jobs if bucket else 0, 1)
        except DomainError:
            continue
        available.append(job)
    return available, counters


def eligible_candidates(session: Session, node: Node, now: datetime, *, allow_synthetic_catalog: bool) -> list[DispatchCandidate]:
    """Bounded initial eligibility snapshot; the locked claim repeats hard gates."""
    route = Job.eligible_channels.bool_op("@>")(func.jsonb_build_array(literal("ch_") + func.replace(cast(Channel.id, String), "-", "")))
    ready = (
        Channel.node_id == node.id, Channel.enabled.is_(True), Channel.draining.is_(False), Channel.deleted_at.is_(None),
        Channel.desired_revision == Channel.applied_revision, Channel.desired_revision == Channel.verified_revision,
        Gpu.enabled.is_(True), Gpu.management_hold.is_(None),
        ~exists(select(GpuOccupancy.gpu_id).where(GpuOccupancy.gpu_id == Gpu.id)),
        WorkflowBinding.desired_digest == WorkflowBinding.verified_digest, WorkflowBinding.applied_digest == WorkflowBinding.verified_digest,
    )
    approved = WorkflowBinding.runtime_approval["profile_id"].astext.is_not(None)
    synthetic = and_(literal(allow_synthetic_catalog), WorkflowRelease.validation["test_only"].as_boolean().is_(True))
    jobs = list(session.scalars(select(Job)
        .join(Channel, and_(Channel.tenant_id == Job.tenant_id, Channel.project_id == Job.project_id, route))
        .join(Gpu, Gpu.id == Channel.gpu_id)
        .join(WorkflowBinding, and_(WorkflowBinding.channel_id == Channel.id, WorkflowBinding.release_id == Job.release_id))
        .join(WorkflowRelease, WorkflowRelease.id == Job.release_id)
        .where(Job.state == "queued", Job.current_attempt_id.is_(None), Job.cancellation_requested.is_(False), Job.deadline > now, *ready, or_(Job.is_validation.is_(True), approved, synthetic))
        .distinct().order_by(Job.created_at, Job.id).limit(GLOBAL_UNFINISHED_JOBS)))
    if not jobs:
        return []
    jobs, _ = quota_candidates(session, jobs)
    if not jobs:
        return []
    release_ids = {job.release_id for job in jobs}
    releases = {identifier: (output, test_only) for identifier, output, test_only in session.execute(select(WorkflowRelease.id, WorkflowRelease.manifest["output"], WorkflowRelease.validation["test_only"].as_boolean()).where(WorkflowRelease.id.in_(release_ids)))}
    routes: dict[UUID, list[tuple[str, UUID, UUID, UUID, bool]]] = defaultdict(list)
    matched = 0
    for release_id, channel_id, gpu_id, tenant_id, project_id, runtime_approved in session.execute(select(WorkflowBinding.release_id, Channel.id, Gpu.id, Channel.tenant_id, Channel.project_id, approved).join(Channel, Channel.id == WorkflowBinding.channel_id).join(Gpu, Gpu.id == Channel.gpu_id).where(WorkflowBinding.release_id.in_(release_ids), *ready).limit(MAX_ROUTE_MATCHES + 1)):
        matched += 1
        if matched > MAX_ROUTE_MATCHES:
            raise DomainError("dispatch_catalog_capacity", "调度绑定目录超过单轮上限", 503)
        routes[release_id].append((public_id("ch", channel_id), gpu_id, tenant_id, project_id, bool(runtime_approved)))
    specifications = {(job.release_id, spec_digest(job.task_type, job.execution)) for job in jobs if not job.is_validation}
    gpu_ids = {route[1] for values in routes.values() for route in values}
    isolated = set(session.execute(select(SpecIsolation.gpu_id, SpecIsolation.release_id, SpecIsolation.spec_digest).where(SpecIsolation.is_active.is_(True), SpecIsolation.gpu_id.in_(gpu_ids), tuple_(SpecIsolation.release_id, SpecIsolation.spec_digest).in_(specifications)).limit(MAX_ROUTE_MATCHES + 1)).all()) if specifications else set()
    if len(isolated) > MAX_ROUTE_MATCHES:
        raise DomainError("dispatch_catalog_capacity", "调度规格目录超过单轮上限", 503)
    candidates = []
    excluded_gpus = failed_oom_gpus(session, [job.id for job in jobs])
    for job in jobs:
        output, test_only = releases[job.release_id]
        if not node.current_boot_id or not capacity_ready(node.inventory, node.current_boot_id, now, next_reserve=output_reservation({"output": output}, 2 * 1024**3)):
            continue
        digest = spec_digest(job.task_type, job.execution)
        if not any(channel_id in job.eligible_channels and (tenant_id, project_id) == (job.tenant_id, job.project_id)
                   and gpu_id not in excluded_gpus.get(job.id, set())
                   and (job.is_validation or runtime_approved or (allow_synthetic_catalog and test_only))
                   and (job.is_validation or (gpu_id, job.release_id, digest) not in isolated)
                   for channel_id, gpu_id, tenant_id, project_id, runtime_approved in routes[job.release_id]):
            continue
        candidates.append(DispatchCandidate(job.id, job.tenant_id, job.priority, job.created_at))
    return candidates


def ordered_candidates(session: Session, candidates: Sequence[DispatchCandidate], now: datetime) -> list[UUID]:
    # The eligibility snapshot is deliberately computed before arbitration.
    # Re-read queue, priority, live counters and fair history under the mutex.
    jobs = list(session.execute(select(Job.id, Job.tenant_id, Job.project_id, Job.key_id, Job.priority, Job.created_at)
        .where(Job.id.in_([item.job_id for item in candidates]), Job.state == "queued", Job.current_attempt_id.is_(None), Job.cancellation_requested.is_(False), Job.deadline > now)
        .limit(GLOBAL_UNFINISHED_JOBS)))
    jobs, counters = quota_candidates(session, jobs)
    services = {identifier: TenantService(bucket.dispatch_virtual_milliseconds, bucket.dispatch_sequence) for (kind, identifier), bucket in counters.items() if kind == "1_tenant"}
    global_bucket = counters.get(("0_global", GLOBAL_ID))
    rows = [DispatchCandidate(job.id, job.tenant_id, job.priority, job.created_at) for job in jobs]
    return order_candidates(rows, services, global_bucket.dispatch_virtual_milliseconds if global_bucket else 0, now)


def charge_dispatch(buckets: dict[tuple[str, str], QuotaBucket], job: Job, release: WorkflowRelease, binding: WorkflowBinding, gpu: Gpu, now: datetime) -> dict[str, Any]:
    approval = binding.runtime_approval
    profile = next((item for item in release.manifest.get("runtime_profiles", []) if approval and item["id"] == approval.get("profile_id") and item["gpu_hardware_id"] == gpu.hardware_id), None)
    cost, source = estimated_service(job.task_type, profile)
    global_bucket, tenant = buckets["0_global", "active"], buckets["1_tenant", "active"]
    start = max(global_bucket.dispatch_virtual_milliseconds, tenant.dispatch_virtual_milliseconds)
    if start > 2**63 - 1 - cost or global_bucket.dispatch_sequence == 2**63 - 1:
        raise DomainError("dispatch_accounting_exhausted", "调度计数已达上限", 503)
    global_bucket.dispatch_virtual_milliseconds = start
    global_bucket.dispatch_sequence += 1
    tenant.dispatch_virtual_milliseconds = start + cost
    tenant.dispatch_sequence = global_bucket.dispatch_sequence
    return {"policy_id": POLICY_ID, "estimated_service_milliseconds": cost, "cost_source": source, "effective_priority": effective_priority(job.priority, job.created_at, now), "selected_at": int(now.timestamp()), "sequence": global_bucket.dispatch_sequence}
