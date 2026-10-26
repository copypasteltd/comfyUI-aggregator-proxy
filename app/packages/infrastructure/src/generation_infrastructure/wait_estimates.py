from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from generation_contracts.capacity import output_reservation
from generation_contracts.wait_estimates import WaitEstimate
from generation_domain.dispatch import TenantService, estimated_service
from generation_domain.errors import DomainError
from generation_domain.spec_isolation import spec_digest
from generation_domain.state import TERMINAL
from generation_domain.values import public_id, utcnow
from generation_domain.wait_estimates import (
    HISTORY_SECONDS,
    MAX_ATTEMPTS_PER_GPU,
    MAX_GPUS,
    MAX_JOBS,
    RuntimeDistribution,
    WaitJob,
    WaitOccupancy,
    WaitRoute,
    estimate_wait,
)
from sqlalchemy import case, func, select, true, tuple_
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase
from generation_infrastructure.dispatch import MAX_ROUTE_MATCHES
from generation_infrastructure.models import (
    ApiKey,
    Attempt,
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
from generation_infrastructure.queue_queries import begin_queue_read
from generation_infrastructure.retry_routes import failed_oom_gpus
from generation_infrastructure.runtime_basis import wait_basis
from generation_infrastructure.transactions import GLOBAL_ID
from generation_infrastructure.worker_capacity import capacity_ready, capacity_snapshot


def milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def scopes(job: Any) -> tuple[tuple[str, UUID], ...]:
    return (("1_tenant", job.tenant_id), ("2_project", job.project_id), ("3_key", job.key_id))


def queue_jobs(session: Session) -> list[Any]:
    # Project the public generation shape in SQL; never load workflow_api or prompts.
    parameters = func.jsonb_strip_nulls(func.jsonb_build_object(*[part for key in ("size", "quality", "n", "seconds", "fps", "strength") for part in (key, Job.execution["parameters"][key])]))
    references = Job.execution["input"]["references"]
    count = case((func.jsonb_typeof(references) == "array", func.jsonb_array_length(references)), else_=0)
    fields = (Job.id, Job.tenant_id, Job.project_id, Job.key_id, Job.release_id, Job.task_type, Job.state, Job.priority, Job.created_at,
              Job.deadline, Job.current_attempt_id, Job.retry_after, Job.cancellation_requested, Job.is_validation, Job.eligible_channels,
              parameters.label("parameters"), Job.execution["input"].has_key("source_image").label("source"),
              Job.execution["input"].has_key("mask").label("mask"), count.label("references"))
    result = []
    for row in session.execute(select(*fields).where(Job.state.not_in(TERMINAL)).order_by(Job.created_at, Job.id).limit(MAX_JOBS + 1)):
        job = SimpleNamespace(**row._mapping)
        inputs = {**({"source_image": {}} if job.source else {}), **({"mask": {}} if job.mask else {}), "references": [None] * job.references}
        job.specification = spec_digest(job.task_type, {"parameters": job.parameters, "input": inputs})
        result.append(job)
    return result


def observed_models(session: Session, gpu_ids: set[UUID], wanted: set[str], now: datetime) -> dict[str, RuntimeDistribution]:
    if not gpu_ids or not wanted:
        return {}
    # LIMIT is inside the lateral lookup, before sample filtering. The existing
    # unique (gpu_id,fence) index bounds every GPU to its latest 200 attempts.
    recent = (select(Attempt.job_id, Attempt.channel_snapshot["wait_basis"].astext.label("basis"), Attempt.created_at.label("started"),
                     Attempt.generation_released_at.label("released"), Attempt.outputs_collected_at.label("collected"))
              .where(Attempt.gpu_id == Gpu.id).order_by(Attempt.fence.desc()).limit(MAX_ATTEMPTS_PER_GPU).correlate(Gpu).lateral())
    statement = (select(recent.c.basis, recent.c.started, recent.c.released, recent.c.collected)
                 .select_from(Gpu).join(recent, true()).join(Job, Job.id == recent.c.job_id)
                 .where(Gpu.id.in_(gpu_ids), recent.c.basis.in_(wanted), Job.state == "succeeded", Job.is_validation.is_(False)))
    samples: dict[str, list[tuple[int, int]]] = defaultdict(list)
    now_ms = milliseconds(now)
    for basis, started, released, collected in session.execute(statement):
        if released is None or collected is None:
            continue
        start_ms, release_ms = milliseconds(started), milliseconds(released)
        if now_ms - HISTORY_SECONDS * 1000 <= start_ms < release_ms <= now_ms and started <= collected <= released:
            samples[basis].append((release_ms - start_ms, release_ms))
    return {basis: RuntimeDistribution.observed(values) for basis, values in samples.items()}


def queue_observation(channel: Any, occupied: bool, now: datetime) -> str | None:
    at, queue = channel.queue_at, channel.queue
    if type(at) not in (int, float) or not now.timestamp() - 15 < at <= now.timestamp() or channel.queue_revision != channel.applied_revision or not isinstance(queue, dict) or queue.get("reachable") is not True:
        return "stale_observation"
    running, pending = queue.get("queue_running"), queue.get("queue_pending")
    if type(running) is not int or type(pending) is not int or running < 0 or pending < 0 or pending or running > int(occupied):
        return "uncertain_execution"
    return None


class WaitEstimates(AdminBase):
    def get(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            now = utcnow()
            result = WaitEstimate(job_id=public_id("job", identifier), snapshot_at_ms=milliseconds(now), valid_until_ms=milliseconds(now) + 15000, status="unknown", reason="no_ready_route")
            target = session.execute(select(Job.state, Job.current_attempt_id, Job.is_validation, Job.cancellation_requested).where(Job.id == identifier)).one_or_none()
            if target is None:
                raise DomainError("not_found", "任务不存在", 404)
            if target.state not in ("queued", "retry_wait") or target.current_attempt_id is not None or target.cancellation_requested:
                return result.model_copy(update={"status": "not_waiting", "reason": "not_queued"}).model_dump()
            if target.is_validation:
                return result.model_copy(update={"reason": "validation_task"}).model_dump()
            return self._snapshot(session, identifier, now, result)

    def _snapshot(self, session: Session, identifier: UUID, now: datetime, result: WaitEstimate) -> dict[str, Any]:
        jobs = queue_jobs(session)
        queued = [job for job in jobs if job.state in ("queued", "retry_wait") and not job.current_attempt_id and not job.cancellation_requested and job.deadline > now]
        if len(jobs) > MAX_JOBS:
            return result.model_copy(update={"reason": "capacity_limit"}).model_dump()
        result.queued_jobs = len(queued)
        if not any(job.id == identifier for job in queued):
            return result.model_copy(update={"reason": "deadline"}).model_dump()
        release_ids = {job.release_id for job in queued}
        releases = {row.id: row for row in session.execute(select(WorkflowRelease.id, WorkflowRelease.digest, WorkflowRelease.manifest["output"].label("output"), WorkflowRelease.manifest["runtime_profiles"].label("profiles"), WorkflowRelease.validation["test_only"].as_boolean().label("test_only")).where(WorkflowRelease.id.in_(release_ids)))}
        topology = list(session.execute(select(Channel.id.label("channel_id"), Channel.tenant_id, Channel.project_id, Channel.gpu_id, Channel.applied_revision,
            Channel.observation["queue_observed_at"].label("queue_at"), Channel.observation["queue_revision"].label("queue_revision"), Channel.observation["queue"].label("queue"),
            Gpu.hardware_id, Node.id.label("node_id"), Node.current_boot_id, Node.inventory["capacity"].label("capacity"), Node.inventory["features"].label("features"),
            WorkflowBinding.release_id, WorkflowBinding.runtime_approval)
            .select_from(WorkflowBinding).join(Channel, Channel.id == WorkflowBinding.channel_id).join(Gpu, Gpu.id == Channel.gpu_id).join(Node, Node.id == Channel.node_id)
            .where(WorkflowBinding.release_id.in_(release_ids), Channel.enabled.is_(True), Channel.draining.is_(False), Channel.deleted_at.is_(None),
                   Channel.desired_revision == Channel.applied_revision, Channel.desired_revision == Channel.verified_revision, Gpu.enabled.is_(True), Gpu.management_hold.is_(None), Node.enabled.is_(True), Node.current_boot_id.is_not(None),
                   WorkflowBinding.desired_digest == WorkflowBinding.verified_digest, WorkflowBinding.applied_digest == WorkflowBinding.verified_digest).limit(MAX_ROUTE_MATCHES + 1)))
        if len(topology) > MAX_ROUTE_MATCHES:
            return result.model_copy(update={"reason": "capacity_limit"}).model_dump()
        occupied = list(session.execute(select(GpuOccupancy.gpu_id, GpuOccupancy.uncertain, Attempt.channel_id.label("occupied_channel"), Attempt.created_at, Attempt.lease_expires_at, Attempt.boot_id, Attempt.fence, Gpu.fence.label("current_fence"), Job.deadline,
            Attempt.channel_snapshot["wait_basis"].astext.label("basis"), Attempt.generation_released_at, Job.id.label("job_id"), Job.tenant_id, Job.project_id, Job.key_id, Job.state,
            Job.current_attempt_id, Attempt.id.label("attempt_id"), Node.current_boot_id, Node.enabled)
            .join(Attempt, Attempt.id == GpuOccupancy.attempt_id).join(Job, Job.id == GpuOccupancy.job_id).join(Node, Node.id == Attempt.node_id).join(Gpu, Gpu.id == GpuOccupancy.gpu_id).limit(MAX_GPUS + 1)))
        gpu_ids = {row.gpu_id for row in topology} | {row.gpu_id for row in occupied}
        if len(gpu_ids) > MAX_GPUS or len(occupied) > MAX_GPUS:
            return result.model_copy(update={"reason": "capacity_limit"}).model_dump()
        result.physical_gpus = len(gpu_ids)
        occupancies = [WaitOccupancy(row.gpu_id, scopes(row), milliseconds(row.created_at), row.basis,
            row.uncertain or row.generation_released_at is not None or row.lease_expires_at <= now or row.deadline <= now or row.fence != row.current_fence or row.boot_id != row.current_boot_id or not row.enabled or row.current_attempt_id != row.attempt_id or row.state not in ("leased", "preparing", "submitting", "running", "collecting")) for row in occupied]
        occupied_channels = {row.occupied_channel for row in occupied}
        specifications = {(job.release_id, job.specification) for job in queued}
        isolated = set(session.execute(select(SpecIsolation.gpu_id, SpecIsolation.release_id, SpecIsolation.spec_digest).where(SpecIsolation.is_active.is_(True), SpecIsolation.gpu_id.in_(gpu_ids), tuple_(SpecIsolation.release_id, SpecIsolation.spec_digest).in_(specifications)).limit(MAX_ROUTE_MATCHES + 1)).all()) if gpu_ids else set()
        if len(isolated) > MAX_ROUTE_MATCHES:
            return result.model_copy(update={"reason": "capacity_limit"}).model_dump()
        excluded = failed_oom_gpus(session, [job.id for job in queued])
        bindings: dict[UUID, list[Any]] = defaultdict(list)
        blocked: dict[UUID, str] = {}
        observations = list(session.execute(select(Channel.id, Channel.gpu_id, Channel.applied_revision,
            Channel.observation["queue_observed_at"].label("queue_at"), Channel.observation["queue_revision"].label("queue_revision"), Channel.observation["queue"].label("queue"))
            .where(Channel.gpu_id.in_(gpu_ids), Channel.deleted_at.is_(None)).limit(MAX_ROUTE_MATCHES + 1))) if gpu_ids else []
        if len(observations) > MAX_ROUTE_MATCHES:
            return result.model_copy(update={"reason": "capacity_limit"}).model_dump()
        for observation in observations:
            reason = queue_observation(observation, observation.id in occupied_channels, now)
            if reason:
                blocked[observation.gpu_id] = reason
        for row in topology:
            bindings[row.release_id].append(row)
            inventory = {"capacity": row.capacity, "features": row.features or []}
            if capacity_snapshot(inventory, row.current_boot_id, now) is None:
                blocked[row.gpu_id] = "stale_observation"
        waiting = []
        for job in queued:
            release = releases[job.release_id]
            routes = []
            for row in bindings[job.release_id]:
                if public_id("ch", row.channel_id) not in job.eligible_channels or (row.tenant_id, row.project_id) != (job.tenant_id, job.project_id) or row.gpu_id in excluded.get(job.id, set()):
                    continue
                synthetic = self.settings.environment == "test" and bool(release.test_only)
                if not job.is_validation and ((row.gpu_id, job.release_id, job.specification) in isolated or (not row.runtime_approval and not synthetic)):
                    continue
                inventory = {"capacity": row.capacity, "features": row.features or []}
                if job.task_type == "text_to_audio" and "audio-output-v1" not in inventory["features"]:
                    continue
                if row.gpu_id not in blocked and not capacity_ready(inventory, row.current_boot_id, now, next_reserve=output_reservation({"output": release.output}, 2 * 1024**3)):
                    continue
                profile = next((profile for profile in (release.profiles or []) if row.runtime_approval and profile["id"] == row.runtime_approval.get("profile_id") and profile["gpu_hardware_id"] == row.hardware_id), None)
                basis = wait_basis(release_digest=release.digest, specification=job.specification, gpu_id=row.gpu_id, hardware_id=row.hardware_id,
                    channel_id=row.channel_id, revision=row.applied_revision, approval=row.runtime_approval, profile=profile, validation=job.is_validation, test_only=bool(release.test_only))
                routes.append(WaitRoute(row.gpu_id, row.channel_id, basis, estimated_service(job.task_type, profile)[0]))
            waiting.append(WaitJob(job.id, job.tenant_id, scopes(job), job.priority, job.created_at, milliseconds(job.retry_after) if job.retry_after else result.snapshot_at_ms, milliseconds(job.deadline), tuple(routes)))
        models = observed_models(session, gpu_ids, {route.basis for job in waiting for route in job.routes if route.basis} | {row.basis for row in occupied if row.basis}, now)
        identifiers = {identifier for job in jobs for _, identifier in scopes(job)}
        limits: dict[tuple[str, UUID], int | None] = {}
        for kind, statement in (("1_tenant", select(Tenant.id, Tenant.limits).where(Tenant.id.in_(identifiers))), ("2_project", select(Project.id, Project.limits).where(Project.id.in_(identifiers))), ("3_key", select(ApiKey.id, ApiKey.policy["limits"]).where(ApiKey.id.in_(identifiers)))):
            for scope_id, policy in session.execute(statement):
                limit = policy.get("generating_jobs", {}) if isinstance(policy, dict) else {}
                limits[kind, scope_id] = None if limit.get("mode") == "unlimited" else limit.get("value", 0)
        buckets = list(session.scalars(select(QuotaBucket).where(QuotaBucket.period == "active", QuotaBucket.scope_id.in_({GLOBAL_ID, *identifiers}))))
        counters = {(bucket.scope_kind, bucket.scope_id): bucket.generating_jobs for bucket in buckets}
        services = {bucket.scope_id: TenantService(bucket.dispatch_virtual_milliseconds, bucket.dispatch_sequence) for bucket in buckets if bucket.scope_kind == "1_tenant"}
        global_bucket = next((bucket for bucket in buckets if bucket.scope_kind == "0_global" and bucket.scope_id == GLOBAL_ID), None)
        estimated = estimate_wait(identifier, waiting, gpu_ids, occupancies, models, limits, counters, services,
            global_bucket.dispatch_virtual_milliseconds if global_bucket else 0, global_bucket.dispatch_sequence if global_bucket else 0, result.snapshot_at_ms, blocked)
        result.status = "unknown" if estimated.reason else "estimated"
        result.reason = estimated.reason  # type: ignore[assignment]
        result.lower_wait_ms, result.upper_wait_ms = estimated.lower_ms, estimated.upper_ms
        if estimated.models:
            result.sample_count = sum(model.count for model in estimated.models.values())
            result.minimum_route_samples = min(model.count for model in estimated.models.values())
            result.oldest_sample_at_ms = min(model.oldest_at_ms for model in estimated.models.values())
            result.newest_sample_at_ms = max(model.newest_at_ms for model in estimated.models.values())
        return WaitEstimate.model_validate(result.model_dump()).model_dump()
