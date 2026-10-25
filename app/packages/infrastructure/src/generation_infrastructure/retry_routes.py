from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from generation_contracts.capacity import output_reservation
from generation_domain.retries import OOM_CODE
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from generation_infrastructure.models import (
    Attempt,
    Channel,
    Gpu,
    Job,
    Node,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.spec_isolation import blocks_claim
from generation_infrastructure.worker_capacity import capacity_ready


def oom_attempts(job_ids: list[UUID]) -> Select[tuple[UUID, UUID]]:
    return select(Attempt.job_id, Attempt.gpu_id).where(
        Attempt.job_id.in_(job_ids), Attempt.closed_at.is_not(None), Attempt.generation_released_at.is_not(None),
        Attempt.generation_evidence["release"]["error_code"].astext == OOM_CODE,
        Attempt.generation_evidence["release"]["execution_stop_confirmed"].as_boolean().is_(True),
    )


def failed_oom_gpus(session: Session, job_ids: list[UUID]) -> dict[UUID, set[UUID]]:
    result: dict[UUID, set[UUID]] = {}
    for job_id, gpu_id in session.execute(oom_attempts(job_ids).distinct()):
        result.setdefault(job_id, set()).add(gpu_id)
    return result


def retry_route_approved(binding: WorkflowBinding, release: WorkflowRelease, gpu: Gpu, *, allow_synthetic_catalog: bool) -> bool:
    if (binding.desired_digest, binding.applied_digest, binding.verified_digest) != (release.digest,) * 3:
        return False
    if allow_synthetic_catalog and release.validation.get("test_only") is True:
        return True
    approval = binding.runtime_approval
    return bool(approval and any(isinstance(profile, dict) and profile.get("id") == approval.get("profile_id")
                                and profile.get("gpu_hardware_id") == gpu.hardware_id
                                for profile in release.manifest.get("runtime_profiles", [])))


def has_oom_alternative(session: Session, job: Job, source_gpu: UUID, *, allow_synthetic_catalog: bool) -> bool:
    """Observe alternatives without taking cross-node/GPU locks; claim rechecks."""
    release = session.get(WorkflowRelease, job.release_id)
    if release is None:
        return False
    now = utcnow()
    excluded = failed_oom_gpus(session, [job.id]).get(job.id, set()) | {source_gpu}
    rows = session.execute(select(Channel, Gpu, Node, WorkflowBinding)
        .join(Gpu, Gpu.id == Channel.gpu_id).join(Node, Node.id == Channel.node_id)
        .join(WorkflowBinding, (WorkflowBinding.channel_id == Channel.id) & (WorkflowBinding.release_id == job.release_id))
        .where(Channel.id.in_([parse_id("ch", identifier) for identifier in job.eligible_channels]),
               Channel.tenant_id == job.tenant_id, Channel.project_id == job.project_id,
               Channel.enabled.is_(True), Channel.draining.is_(False), Channel.deleted_at.is_(None),
               Channel.desired_revision == Channel.applied_revision, Channel.desired_revision == Channel.verified_revision,
               WorkflowBinding.desired_digest == release.digest, WorkflowBinding.applied_digest == release.digest,
               WorkflowBinding.verified_digest == release.digest, Gpu.enabled.is_(True), Gpu.management_hold.is_(None),
               Gpu.id.not_in(excluded), Node.enabled.is_(True), Node.current_boot_id.is_not(None),
               Node.observed_at > now - timedelta(seconds=15)).order_by(Channel.id).limit(101))
    for channel, gpu, node, binding in rows:
        if public_id("ch", channel.id) not in job.eligible_channels:
            continue
        if not retry_route_approved(binding, release, gpu, allow_synthetic_catalog=allow_synthetic_catalog) or blocks_claim(session, job, gpu):
            continue
        if job.task_type == "text_to_audio" and "audio-output-v1" not in node.inventory.get("features", []):
            continue
        if capacity_ready(node.inventory, node.current_boot_id, now, next_reserve=output_reservation(release.manifest, 2 * 1024**3)):
            return True
    return False
