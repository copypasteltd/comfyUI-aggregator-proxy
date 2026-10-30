from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from generation_contracts.capacity import output_reservation
from generation_contracts.device_pool import DeviceRouting
from generation_domain.device_pool import DeviceShare, choose_device
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from generation_infrastructure.models import (
    Attempt,
    Channel,
    DevicePool,
    DevicePoolMember,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    PublicModel,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.retry_routes import failed_oom_gpus, retry_route_approved
from generation_infrastructure.spec_isolation import blocks_claim
from generation_infrastructure.worker_capacity import capacity_ready


def pool_configuration(session: Session, model: PublicModel, routing: DeviceRouting) -> dict[str, Any]:
    channels = list(session.scalars(select(Channel).where(Channel.id.in_([parse_id("ch", value) for value in model.channel_ids]), Channel.tenant_id == model.tenant_id, Channel.project_id == model.project_id, Channel.deleted_at.is_(None)).order_by(Channel.id)))
    if len(channels) != len(set(model.channel_ids)):
        raise DomainError("channel_not_available", "设备池映射渠道不可用", 422)
    gpu_ids = {public_id("gpu", row.gpu_id) for row in channels}
    if set(routing.weights) - gpu_ids:
        raise DomainError("device_weight_not_mapped", "权重只能指定当前模型映射的物理GPU", 422)
    return {"policy": "balanced_ready", "model_id": public_id("mdl", model.id), "release_id": public_id("wfr", model.release_id),
            "tenant_id": public_id("ten", model.tenant_id), "project_id": public_id("prj", model.project_id),
            "channels": [{"id": public_id("ch", row.id), "gpu_id": public_id("gpu", row.gpu_id)} for row in channels],
            "members": [{"gpu_id": value, "weight": routing.weights.get(value, 1)} for value in sorted(gpu_ids)]}


def validate_routing(session: Session, model: PublicModel, routing: DeviceRouting) -> dict[str, Any]:
    if routing.policy == "balanced_ready":
        pool_configuration(session, model, routing)
    return routing.model_dump()


def freeze_pool(session: Session, model: PublicModel) -> str | None:
    routing = DeviceRouting.model_validate(model.routing)
    if routing.policy != "balanced_ready":
        return None
    configuration = pool_configuration(session, model, routing)
    identifier = fingerprint(configuration)
    session.execute(insert(DevicePool).values(id=identifier, tenant_id=model.tenant_id, project_id=model.project_id, model_id=model.id, release_id=model.release_id, configuration=configuration).on_conflict_do_nothing())
    pool = session.get(DevicePool, identifier)
    if not pool or pool.configuration != configuration:
        raise DomainError("device_pool_invalid", "设备池版本冲突", 503)
    for member in configuration["members"]:
        session.execute(insert(DevicePoolMember).values(pool_id=identifier, gpu_id=parse_id("gpu", member["gpu_id"]), weight=member["weight"]).on_conflict_do_nothing())
    return identifier


@dataclass
class PoolChoice:
    pool: DevicePool
    members: list[DevicePoolMember]
    channel_id: UUID
    gpu_id: UUID
    node_id: UUID
    credits: dict[UUID, int]


def target_for_job(session: Session, job: Job, *, allow_synthetic_catalog: bool) -> PoolChoice | None:
    if job.device_pool_id is None:
        return None
    pool = session.get(DevicePool, job.device_pool_id)
    if not pool or (pool.tenant_id, pool.project_id, pool.release_id) != (job.tenant_id, job.project_id, job.release_id) or fingerprint(pool.configuration) != pool.id:
        raise DomainError("device_pool_invalid", "原任务设备池版本不可用", 503)
    configuration = pool.configuration
    expected = {parse_id("gpu", item["gpu_id"]): item["weight"] for item in configuration["members"]}
    members = list(session.scalars(select(DevicePoolMember).where(DevicePoolMember.pool_id == pool.id).order_by(DevicePoolMember.gpu_id)))
    if {member.gpu_id: member.weight for member in members} != expected:
        raise DomainError("device_pool_invalid", "设备池成员或权重不一致", 503)
    release = session.get(WorkflowRelease, job.release_id)
    if release is None:
        return None
    now = utcnow()
    rows = list(session.execute(select(Channel, Gpu, Node, WorkflowBinding)
        .join(Gpu, Gpu.id == Channel.gpu_id).join(Node, Node.id == Channel.node_id)
        .join(WorkflowBinding, (WorkflowBinding.channel_id == Channel.id) & (WorkflowBinding.release_id == job.release_id))
        .where(Channel.id.in_([parse_id("ch", value) for value in job.eligible_channels]), Channel.tenant_id == job.tenant_id, Channel.project_id == job.project_id,
               Channel.enabled.is_(True), Channel.draining.is_(False), Channel.deleted_at.is_(None),
               Channel.desired_revision == Channel.applied_revision, Channel.desired_revision == Channel.verified_revision,
               Gpu.enabled.is_(True), Gpu.management_hold.is_(None), Node.enabled.is_(True), Node.current_boot_id.is_not(None),
               Node.observed_at > now - timedelta(seconds=15)).order_by(Channel.id).limit(101)))
    if len(rows) > 100:
        raise DomainError("device_pool_invalid", "设备池超过冻结渠道范围", 503)
    occupied = set(session.scalars(select(GpuOccupancy.gpu_id).where(GpuOccupancy.gpu_id.in_(expected))))
    stale_nodes = set(session.scalars(select(Attempt.node_id).join(GpuOccupancy, GpuOccupancy.attempt_id == Attempt.id).join(Node, Node.id == Attempt.node_id)
        .where(Attempt.node_id.in_({row[2].id for row in rows}), Attempt.boot_id != Node.current_boot_id).distinct()))
    excluded = failed_oom_gpus(session, [job.id]).get(job.id, set())
    frozen_channels = {item["id"]: item["gpu_id"] for item in configuration["channels"]}
    ready: dict[UUID, tuple[Channel, Node]] = {}
    for channel, gpu, node, binding in rows:
        if gpu.id not in expected or public_id("gpu", gpu.id) != frozen_channels.get(public_id("ch", channel.id)) or gpu.id in occupied | excluded or node.id in stale_nodes:
            continue
        queue = channel.observation.get("queue", {})
        queue_at = channel.observation.get("queue_observed_at", 0)
        if not isinstance(queue, dict) or queue.get("reachable") is not True or queue.get("queue_running") != 0 or queue.get("queue_pending") != 0 or type(queue_at) not in (int, float) or not 0 <= now.timestamp() - queue_at < 15 or channel.observation.get("queue_revision") != channel.desired_revision:
            continue
        if not retry_route_approved(binding, release, gpu, allow_synthetic_catalog=allow_synthetic_catalog) or blocks_claim(session, job, gpu):
            continue
        feature = {"image_to_3d": "model3d-output-v1", "text_to_audio": "audio-output-v1"}.get(job.task_type)
        if feature and feature not in node.inventory.get("features", []):
            continue
        if not capacity_ready(node.inventory, node.current_boot_id, now, next_reserve=output_reservation(release.manifest, 2 * 1024**3)):
            continue
        ready.setdefault(gpu.id, (channel, node))
    decision = choose_device([DeviceShare(member.gpu_id, member.weight, member.credit, member.last_sequence) for member in members if member.gpu_id in ready])
    if decision is None:
        return None
    gpu_id, credits = decision
    channel, node = ready[gpu_id]
    return PoolChoice(pool, members, channel.id, gpu_id, node.id, credits)


def charge_pool(choice: PoolChoice, gpu_id: UUID) -> dict[str, Any]:
    selected = next(member for member in choice.members if member.gpu_id == gpu_id)
    if choice.gpu_id != gpu_id or max(choice.pool.sequence, selected.assigned_count) == 2**63 - 1:
        raise DomainError("device_pool_exhausted", "设备池分配计数不可提交", 503)
    choice.pool.sequence += 1
    for member in choice.members:
        member.credit = choice.credits.get(member.gpu_id, 0)
    selected.assigned_count += 1
    selected.last_sequence = choice.pool.sequence
    return {"policy": "balanced_ready", "pool_id": choice.pool.id, "gpu_id": public_id("gpu", gpu_id), "weight": selected.weight, "sequence": choice.pool.sequence, "assigned_count": selected.assigned_count}


def pool_view(session: Session, model: PublicModel) -> dict[str, Any]:
    routing = DeviceRouting.model_validate(model.routing)
    configuration = pool_configuration(session, model, routing) if routing.policy == "balanced_ready" else None
    pool_id = fingerprint(configuration) if configuration else None
    pool = session.get(DevicePool, pool_id) if pool_id else None
    history = {member.gpu_id: member for member in session.scalars(select(DevicePoolMember).where(DevicePoolMember.pool_id == pool_id))} if pool else {}
    return {"model_id": public_id("mdl", model.id), "model_revision": model.revision, "policy": routing.policy, "pool_id": pool_id,
            "sequence": pool.sequence if pool else 0,
            "members": [{"gpu_id": item["gpu_id"], "weight": item["weight"], "assigned_count": history[parse_id("gpu", item["gpu_id"])].assigned_count if parse_id("gpu", item["gpu_id"]) in history else 0,
                         "last_sequence": history[parse_id("gpu", item["gpu_id"])].last_sequence if parse_id("gpu", item["gpu_id"]) in history else 0} for item in configuration["members"]] if configuration else []}
