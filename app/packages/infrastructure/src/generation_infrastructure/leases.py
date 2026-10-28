from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from generation_contracts.capacity import WorkerCapacity, output_reservation
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.policies import enforce_limit
from generation_domain.retries import OOM_CODE, RETRY_POLICY, confirmed_oom_evidence, retry_after
from generation_domain.spec_isolation import spec_digest
from generation_domain.state import TERMINAL, transition, uncertain
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.dispatch import (
    DISPATCH_LOCK,
    charge_dispatch,
    eligible_candidates,
    ordered_candidates,
)
from generation_infrastructure.jobs import terminal_accounting
from generation_infrastructure.models import (
    ApiKey,
    Attempt,
    Channel,
    ClaimRequest,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    NodeBoot,
    Project,
    Tenant,
    WorkerInbox,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.retry_routes import (
    failed_oom_gpus,
    has_oom_alternative,
    oom_attempts,
    retry_route_approved,
)
from generation_infrastructure.runtime_basis import wait_basis
from generation_infrastructure.security import SecretVault
from generation_infrastructure.spec_isolation import blocks_claim, confirmed_failure
from generation_infrastructure.transactions import lock_buckets, record_change
from generation_infrastructure.worker_capacity import capacity_ready


class LeaseRepository:
    def __init__(self, sessions: sessionmaker[Session], vault: SecretVault, *, allow_synthetic_catalog: bool = False) -> None:
        self.sessions = sessions
        self.vault = vault
        self.allow_synthetic_catalog = allow_synthetic_catalog

    def attempt_status(self, node_id: UUID, boot_id: UUID, attempt_id: UUID, fence: int) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            attempt = session.scalar(select(Attempt).where(Attempt.id == attempt_id, Attempt.node_id == node_id))
            if not attempt:
                raise DomainError("not_found", "执行尝试不存在", 404)
            node = session.get(Node, node_id)
            if not node or not node.enabled or node.current_boot_id != boot_id or attempt.boot_id != boot_id or attempt.fence != fence:
                raise DomainError("stale_lease", "启动标识或执行代数已失效", 409)
            job = session.get(Job, attempt.job_id)
            assert job
            now = utcnow()
            current = job.current_attempt_id == attempt.id
            open_current = current and not attempt.closed_at and job.state not in TERMINAL
            valid = open_current and attempt.lease_expires_at > now and job.state != "reconciling"
            occupancy = session.get(GpuOccupancy, attempt.gpu_id)
            occupied = bool(occupancy and occupancy.attempt_id == attempt.id)
            return {"protocol_version": "0.4.0", "server_time": int(now.timestamp()), "job_id": public_id("job", job.id), "attempt_id": public_id("att", attempt.id), "node_id": public_id("node", node_id), "boot_id": str(boot_id), "fencing_token": fence, "status": job.state, "updated_revision": job.revision, "last_journal_seq": attempt.journal_seq, "is_current": current, "lease_expires_at": int(attempt.lease_expires_at.timestamp()), "lease_valid": bool(valid), "cancel_requested": job.cancellation_requested, "closed": attempt.closed_at is not None, "generation_released": attempt.generation_released_at is not None, "gpu_occupied": occupied, "occupancy_uncertain": bool(occupied and occupancy and occupancy.uncertain), "reconciliation_required": bool(open_current and (attempt.lease_expires_at <= now or job.state == "reconciling"))}

    def register(self, node_id: UUID, boot_id: UUID, inventory: dict[str, Any], *, credential_identifier: str | None = None) -> dict[str, Any]:
        now = utcnow()
        with self.sessions.begin() as session:
            node = session.execute(select(Node).where(Node.id == node_id).with_for_update()).scalar_one()
            if not node.enabled:
                raise DomainError("worker_disabled", "Worker 节点已停用", 403)
            if credential_identifier is not None and credential_identifier != node.credential_identifier:
                raise DomainError("invalid_credentials", "Worker 凭证已失效", 401)
            seen = session.scalar(select(NodeBoot).where(NodeBoot.boot_id == boot_id))
            if seen and (seen.node_id != node_id or node.current_boot_id != boot_id):
                raise DomainError("stale_boot", "旧的 Worker 启动标识不能再次注册")
            if not seen:
                session.add(NodeBoot(node_id=node_id, boot_id=boot_id))
            if node.current_boot_id != boot_id:
                for channel in session.scalars(select(Channel).where(Channel.node_id == node_id)):
                    channel.verified_revision = None
                    channel.observation = {**channel.observation, "queue": {}, "queue_observed_at": 0, "workflow_export": None, "workflow_export_observed_at": 0, "workflow_export_revision": None, "workflow_export_boot_id": None}
                    for binding in session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id)):
                        binding.verified_digest = None
            previous_capacity = {key: node.inventory[key] for key in ("capacity", "capacity_sequence") if key in node.inventory} if node.current_boot_id == boot_id else {}
            node.current_boot_id = boot_id
            node.observed_at = now
            node.inventory = {**inventory, **previous_capacity}
            for info in inventory.get("gpus", []):
                gpu = session.scalar(select(Gpu).where(Gpu.node_id == node_id, Gpu.hardware_id == info["hardware_id"]))
                if gpu is None:
                    session.add(Gpu(node_id=node_id, hardware_id=info["hardware_id"], name=info["name"], memory_bytes=info.get("memory_bytes")))
            record_change(session, public_id("node", node_id), "worker.registered", node_id, str(uuid4()))
            pending = list(session.scalars(select(Attempt.id).where(Attempt.node_id == node_id, Attempt.closed_at.is_(None), Attempt.boot_id != boot_id)))
            return {"node_id": public_id("node", node_id), "boot_id": str(boot_id), "heartbeat_seconds": 5, "reconciliation_required": [public_id("att", identifier) for identifier in pending]}

    def heartbeat(self, node_id: UUID, boot_id: UUID, observations: list[dict[str, Any]], capacity: dict[str, Any] | None = None) -> dict[str, Any]:
        sample = WorkerCapacity.model_validate(capacity) if capacity is not None else None
        with self.sessions.begin() as session:
            node = session.execute(select(Node).where(Node.id == node_id).with_for_update()).scalar_one_or_none()
            if not node or not node.enabled or node.current_boot_id != boot_id:
                raise DomainError("stale_boot", "Worker 启动标识已失效")
            now = utcnow()
            node.observed_at = now
            inventory = {**node.inventory}
            if sample is None:
                inventory.pop("capacity", None)
            elif sample.sequence > inventory.get("capacity_sequence", 0):
                observed_ms = int(now.timestamp() * 1000) - sample.observed_ago_ms
                previous_ms = inventory.get("capacity", {}).get("observed_at_ms", 0)
                inventory["capacity_sequence"] = sample.sequence
                if observed_ms >= previous_ms:
                    inventory["capacity"] = {"boot_id": str(boot_id), "observed_at_ms": observed_ms, "sample": sample.model_dump()}
            node.inventory = inventory
            seen: set[UUID] = set()
            for item in observations:
                channel = session.get(Channel, parse_id("ch", item["channel_id"]))
                if not channel or channel.node_id != node_id:
                    raise DomainError("channel_denied", "渠道不属于该 Worker", 403)
                if channel.id in seen:
                    raise DomainError("duplicate_observation", "同次心跳不能重复观测同一渠道", 422)
                seen.add(channel.id)
                if item["revision"] != channel.applied_revision or item["revision"] != channel.desired_revision or item["observed_ago_ms"] >= 15000 or channel.deleted_at:
                    continue
                observed = now - timedelta(milliseconds=item["observed_ago_ms"])
                previous = channel.observation.get("queue_observed_at", 0)
                if observed.timestamp() < previous:
                    continue
                channel.observation = {**channel.observation, "queue": item["observation"], "queue_observed_at": observed.timestamp(), "queue_revision": item["revision"], "workflow_export": item.get("workflow_export"), "workflow_export_observed_at": observed.timestamp(), "workflow_export_revision": item["revision"], "workflow_export_boot_id": str(boot_id)}
                channel.observed_at = now
            configs = [{"id": public_id("ch", c.id), "revision": c.applied_revision, "base_url": c.base_url, "authorization": self.vault.decrypt(c.encrypted_auth) if c.encrypted_auth else None} for c in session.scalars(select(Channel).where(Channel.node_id == node_id, Channel.deleted_at.is_(None), Channel.applied_revision == Channel.desired_revision).order_by(Channel.id).limit(100))]
            return {"server_time": int(now.timestamp()), "channels": configs}

    def _claim_request(self, session: Session, node_id: UUID, boot_id: UUID, request_id: str) -> ClaimRequest:
        session.execute(insert(ClaimRequest).values(node_id=node_id, boot_id=boot_id, request_id=request_id, empty=False).on_conflict_do_nothing())
        return session.execute(select(ClaimRequest).where(ClaimRequest.node_id == node_id, ClaimRequest.boot_id == boot_id, ClaimRequest.request_id == request_id).with_for_update()).scalar_one()

    def _replay_claim(self, session: Session, request: ClaimRequest) -> dict[str, Any]:
        attempt = session.get(Attempt, request.attempt_id)
        assert attempt
        job = session.get(Job, attempt.job_id)
        channel = session.get(Channel, attempt.channel_id)
        assert job and channel
        if attempt.closed_at or job.state in TERMINAL:
            return {"closed": True, "attempt_id": public_id("att", attempt.id), "status": job.state}
        if attempt.lease_expires_at <= utcnow():
            return {"reconciliation_required": True, "attempt_id": public_id("att", attempt.id), "status": job.state}
        return self._claim_projection(session, job, attempt, channel)

    def claim(self, node_id: UUID, boot_id: UUID, request_id: str | None = None) -> dict[str, Any] | None:
        request_id = request_id or str(uuid4())
        with self.sessions.begin() as session:
            node = session.get(Node, node_id)
            if not node or not node.enabled or node.current_boot_id != boot_id or not node.observed_at or node.observed_at < utcnow() - timedelta(seconds=15):
                raise DomainError("stale_boot", "Worker 心跳或启动标识已失效")
            previous = session.scalar(select(ClaimRequest).where(ClaimRequest.node_id == node_id, ClaimRequest.boot_id == boot_id, ClaimRequest.request_id == request_id))
            replay = previous is not None and (previous.attempt_id is not None or previous.empty)
            candidates = eligible_candidates(session, node, utcnow(), allow_synthetic_catalog=self.allow_synthetic_catalog) if not replay and node.enabled and capacity_ready(node.inventory, boot_id, utcnow()) else []
        with self.sessions.begin() as session:
            # All API processes arbitrate from fresh durable service history.
            # No HTTP, media, or inference work occurs under this transaction.
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DISPATCH_LOCK})
            request = self._claim_request(session, node_id, boot_id, request_id)
            node = session.get(Node, node_id)
            if not node or not node.enabled or node.current_boot_id != boot_id or not node.observed_at or node.observed_at < utcnow() - timedelta(seconds=15):
                raise DomainError("stale_boot", "Worker 心跳或启动标识已失效")
            old_occupied = session.scalar(select(GpuOccupancy.gpu_id).join(Attempt, GpuOccupancy.attempt_id == Attempt.id).where(Attempt.node_id == node_id, Attempt.boot_id != boot_id).limit(1))
            if old_occupied:
                raise DomainError("reconciliation_required", "节点旧启动仍有 GPU 占用，先完成核对")
            if request.attempt_id:
                return self._replay_claim(session, request)
            if request.empty:
                return None
            if not node.enabled or not capacity_ready(node.inventory, boot_id, utcnow()):
                request.empty = True
                return None
            for identifier in ordered_candidates(session, candidates, utcnow()):
                result = self._try_claim(session, identifier, node_id, boot_id, request_id)
                if result:
                    return result
                if request.empty:
                    return None
            request.empty = True
            return None

    def _try_claim(self, session: Session, job_id: UUID, node_id: UUID, boot_id: UUID, request_id: str) -> dict[str, Any] | None:
        request = self._claim_request(session, node_id, boot_id, request_id)
        node_guard = session.execute(select(Node).where(Node.id == node_id).with_for_update(read=True).execution_options(populate_existing=True)).scalar_one()
        if not node_guard.enabled or node_guard.current_boot_id != boot_id:
            raise DomainError("stale_boot", "Worker 启动标识已失效")
        if request.attempt_id:
            return self._replay_claim(session, request)
        if request.empty:
            return None
        if not capacity_ready(node_guard.inventory, boot_id, utcnow()):
            request.empty = True
            return None
        preview = session.get(Job, job_id)
        if preview is None:
            return None
        if preview.task_type == "image_to_3d" and "model3d-output-v1" not in node_guard.inventory.get("features", []):
            return None
        if preview.task_type == "text_to_audio" and "audio-output-v1" not in node_guard.inventory.get("features", []):
            return None
        buckets = lock_buckets(session, preview.tenant_id, preview.project_id, preview.key_id, preview.accepted_period)
        session.expunge(preview)
        job = session.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
        if job.state != "queued" or job.current_attempt_id or job.deadline <= utcnow() or job.cancellation_requested:
            return None
        node = session.get(Node, node_id)
        if not node or node.current_boot_id != boot_id or not node.enabled:
            raise DomainError("stale_boot", "Worker 启动标识已失效")
        key = session.get(ApiKey, job.key_id, populate_existing=True)
        tenant = session.get(Tenant, job.tenant_id, populate_existing=True)
        project = session.get(Project, job.project_id, populate_existing=True)
        assert key and tenant and project
        for kind, limits in [("1_tenant", tenant.limits), ("2_project", project.limits), ("3_key", key.policy["limits"])]:
            try:
                enforce_limit(limits, "generating_jobs", buckets[kind, "active"].generating_jobs, 1)
            except DomainError:
                return None
        channels = list(session.scalars(select(Channel).where(Channel.node_id == node_id, Channel.tenant_id == job.tenant_id, Channel.project_id == job.project_id, Channel.enabled.is_(True), Channel.draining.is_(False), Channel.deleted_at.is_(None), Channel.desired_revision == Channel.applied_revision, Channel.desired_revision == Channel.verified_revision).order_by(Channel.gpu_id, Channel.id).execution_options(populate_existing=True)))
        excluded_gpus = failed_oom_gpus(session, [job.id]).get(job.id, set())
        for channel in channels:
            if public_id("ch", channel.id) not in job.eligible_channels or channel.gpu_id in excluded_gpus:
                continue
            binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id, WorkflowBinding.release_id == job.release_id, WorkflowBinding.desired_digest == WorkflowBinding.verified_digest, WorkflowBinding.applied_digest == WorkflowBinding.verified_digest).execution_options(populate_existing=True))
            if not binding:
                continue
            release = session.get(WorkflowRelease, job.release_id, populate_existing=True)
            if not release:
                continue
            if not job.is_validation and not binding.runtime_approval and not (self.allow_synthetic_catalog and release and release.validation.get("test_only")):
                continue
            gpu = session.execute(select(Gpu).where(Gpu.id == channel.gpu_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
            if not gpu.enabled or gpu.management_hold is not None or session.get(GpuOccupancy, gpu.id) is not None or blocks_claim(session, job, gpu):
                continue
            if excluded_gpus and not retry_route_approved(binding, release, gpu, allow_synthetic_catalog=self.allow_synthetic_catalog):
                continue
            node = session.execute(select(Node).where(Node.id == node_id).with_for_update(read=True).execution_options(populate_existing=True)).scalar_one()
            if not node.enabled or node.current_boot_id != boot_id:
                raise DomainError("stale_boot", "Worker 启动标识已失效")
            if not capacity_ready(node.inventory, boot_id, utcnow(), next_reserve=output_reservation(release.manifest if release else None, 2 * 1024**3)):
                return None
            gpu.fence += 1
            attempt = Attempt(job_id=job.id, channel_id=channel.id, channel_snapshot={"id": public_id("ch", channel.id), "revision": channel.applied_revision, "base_url": channel.base_url, "encrypted_auth": channel.encrypted_auth}, node_id=node_id, boot_id=boot_id, gpu_id=gpu.id, fence=gpu.fence, lease_expires_at=utcnow() + timedelta(seconds=60))
            session.add(attempt)
            session.flush()
            dispatch = charge_dispatch(buckets, job, release, binding, gpu, utcnow())
            profile = next((item for item in release.manifest.get("runtime_profiles", []) if binding.runtime_approval and item["id"] == binding.runtime_approval.get("profile_id") and item["gpu_hardware_id"] == gpu.hardware_id), None)
            basis = wait_basis(release_digest=release.digest, specification=spec_digest(job.task_type, job.execution), gpu_id=gpu.id,
                hardware_id=gpu.hardware_id, channel_id=channel.id, revision=channel.desired_revision, approval=binding.runtime_approval,
                profile=profile, validation=job.is_validation, test_only=bool(release.validation.get("test_only")))
            attempt.channel_snapshot = {**attempt.channel_snapshot, "dispatch": dispatch, "wait_basis": basis}
            request.attempt_id = attempt.id
            session.add(GpuOccupancy(gpu_id=gpu.id, attempt_id=attempt.id, job_id=job.id))
            job.current_attempt_id = attempt.id
            job.state = transition("queued", "leased", "claim", {"admission_confirmed", "generation_slot_held", "retry_budget_available"})
            job.revision += 1
            for (_kind, period), bucket in buckets.items():
                if period == "active":
                    bucket.generating_jobs += 1
            record_change(session, public_id("node", node_id), "job.claimed", job.id, str(uuid4()), {"dispatch": dispatch})
            return self._claim_projection(session, job, attempt, channel)
        return None

    def _claim_projection(self, session: Session, job: Job, attempt: Attempt, channel: Channel) -> dict[str, Any]:
        release = session.get(WorkflowRelease, job.release_id)
        assert release
        snapshot = attempt.channel_snapshot
        if snapshot.get("legacy_requires_reconciliation"):
            return {"reconciliation_required": True, "attempt_id": public_id("att", attempt.id), "status": job.state}
        return {"protocol_version": "0.4.0", "retry_policy": RETRY_POLICY, "server_time": int(utcnow().timestamp()), "job_id": public_id("job", job.id), "attempt_id": public_id("att", attempt.id), "node_id": public_id("node", attempt.node_id), "boot_id": str(attempt.boot_id), "fencing_token": attempt.fence, "lease_expires_at": int(attempt.lease_expires_at.timestamp()), "generation_released": attempt.generation_released_at is not None, "execution": job.execution, "execution_digest": job.execution_fingerprint, "workflow": release.manifest, "workflow_digest": release.digest, "channel": {"id": snapshot["id"], "gpu_id": public_id("gpu", attempt.gpu_id), "revision": snapshot["revision"], "base_url": snapshot["base_url"], "authorization": self.vault.decrypt(snapshot["encrypted_auth"]) if snapshot["encrypted_auth"] else None}}

    def _lock_attempt(self, session: Session, node_id: UUID, boot_id: UUID, attempt_id: UUID, fence: int) -> tuple[Job, Attempt, dict[Any, Any]]:
        node_guard = session.execute(select(Node).where(Node.id == node_id).with_for_update(read=True)).scalar_one()
        if node_guard.current_boot_id != boot_id:
            raise DomainError("stale_boot", "Worker 启动标识已失效")
        preview = session.get(Attempt, attempt_id)
        if not preview or preview.node_id != node_id:
            raise DomainError("not_found", "执行尝试不存在", 404)
        job_preview = session.get(Job, preview.job_id)
        assert job_preview
        job_id = job_preview.id
        buckets = lock_buckets(session, job_preview.tenant_id, job_preview.project_id, job_preview.key_id, job_preview.accepted_period)
        session.expunge(job_preview)
        session.expunge(preview)
        job = session.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
        gpu_id = session.scalar(select(Attempt.gpu_id).where(Attempt.id == attempt_id))
        session.execute(select(Gpu).where(Gpu.id == gpu_id).with_for_update()).scalar_one()
        attempt = session.execute(select(Attempt).where(Attempt.id == attempt_id).with_for_update()).scalar_one()
        node = session.execute(select(Node).where(Node.id == node_id).with_for_update(read=True)).scalar_one_or_none()
        if not node or node.current_boot_id != boot_id or attempt.boot_id != boot_id or attempt.fence != fence or job.current_attempt_id != attempt.id or attempt.closed_at or attempt.lease_expires_at <= utcnow() or job.state in TERMINAL:
            raise DomainError("stale_lease", "执行租约已失效")
        # Once generation is released, later GPU fences must not revoke this result lease.
        return job, attempt, buckets

    def renew(self, node_id: UUID, boot_id: UUID, attempt_id: UUID, fence: int) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job, attempt, _ = self._lock_attempt(session, node_id, boot_id, attempt_id, fence)
            attempt.lease_expires_at = utcnow() + timedelta(seconds=60)
            return {"lease_expires_at": int(attempt.lease_expires_at.timestamp()), "cancel_requested": job.cancellation_requested, "status": job.state}

    def event(self, node_id: UUID, event: WorkerEvent) -> dict[str, Any]:
        attempt_id = parse_id("att", event.attempt_id)
        if parse_id("node", event.node_id) != node_id:
            raise DomainError("identity_mismatch", "Worker 身份不匹配", 403)
        digest = fingerprint(event.model_dump())
        with self.sessions.begin() as session:
            prior = session.scalar(select(WorkerInbox).where(WorkerInbox.event_id == UUID(event.event_id)))
            if prior:
                if prior.attempt_id != attempt_id or prior.digest != digest:
                    raise DomainError("event_conflict", "重复事件内容不一致")
                return prior.outcome
            try:
                job, attempt, buckets = self._lock_attempt(session, node_id, UUID(event.boot_id), attempt_id, event.fencing_token)
            except DomainError:
                # A concurrent copy may have closed this lease while our lock
                # waited. Recheck the now-committed inbox before rejecting it.
                prior = session.scalar(select(WorkerInbox).where(WorkerInbox.event_id == UUID(event.event_id)))
                if prior and prior.attempt_id == attempt_id and prior.digest == digest:
                    return prior.outcome
                raise
            # Check again after serialization: two concurrent deliveries may race before locks.
            prior = session.scalar(select(WorkerInbox).where(WorkerInbox.event_id == UUID(event.event_id)))
            if prior:
                if prior.digest != digest:
                    raise DomainError("event_conflict", "重复事件内容不一致")
                return prior.outcome
            if event.job_id != public_id("job", job.id) or event.journal_seq <= attempt.journal_seq:
                raise DomainError("stale_event", "事件序号或任务不匹配")
            payload = event.payload
            facts = {"current_lease", "generation_slot_held"}
            if event.type == "preparing":
                if payload["execution_digest"] != job.execution_fingerprint:
                    raise DomainError("execution_mismatch", "执行摘要不一致")
                job.state = transition(job.state, "preparing", "worker_preparing", facts)
                if attempt.preparing_at is None:
                    attempt.preparing_at = utcnow()
            elif event.type == "submit_intent":
                release = session.get(WorkflowRelease, job.release_id)
                if not release or payload["workflow_digest"] != release.digest:
                    raise DomainError("workflow_mismatch", "工作流摘要不一致")
                attempt.association_id = UUID(payload["association_id"])
                job.state = transition(job.state, "submitting", "submit_intent", facts | {"inputs_verified", "journal_intent_durable"})
                if attempt.submit_intent_at is None:
                    attempt.submit_intent_at = utcnow()
            elif event.type == "submitted":
                attempt.prompt_id = payload["prompt_id"]
                job.state = transition(job.state, "running", "submitted", facts | {"comfy_acceptance_observed"})
                if attempt.submitted_at is None:
                    attempt.submitted_at = utcnow()
            elif event.type == "outputs_collected":
                attempt.generation_evidence = payload
                job.state = transition(job.state, "collecting", "generation_observed_complete", facts | {"execution_stopped"})
                if attempt.outputs_collected_at is None:
                    attempt.outputs_collected_at = utcnow()
            elif event.type == "gpu_released":
                if payload["gpu_id"] != public_id("gpu", attempt.gpu_id) or not attempt.generation_evidence:
                    raise DomainError("release_evidence_missing", "缺少稳定输出或 GPU 对应证据")
                self._release_generation(session, attempt, buckets, payload)
                job.state = transition(job.state, "uploading", "gpu_released", facts | {"outputs_stable", "execution_stopped", "gpu_release_recorded"})
            elif event.type in ("execution_failed", "cancel_confirmed"):
                if payload.get("execution_stop_confirmed"):
                    oom_proof = confirmed_oom_evidence(payload, prompt_id=attempt.prompt_id, association_id=str(attempt.association_id) if attempt.association_id else None)
                    if "retry_evidence" in payload and not oom_proof:
                        raise DomainError("failure_evidence_mismatch", "失败证明与当前执行尝试不一致", 409)
                    oom_proof = oom_proof and attempt.outputs_collected_at is None and attempt.generation_released_at is None and job.result_manifest is None
                    prior_ooms = len(session.execute(oom_attempts([job.id]).where(Attempt.id != attempt.id).limit(1)).all()) if oom_proof else 0
                    self._release_generation(session, attempt, buckets, payload)
                    now = utcnow()
                    attempt.closed_at = now
                    alternative = has_oom_alternative(session, job, attempt.gpu_id, allow_synthetic_catalog=self.allow_synthetic_catalog) if oom_proof and not prior_ooms and job.retry_count < 2 and not job.cancellation_requested and not job.is_validation else False
                    eligible = retry_after(state=job.state, count=job.retry_count, error_code=payload.get("error_code"), execution_stopped=payload.get("execution_stop_confirmed") is True, evidence_digest=payload.get("evidence_digest"), has_submission=attempt.association_id is not None or attempt.prompt_id is not None, cancelled=job.cancellation_requested or event.type != "execution_failed", validation=job.is_validation, now=now, deadline=job.deadline, oom_failures=prior_ooms, oom_evidence=oom_proof, alternative_gpu=alternative)
                    if eligible is not None:
                        trigger = "retry_confirmed_oom" if payload.get("error_code") == OOM_CODE else "retry_confirmed_failure"
                        job.state = transition(job.state, "retry_wait", trigger, {"execution_stopped", "old_attempt_closed", "retry_budget_available", "resources_accounted", "current_prompt_evidence", "alternative_physical_gpu", "unchanged_generation_spec"})
                        job.retry_count += 1
                        job.retry_after = eligible
                        job.retry_error_code = payload["error_code"]
                        job.retry_source_attempt_id = attempt.id
                        job.current_attempt_id = None
                        job.error_code = None
                        record_change(session, "scheduler", "job.retry_scheduled", job.id, event.event_id, {"count": job.retry_count, "next_at": int(eligible.timestamp()), "error_code": job.retry_error_code})
                    else:
                        target = "cancelled" if job.cancellation_requested or job.state == "cancel_requested" else "failed"
                        if job.state == "reconciling":
                            trigger = "reconciled_cancellation" if target == "cancelled" else "reconciled_permanent_failure"
                        else:
                            trigger = "cancel_confirmed" if target == "cancelled" else "permanent_confirmed_failure"
                        transition(job.state, target, trigger, {"execution_stopped", "safe_failure", "terminal_commit_once", "resources_accounted", "no_result_publish"})
                        terminal_accounting(session, job, buckets, target)
                        job.error_code = payload.get("error_code")
                    if job.state in ("failed", "retry_wait"):
                        confirmed_failure(session, job, attempt, event.event_id, error_code=payload.get("error_code"))
                else:
                    job.state = uncertain(job.state)
                    occupancy = session.get(GpuOccupancy, attempt.gpu_id)
                    if occupancy:
                        occupancy.uncertain = True
            elif event.type == "reconcile_observation":
                if job.state != "reconciling":
                    job.state = uncertain(job.state)
                occupancy = session.get(GpuOccupancy, attempt.gpu_id)
                if occupancy:
                    occupancy.uncertain = True
            elif event.type == "upload_progress" and job.state != "uploading":
                raise DomainError("invalid_event", "任务尚未进入上传状态")
            elif event.type == "progress" and job.state not in ("running", "collecting", "cancel_requested"):
                raise DomainError("invalid_event", "任务尚未运行")
            attempt.journal_seq = event.journal_seq
            job.revision += 1
            outcome = {"accepted": True, "status": job.state, "journal_seq": event.journal_seq}
            session.add(WorkerInbox(event_id=UUID(event.event_id), attempt_id=attempt.id, journal_seq=event.journal_seq, digest=digest, outcome=outcome))
            record_change(session, public_id("node", node_id), "worker." + event.type, job.id, event.event_id)
            return outcome

    def _release_generation(self, session: Session, attempt: Attempt, buckets: dict[Any, Any], evidence: dict[str, Any]) -> None:
        if attempt.generation_released_at:
            return
        occupancy = session.get(GpuOccupancy, attempt.gpu_id)
        if not occupancy or occupancy.attempt_id != attempt.id:
            raise DomainError("occupancy_mismatch", "GPU 占用归属不匹配")
        session.delete(occupancy)
        for (_kind, period), bucket in buckets.items():
            if period == "active":
                bucket.generating_jobs -= 1
        attempt.generation_released_at = utcnow()
        attempt.generation_evidence = {**(attempt.generation_evidence or {}), "release": evidence}
