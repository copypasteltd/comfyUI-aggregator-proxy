from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from generation_contracts.recovery import Reconciliation
from generation_domain.errors import DomainError
from generation_domain.state import TERMINAL, transition, uncertain
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from sqlalchemy import select

from generation_infrastructure.jobs import terminal_accounting
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
            buckets = lock_buckets(session, job_preview.tenant_id, job_preview.project_id, job_preview.key_id, job_preview.accepted_period)
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
            if data.observation != "host_restarted" and attempt.boot_id == node.current_boot_id and attempt.fence > data.previous_fencing_token and attempt.lease_expires_at > utcnow():
                claim = self.leases._claim_projection(session, job, attempt, channel)
                return {"status": "resumed", "lease": {**claim, "resume_state": job.state, "prompt_id": attempt.prompt_id, "journal_seq": attempt.journal_seq}}
            if str(attempt.boot_id) != data.previous_boot_id or attempt.fence != data.previous_fencing_token or attempt.closed_at:
                raise DomainError("stale_attempt", "核对的旧执行标识不一致")
            if job.state != "reconciling":
                job.state = uncertain(job.state)
            occupancy = session.get(GpuOccupancy, gpu.id)
            if occupancy and occupancy.attempt_id == attempt.id:
                occupancy.uncertain = True
            if data.host_checkpoint is not None or data.host_restart is not None:
                if attempt.generation_released_at or attempt.outputs_collected_at or job.result_manifest is not None or not occupancy or occupancy.attempt_id != attempt.id:
                    raise DomainError("host_recovery_unavailable", "当前尝试不能使用主机重启收口", 409)
                if data.association_id != (str(attempt.association_id) if attempt.association_id else None) or data.prompt_id != attempt.prompt_id:
                    raise DomainError("host_recovery_identity_mismatch", "主机恢复证据与原提交不符", 409)
                binding = {"node_id": public_id("node", node_id), "attempt_id": data.attempt_id, "boot_id": str(attempt.boot_id), "fence": attempt.fence, "association_id": data.association_id, "prompt_id": attempt.prompt_id, "execution_digest": job.execution_fingerprint, "gpu_id": public_id("gpu", gpu.id), "gpu_hardware_id": gpu.hardware_id, "channel_digest": fingerprint({"base_url": attempt.channel_snapshot["base_url"].rstrip("/")})}
                checkpoint = attempt.recovery_checkpoint
                if data.host_checkpoint is not None:
                    observed = data.host_checkpoint.model_dump()
                    if observed["gpu_hardware_id"] != gpu.hardware_id or observed["channel_digest"] != binding["channel_digest"]:
                        raise DomainError("host_recovery_identity_mismatch", "检查点对应其他 GPU 或渠道", 409)
                    if checkpoint is None:
                        checkpoint = {"observation": observed, "binding": binding, "worker_boot_id": data.boot_id}
                        checkpoint["digest"] = fingerprint(checkpoint)
                        attempt.recovery_checkpoint = checkpoint
                        job.revision += 1
                        record_change(session, public_id("node", node_id), "recovery.host_checkpoint", job.id, checkpoint["digest"], {"attempt_id": data.attempt_id, "checkpoint_digest": checkpoint["digest"]})
                    elif checkpoint.get("observation") != observed or checkpoint.get("binding") != binding:
                        raise DomainError("host_recovery_checkpoint_conflict", "持久检查点已存在，禁止覆盖", 409)
                    return {"status": "attention_required", "safe_to_resubmit": False, "host_checkpoint_digest": checkpoint["digest"]}
                proof = data.host_restart
                assert proof is not None
                if data.evidence_digest != fingerprint({"observation": data.observation, "source": data.source, "prompt_id": data.prompt_id, "host_restart": proof.model_dump()}):
                    raise DomainError("host_recovery_evidence_mismatch", "停止确认摘要与证明内容不符", 409)
                if not checkpoint or checkpoint.get("binding") != binding or checkpoint.get("digest") != proof.checkpoint_digest or checkpoint.get("worker_boot_id") == data.boot_id:
                    raise DomainError("host_recovery_checkpoint_mismatch", "缺少与旧执行匹配的重启前检查点", 409)
                baseline = checkpoint["observation"]
                if proof.host_identity_digest != baseline["host_identity_digest"] or proof.uptime_ms + 1000 > baseline["uptime_ms"]:
                    raise DomainError("host_restart_not_proven", "未能证明同一主机已重新启动", 409)
                release = {"error_code": "execution_host_restarted", "execution_stop_confirmed": True, "evidence_digest": data.evidence_digest, "host_restart": proof.model_dump()}
                self.leases._release_generation(session, attempt, buckets, release)
                attempt.closed_at = utcnow()
                target = "cancelled" if job.cancellation_requested else "failed"
                trigger = "reconciled_cancellation" if target == "cancelled" else "reconciled_permanent_failure"
                transition(job.state, target, trigger, {"execution_stopped", "safe_failure", "terminal_commit_once", "resources_accounted", "no_result_publish"})
                terminal_accounting(session, job, buckets, target)
                job.error_code = None if target == "cancelled" else "execution_host_restarted"
                record_change(session, public_id("node", node_id), "recovery.host_restart_confirmed", job.id, data.evidence_digest, {"attempt_id": data.attempt_id, "checkpoint_digest": proof.checkpoint_digest, "outcome": target})
                return {"status": "closed", "job_status": job.state}
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
            attempt.recovery_checkpoint = None
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
