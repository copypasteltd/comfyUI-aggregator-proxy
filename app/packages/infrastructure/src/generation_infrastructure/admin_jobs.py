from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity, BusinessIdentity
from generation_contracts.admin import JobPriority
from generation_contracts.queue_queries import QueueQuery
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision, timestamp
from generation_infrastructure.jobs import JobRepository, job_projection
from generation_infrastructure.models import ApiKey, Artifact, Attempt, GpuOccupancy, Job
from generation_infrastructure.operational import attempt_timing, job_timing
from generation_infrastructure.queue_queries import begin_queue_read, job_filters, waiting_times
from generation_infrastructure.transactions import record_change


def admin_job_projection(job: Job, first_assigned: datetime | None = None, now: datetime | None = None) -> dict[str, Any]:
    return {**job_projection(job), **waiting_times(job, first_assigned, now or utcnow()), "tenant_id": public_id("ten", job.tenant_id), "project_id": public_id("prj", job.project_id), "key_id": public_id("key", job.key_id), "priority": job.priority, "is_validation": job.is_validation, "cancellation_requested": job.cancellation_requested, "retry_of": public_id("job", job.retry_of) if job.retry_of else None}


def require_job(session: Session, identifier: UUID) -> Job:
    job = session.get(Job, identifier)
    if not job:
        raise DomainError("not_found", "任务不存在", 404)
    return job


class JobAdministration(AdminBase):
    def list(self, query: QueueQuery) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            now = utcnow()
            rows = list(session.scalars(select(Job).where(*job_filters(query)).order_by(Job.created_at.desc(), Job.id).offset((query.page - 1) * 50).limit(51)))
            first = {identifier: assigned for identifier, assigned in session.execute(select(Attempt.job_id, func.min(Attempt.created_at)).where(Attempt.job_id.in_([row.id for row in rows[:50]])).group_by(Attempt.job_id))} if rows else {}
            return {"data": [admin_job_projection(row, first.get(row.id), now) for row in rows[:50]], "page": query.page, "has_more": len(rows) > 50, "snapshot_at": int(now.timestamp()), "filters": query.filters()}

    def detail(self, actor: AdminIdentity, identifier: UUID, request_id: str, *, sensitive: bool = False) -> dict[str, Any]:
        with self.sessions.begin() as session:
            if sensitive:
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                session.execute(text("SET LOCAL lock_timeout = '1s'"))
            else:
                begin_queue_read(session)
            now = utcnow()
            job = require_job(session, identifier)
            first_assigned = session.scalar(select(func.min(Attempt.created_at)).where(Attempt.job_id == job.id))
            attempts = session.execute(select(Attempt, GpuOccupancy).outerjoin(GpuOccupancy, GpuOccupancy.attempt_id == Attempt.id).where(Attempt.job_id == job.id).order_by((Attempt.id == job.current_attempt_id).desc(), Attempt.created_at.desc(), Attempt.id).limit(101)).all()
            shown = sorted(attempts[:100], key=lambda row: (row[0].created_at, row[0].id))
            result = {**admin_job_projection(job, first_assigned, now), "timing": job_timing(job, first_assigned, now), "attempts_has_more": len(attempts) > 100, "current_attempt_id": public_id("att", job.current_attempt_id) if job.current_attempt_id else None, "release_id": public_id("wfr", job.release_id), "attempts": [{"id": public_id("att", a.id), "node_id": public_id("node", a.node_id), "channel_id": public_id("ch", a.channel_id), "gpu_id": public_id("gpu", a.gpu_id), "fence": a.fence, "lease_expires_at": timestamp(a.lease_expires_at), "generation_released_at": timestamp(a.generation_released_at), "closed_at": timestamp(a.closed_at), "has_prompt_id": bool(a.prompt_id), "has_generation_evidence": a.generation_evidence is not None, "gpu_occupied": occupancy is not None, "occupancy_uncertain": occupancy.uncertain if occupancy else False, "timing": attempt_timing(a, job, now)} for a, occupancy in shown], "retries": [public_id("job", retry) for retry in session.scalars(select(Job.id).where(Job.retry_of == job.id).order_by(Job.created_at, Job.id).limit(100))], "artifacts": [public_id("content", artifact) for artifact in session.scalars(select(Artifact.id).where(Artifact.job_id == job.id, Artifact.deleted_at.is_(None)).order_by(Artifact.position))]}
            if sensitive:
                result["input"] = job.request["input"]
                record_change(session, public_id("usr", actor.user_id), "job.sensitive_read", job.id, request_id)
            return result

    def cancel(self, actor: AdminIdentity, identifier: UUID, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "job.cancel:" + str(identifier), token, {})
            if idem.result is not None:
                return idem.result
            job = require_job(session, identifier)
            identity = BusinessIdentity(job.tenant_id, job.project_id, job.key_id, {})
            session.expunge(job)
            JobRepository(self.sessions).cancel(identity, identifier, request_id, _session=session, audit_actor=public_id("usr", actor.user_id))
            idem.result = admin_job_projection(require_job(session, identifier))
            return idem.result

    def retry(self, actor: AdminIdentity, identifier: UUID, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "job.retry:" + str(identifier), token, {})
            if idem.result is not None:
                return idem.result
            job = require_job(session, identifier)
            key = session.get(ApiKey, job.key_id)
            assert key is not None
            identity = BusinessIdentity(job.tenant_id, job.project_id, key.id, key.policy)
            request = deepcopy(job.request)
            request.setdefault("parameters", {})["seed"] = job.execution["parameters"]["seed"]
            session.expunge(key)
            # Derive a durable, non-client-visible business operation from the admin mutation.
            result = JobRepository(self.sessions).create(identity, request, "admin-retry:" + str(idem.id), request_id, _session=session, retry_of=identifier, audit_actor=public_id("usr", actor.user_id))
            idem.result = admin_job_projection(require_job(session, parse_id("job", result["id"])))
            return idem.result

    def priority(self, actor: AdminIdentity, identifier: UUID, data: JobPriority, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "job.priority:" + str(identifier), token, data.model_dump(mode="json"))
            if idem.result is not None:
                return idem.result
            job = session.scalar(select(Job).where(Job.id == identifier).with_for_update(key_share=True))
            if not job:
                raise DomainError("not_found", "任务不存在", 404)
            require_revision(job.revision, data.expected_revision)
            if job.state not in ("queued", "retry_wait") or job.current_attempt_id:
                raise DomainError("priority_not_available", "仅未领取的排队任务可修改优先级", 409)
            job.priority = data.priority
            job.revision += 1
            record_change(session, public_id("usr", actor.user_id), "job.priority_changed", job.id, request_id, {"priority": job.priority, "revision": job.revision}, revision_before=data.expected_revision, revision_after=job.revision)
            idem.result = admin_job_projection(job)
            return idem.result
