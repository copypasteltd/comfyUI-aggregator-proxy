from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from generation_application.ports import BusinessIdentity
from generation_application.storage import ObjectStore
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_domain.workflow_contexts import CONTEXT_MIME, MAX_CONTEXT_BYTES, inspect_context
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.models import (
    Job,
    JobContextReference,
    ObjectRecord,
    WorkflowContext,
    WorkflowRelease,
)

if TYPE_CHECKING:
    from generation_infrastructure.leases import LeaseRepository


def freeze_context(session: Session, identity: BusinessIdentity, manifest: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any] | None:
    reference = supplied.get("context")
    if reference is None:
        return None
    identity.require("jobs:read")
    profile = manifest.get("context") or {}
    result = session.execute(select(WorkflowContext, ObjectRecord).join(ObjectRecord, WorkflowContext.object_id == ObjectRecord.id).join(Job, WorkflowContext.job_id == Job.id).where(
        Job.id == parse_id("job", reference["job_id"]), Job.tenant_id == identity.tenant_id,
        Job.project_id == identity.project_id, Job.key_id == identity.key_id,
        Job.state == "succeeded", Job.current_attempt_id == WorkflowContext.attempt_id,
        ObjectRecord.deleted_at.is_(None), ObjectRecord.delete_requested_at.is_(None),
        ObjectRecord.expires_at > utcnow(), ObjectRecord.verified_at.is_not(None),
        ObjectRecord.version_id.is_not(None),
    ).with_for_update(of=ObjectRecord)).one_or_none()
    if result is None:
        raise DomainError("workflow_context_unavailable", "父任务上下文不可用、未成功、已到期或不属于当前 Key", 404)
    context, obj = result
    if not profile.get("load") or profile.get("compatibility") != context.compatibility:
        raise DomainError("workflow_context_incompatible", "父任务与当前工作流的续拍上下文不兼容", 422)
    return {"id": public_id("ctx", context.id), "sha256": obj.sha256, "bytes": obj.bytes, "family": "h3-av-v1", "compatibility": context.compatibility}


class ContextRepository:
    def __init__(self, sessions: sessionmaker[Session], store: ObjectStore, leases: LeaseRepository) -> None:
        self.sessions, self.store, self.leases = sessions, store, leases

    def _producer(self, session: Session, node: UUID, attempt: UUID, boot: UUID, fence: int) -> tuple[Job, str]:
        job, row, _ = self.leases._lock_attempt(session, node, boot, attempt, fence)
        release = session.get(WorkflowRelease, job.release_id)
        profile = release.manifest.get("context") if release else None
        if job.state != "uploading" or not row.generation_released_at or not profile or not profile.get("save_node_id"):
            raise DomainError("workflow_context_not_expected", "当前租约不允许提交续拍上下文", 409)
        return job, profile["compatibility"]

    def preflight(self, node: UUID, attempt: UUID, boot: UUID, fence: int) -> None:
        with self.sessions.begin() as session:
            self._producer(session, node, attempt, boot, fence)

    def accept(self, node: UUID, attempt: UUID, boot: UUID, fence: int, source: Path) -> dict[str, Any]:
        self.preflight(node, attempt, boot, fence)
        metadata = inspect_context(source)
        digest = hashlib.sha256()
        with source.open("rb") as incoming:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        # A unique object version is never overwritten, including after a lost ACK.
        key = "contexts/" + attempt.hex + "/" + uuid4().hex
        with source.open("rb") as incoming:
            stored = self.store.put(key, incoming, CONTEXT_MIME, MAX_CONTEXT_BYTES)
        try:
            if stored.sha256 != sha or stored.bytes != source.stat().st_size:
                raise DomainError("workflow_context_digest_mismatch", "上下文存储完整性校验失败", 503)
            confirmed, size = hashlib.sha256(), 0
            for chunk in self.store.read(stored.key, stored.version, 0, stored.bytes - 1):
                size += len(chunk)
                confirmed.update(chunk)
            if size != stored.bytes or confirmed.hexdigest() != sha:
                raise DomainError("workflow_context_digest_mismatch", "上下文存储回读校验失败", 503)
            with self.sessions.begin() as session:
                job, compatibility = self._producer(session, node, attempt, boot, fence)
                old = session.scalar(select(WorkflowContext).where(WorkflowContext.attempt_id == attempt))
                if old:
                    obj = session.get(ObjectRecord, old.object_id)
                    if not obj or obj.sha256 != sha or obj.bytes != stored.bytes or old.compatibility != compatibility:
                        raise DomainError("workflow_context_conflict", "同一执行的上下文不可变", 409)
                    return {"verified": True, "sha256": sha, "bytes": obj.bytes}
                obj = ObjectRecord(tenant_id=job.tenant_id, project_id=job.project_id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=sha, media_type=CONTEXT_MIME, verified_at=utcnow(), metadata_json={**metadata, "compatibility": compatibility}, expires_at=utcnow() + timedelta(hours=8))
                session.add(obj)
                session.flush()
                session.add(WorkflowContext(tenant_id=job.tenant_id, project_id=job.project_id, key_id=job.key_id, job_id=job.id, attempt_id=attempt, object_id=obj.id, compatibility=compatibility))
            return {"verified": True, "sha256": sha, "bytes": stored.bytes}
        finally:
            # Wait for any uncertain publisher before deciding whether bytes are unreferenced.
            try:
                with self.sessions.begin() as session:
                    session.scalar(select(Job.id).where(Job.current_attempt_id == attempt).with_for_update())
                    published = session.scalar(select(ObjectRecord.id).where(ObjectRecord.object_key == stored.key, ObjectRecord.version_id == stored.version))
                if published is None:
                    self.store.delete(stored.key, stored.version)
            except Exception:
                logging.getLogger(__name__).warning("workflow_context_cleanup_deferred")

    def input_object(self, node: UUID, attempt: UUID, boot: UUID, fence: int) -> ObjectRecord:
        with self.sessions.begin() as session:
            job, _, _ = self.leases._lock_attempt(session, node, boot, attempt, fence)
            frozen = job.execution.get("context")
            reference = session.get(JobContextReference, job.id)
            context = session.get(WorkflowContext, reference.context_id) if reference else None
            obj = session.get(ObjectRecord, context.object_id) if context else None
            if not frozen or not context or not obj or context.key_id != job.key_id or context.project_id != job.project_id or context.tenant_id != job.tenant_id or public_id("ctx", context.id) != frozen["id"] or obj.deleted_at or obj.delete_requested_at or not obj.verified_at or not obj.version_id or obj.sha256 != frozen["sha256"] or obj.bytes != frozen["bytes"]:
                raise DomainError("workflow_context_unavailable", "当前执行没有可用的冻结上下文", 404)
            session.expunge(obj)
            return obj
