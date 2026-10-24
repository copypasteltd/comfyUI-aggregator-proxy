from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from generation_contracts.recovery import Reconciliation
from generation_domain.errors import DomainError
from generation_domain.state import TERMINAL, transition, uncertain
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import select

from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import Attempt, Channel, Gpu, GpuOccupancy, Job, Node
from generation_infrastructure.transactions import lock_buckets, record_change


class RecoveryRepository:
    def __init__(self, leases: LeaseRepository) -> None:
        self.leases = leases

    def reconcile(self, node_id: UUID, data: Reconciliation) -> dict[str, Any]:
        attempt_id = parse_id("att", data.attempt_id)
        with self.leases.sessions.begin() as session:
            node = session.execute(select(Node).where(Node.id == node_id).with_for_update(read=True)).scalar_one()
            if str(node.current_boot_id) != data.boot_id:
                raise DomainError("stale_boot", "当前 Worker 启动标识无效")
            assert node.current_boot_id is not None
            preview = session.get(Attempt, attempt_id)
            if not preview or preview.node_id != node_id:
                raise DomainError("not_found", "执行尝试不存在", 404)
            job_preview = session.get(Job, preview.job_id)
            assert job_preview
            job_id, gpu_id = job_preview.id, preview.gpu_id
            lock_buckets(session, job_preview.tenant_id, job_preview.project_id, job_preview.key_id, job_preview.accepted_period)
            session.expunge(job_preview)
            session.expunge(preview)
            job = session.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
            gpu = session.execute(select(Gpu).where(Gpu.id == gpu_id).with_for_update()).scalar_one()
            attempt = session.execute(select(Attempt).where(Attempt.id == attempt_id).with_for_update()).scalar_one()
            channel = session.get(Channel, attempt.channel_id)
            assert channel
            if job.current_attempt_id != attempt.id:
                raise DomainError("stale_attempt", "执行已被替代，不能恢复")
            if job.state in TERMINAL:
                return {"status": "closed", "job_status": job.state}
            # A lost recovery response can replay the same decision under the new boot.
            if attempt.boot_id == node.current_boot_id and attempt.fence > data.previous_fencing_token and attempt.lease_expires_at > utcnow():
                claim = self.leases._claim_projection(session, job, attempt, channel)
                return {"status": "resumed", "lease": {**claim, "resume_state": job.state, "prompt_id": attempt.prompt_id, "journal_seq": attempt.journal_seq}}
            if str(attempt.boot_id) != data.previous_boot_id or attempt.fence != data.previous_fencing_token or attempt.closed_at:
                raise DomainError("stale_attempt", "核对的旧执行标识不一致")
            if job.state != "reconciling":
                job.state = uncertain(job.state)
            occupancy = session.get(GpuOccupancy, gpu.id)
            if occupancy and occupancy.attempt_id == attempt.id:
                occupancy.uncertain = True
            can_resume = False
            target = "running"
            if attempt.generation_released_at:
                target = "uploading"
                can_resume = data.observation == "local_outputs" and bool(attempt.generation_evidence) and data.manifest_digest == (attempt.generation_evidence or {}).get("manifest_digest")
            elif attempt.association_id and str(attempt.association_id) == data.association_id and data.observation == "found" and data.source in ("queue_running", "queue_pending", "history") and data.prompt_id and occupancy and occupancy.attempt_id == attempt.id and (attempt.prompt_id is None or attempt.prompt_id == data.prompt_id):
                attempt.prompt_id = data.prompt_id
                can_resume = True
            if not can_resume:
                job.revision += 1
                record_change(session, public_id("node", node_id), "recovery.attention", job.id, data.evidence_digest, {"observation": data.observation, "source": data.source})
                return {"status": "attention_required", "safe_to_resubmit": False}
            if job.cancellation_requested:
                target = "cancel_requested"
            # Restore only the existing attempt; never create/re-submit a new execution.
            gpu.fence += 1
            attempt.fence, attempt.boot_id = gpu.fence, node.current_boot_id
            attempt.lease_expires_at = utcnow() + timedelta(seconds=60)
            if occupancy and occupancy.attempt_id == attempt.id:
                occupancy.uncertain = False
            job.state = transition(job.state, target, "resume_confirmed_attempt", {"resume_authorized"})
            job.revision += 1
            record_change(session, public_id("node", node_id), "recovery.resumed", job.id, data.evidence_digest)
            claim = self.leases._claim_projection(session, job, attempt, channel)
            return {"status": "resumed", "lease": {**claim, "resume_state": job.state, "prompt_id": attempt.prompt_id, "journal_seq": attempt.journal_seq}}

    def context(self, node_id: UUID, boot_id: UUID, attempt_id: UUID) -> dict[str, Any]:
        with self.leases.sessions() as session:
            node = session.get(Node, node_id)
            attempt = session.get(Attempt, attempt_id)
            if not node or node.current_boot_id != boot_id or not attempt or attempt.node_id != node_id:
                raise DomainError("not_found", "当前节点没有此执行的核对权限", 404)
            job = session.get(Job, attempt.job_id)
            channel = session.get(Channel, attempt.channel_id)
            assert job and channel
            return self.leases._claim_projection(session, job, attempt, channel)
