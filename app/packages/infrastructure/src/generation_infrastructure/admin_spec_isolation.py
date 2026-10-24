from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.spec_isolation import SpecIsolationAction, SpecIsolationCreate
from generation_domain.errors import DomainError
from generation_domain.spec_isolation import spec_digest
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision
from generation_infrastructure.models import (
    Artifact,
    Attempt,
    Channel,
    Gpu,
    Job,
    Node,
    ObjectRecord,
    OutputUpload,
    SpecIsolation,
)
from generation_infrastructure.spec_isolation import projection, state_for
from generation_infrastructure.transactions import record_change


def isolate(row: SpecIsolation) -> None:
    row.is_active, row.isolated_at, row.reason = True, utcnow(), "manual_investigation"
    row.cleared_validation_job_id = None
    row.updated_at, row.revision = utcnow(), row.revision + 1


def require_validation(session: Session, row: SpecIsolation, node: Node, identifier: UUID) -> Job:
    job = session.get(Job, identifier)
    now = utcnow()
    if (job is None or not job.is_validation or job.state != "succeeded" or job.tenant_id != row.tenant_id
            or job.project_id != row.project_id or job.release_id != row.release_id
            or spec_digest(job.task_type, job.execution) != row.spec_digest
            or job.terminal_at is None or not now - timedelta(hours=24) < job.terminal_at <= now
            or row.isolated_at is None or job.created_at <= max(row.isolated_at, row.last_failure_at or row.isolated_at)
            or not job.result_manifest):
        raise DomainError("validation_proof_rejected", "须提供隔离后、同 GPU/发布/规格且 24 小时内成功的显式验证任务", 409)
    attempt = session.get(Attempt, job.current_attempt_id) if job.current_attempt_id else None
    if (attempt is None or attempt.job_id != job.id or attempt.gpu_id != row.gpu_id
            or attempt.node_id != node.id or attempt.boot_id != node.current_boot_id
            or attempt.closed_at is None or attempt.generation_released_at is None):
        raise DomainError("validation_proof_rejected", "验证尝试不属于当前设备启动或未确认生成与结果终态", 409)
    channel = session.get(Channel, attempt.channel_id)
    revision = attempt.channel_snapshot.get("revision")
    if (channel is None or channel.deleted_at is not None or channel.node_id != node.id
            or channel.gpu_id != row.gpu_id or channel.desired_revision != revision
            or channel.applied_revision != revision or channel.verified_revision != revision):
        raise DomainError("validation_proof_rejected", "验证来源渠道配置已变化，须重新验证", 409)
    uploads = list(session.scalars(select(OutputUpload).where(OutputUpload.attempt_id == attempt.id).order_by(OutputUpload.position).limit(101)))
    artifacts = list(session.scalars(select(Artifact).where(Artifact.job_id == job.id).order_by(Artifact.position).limit(101)))
    manifest = job.result_manifest
    if (not 1 <= len(uploads) <= 100 or len(artifacts) != len(uploads)
            or manifest.get("upload_ids") != [public_id("upl", u.id) for u in uploads]
            or manifest.get("digest") != fingerprint([u.specification for u in uploads])
            or [item.get("id") for item in manifest.get("artifacts", [])] != [public_id("content", a.id) for a in artifacts]):
        raise DomainError("validation_proof_rejected", "验证缺少完整的云端产物清单", 409)
    for upload, artifact in zip(uploads, artifacts, strict=True):
        obj = session.get(ObjectRecord, upload.object_id) if upload.object_id else None
        if (obj is None or not obj.verified_at or not obj.version_id or obj.deleted_at is not None
                or obj.delete_requested_at is not None or obj.expires_at <= now
                or artifact.deleted_at is not None or artifact.object_id != obj.id
                or obj.tenant_id != row.tenant_id or obj.project_id != row.project_id):
            raise DomainError("validation_proof_rejected", "验证产物缺少可用的已验证不可变对象", 409)
    return job


class SpecIsolationAdministration(AdminBase):
    def list(self, page: int = 1, state: str = "all", gpu_id: UUID | None = None, release_id: UUID | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            query = select(SpecIsolation)
            if state == "isolated":
                query = query.where(SpecIsolation.is_active.is_(True))
            elif state == "watching":
                query = query.where(SpecIsolation.is_active.is_(False), SpecIsolation.failure_count > 0)
            elif state == "clear":
                query = query.where(SpecIsolation.is_active.is_(False), SpecIsolation.failure_count == 0)
            if gpu_id is not None:
                query = query.where(SpecIsolation.gpu_id == gpu_id)
            if release_id is not None:
                query = query.where(SpecIsolation.release_id == release_id)
            rows = list(session.scalars(query.order_by(SpecIsolation.is_active.desc(), SpecIsolation.updated_at.desc(), SpecIsolation.id).offset((page - 1) * 50).limit(51)))
            return {"data": [projection(row) for row in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def create(self, actor: AdminIdentity, data: SpecIsolationCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "spec.isolate_job", token, data.model_dump())
            if idem.result is not None:
                return idem.result
            identifier = parse_id("job", data.job_id)
            preview = session.get(Job, identifier)
            attempt = session.get(Attempt, preview.current_attempt_id) if preview and preview.current_attempt_id else None
            if attempt is None:
                raise DomainError("execution_required", "来源任务尚未发生执行，不能定位物理 GPU", 409)
            session.execute(select(Node).where(Node.id == attempt.node_id).with_for_update(read=True)).scalar_one()
            job = session.execute(select(Job).where(Job.id == identifier).with_for_update(read=True).execution_options(populate_existing=True)).scalar_one()
            require_revision(job.revision, data.expected_revision)
            if job.current_attempt_id != attempt.id:
                raise DomainError("execution_changed", "任务当前执行已变化，请刷新", 409)
            gpu = session.execute(select(Gpu).where(Gpu.id == attempt.gpu_id).with_for_update()).scalar_one()
            row = state_for(session, job, gpu)
            isolation_before = row.revision
            isolate(row)
            record_change(session, public_id("usr", actor.user_id), "spec.manually_isolated", row.id, request_id, {"job_id": data.job_id, "evidence_id": data.evidence_id, "revision": row.revision}, revision_before=isolation_before, revision_after=row.revision)
            idem.result = projection(row)
            return idem.result

    def action(self, actor: AdminIdentity, identifier: UUID, data: SpecIsolationAction, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "spec.action:" + str(identifier), token, data.model_dump())
            if idem.result is not None:
                return idem.result
            preview = session.get(SpecIsolation, identifier)
            gpu = session.get(Gpu, preview.gpu_id) if preview else None
            if gpu is None:
                raise DomainError("not_found", "规格状态不存在", 404)
            node = session.execute(select(Node).where(Node.id == gpu.node_id).with_for_update(read=True)).scalar_one()
            session.execute(select(Gpu).where(Gpu.id == gpu.id).with_for_update()).scalar_one()
            row = session.execute(select(SpecIsolation).where(SpecIsolation.id == identifier).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
            if row is None:
                raise DomainError("not_found", "规格状态不存在", 404)
            require_revision(row.revision, data.expected_revision)
            detail: dict[str, Any]
            if data.action == "isolate":
                isolate(row)
                detail = {"evidence_id": data.evidence_id}
            else:
                if not row.is_active:
                    raise DomainError("spec_not_isolated", "该规格当前未隔离", 409)
                job = require_validation(session, row, node, parse_id("job", data.validation_job_id))
                row.is_active, row.failure_count, row.cleared_validation_job_id = False, 0, job.id
                row.updated_at, row.revision = utcnow(), row.revision + 1
                detail = {"validation_job_id": data.validation_job_id}
            record_change(session, public_id("usr", actor.user_id), "spec." + data.action, row.id, request_id, {**detail, "revision": row.revision}, revision_before=data.expected_revision, revision_after=row.revision)
            idem.result = projection(row)
            return idem.result
