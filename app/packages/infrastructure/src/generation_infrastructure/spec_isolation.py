from __future__ import annotations

from datetime import timedelta
from typing import Any

from generation_domain.spec_isolation import (
    FAILURE_THRESHOLD,
    FAILURE_WINDOW_SECONDS,
    ISOLATING_FAILURES,
    generation_spec,
    spec_digest,
)
from generation_domain.values import public_id, utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.models import Attempt, Gpu, Job, SpecIsolation
from generation_infrastructure.transactions import record_change


def matching_state(session: Session, job: Job, gpu: Gpu) -> SpecIsolation | None:
    # Callers hold the physical GPU row lock. It serializes absent-row creation too.
    return session.scalar(select(SpecIsolation).where(SpecIsolation.gpu_id == gpu.id, SpecIsolation.release_id == job.release_id, SpecIsolation.spec_digest == spec_digest(job.task_type, job.execution)).execution_options(populate_existing=True))


def state_for(session: Session, job: Job, gpu: Gpu) -> SpecIsolation:
    row = matching_state(session, job, gpu)
    if row is None:
        row = SpecIsolation(tenant_id=job.tenant_id, project_id=job.project_id, gpu_id=gpu.id, release_id=job.release_id, spec_digest=spec_digest(job.task_type, job.execution), specification=generation_spec(job.task_type, job.execution), is_active=False, revision=1, failure_count=0, updated_at=utcnow())
        session.add(row)
        session.flush()
    return row


def blocks_claim(session: Session, job: Job, gpu: Gpu) -> bool:
    row = matching_state(session, job, gpu)
    return bool(row is not None and row.is_active and not job.is_validation)


def confirmed_failure(session: Session, job: Job, attempt: Attempt, request_id: str) -> None:
    if job.state != "failed" or job.error_code not in ISOLATING_FAILURES or attempt.generation_released_at is None:
        return
    gpu = session.get(Gpu, attempt.gpu_id)
    assert gpu is not None
    row = state_for(session, job, gpu)
    at = attempt.generation_released_at
    if row.last_success_generation_at is not None and at <= row.last_success_generation_at:
        return
    window = timedelta(seconds=FAILURE_WINDOW_SECONDS)
    if row.last_failure_at is not None and at < row.last_failure_at - window:
        return
    if row.last_failure_at is None or at > row.last_failure_at + window:
        row.failure_count = 1
    else:
        row.failure_count = min(1000000, row.failure_count + 1)
    if row.last_failure_at is None or at >= row.last_failure_at:
        row.last_failure_at, row.last_failure_job_id, row.last_error_code = at, job.id, job.error_code
    if row.failure_count >= FAILURE_THRESHOLD and not row.is_active:
        row.is_active, row.isolated_at, row.reason = True, utcnow(), "repeated_failure"
        row.cleared_validation_job_id = None
    row.updated_at, row.revision = utcnow(), row.revision + 1
    record_change(session, "system", "spec.failure_observed", row.id, request_id, {"job_id": public_id("job", job.id), "error_code": job.error_code, "is_active": row.is_active, "revision": row.revision})


def verified_success(session: Session, job: Job, attempt: Attempt, request_id: str) -> None:
    assert job.state == "succeeded" and attempt.generation_released_at is not None
    gpu = session.get(Gpu, attempt.gpu_id)
    assert gpu is not None
    row = state_for(session, job, gpu)
    at = attempt.generation_released_at
    if row.last_success_generation_at is not None and at <= row.last_success_generation_at:
        return
    row.last_success_generation_at = at
    if not row.is_active and (row.last_failure_at is None or at > row.last_failure_at):
        row.failure_count = 0
    row.updated_at, row.revision = utcnow(), row.revision + 1
    record_change(session, "system", "spec.success_observed", row.id, request_id, {"job_id": public_id("job", job.id), "is_active": row.is_active, "revision": row.revision})


def projection(row: SpecIsolation) -> dict[str, Any]:
    def at(name: str) -> int | None:
        value = getattr(row, name)
        return int(value.timestamp()) if value is not None else None
    return {"id": public_id("iso", row.id), "tenant_id": public_id("ten", row.tenant_id), "project_id": public_id("prj", row.project_id), "gpu_id": public_id("gpu", row.gpu_id), "release_id": public_id("wfr", row.release_id), "spec_digest": row.spec_digest, "specification": row.specification, "is_active": row.is_active, "revision": row.revision, "failure_count": row.failure_count, "last_failure_at": at("last_failure_at"), "last_failure_job_id": public_id("job", row.last_failure_job_id) if row.last_failure_job_id else None, "last_error_code": row.last_error_code, "last_success_generation_at": at("last_success_generation_at"), "isolated_at": at("isolated_at"), "reason": row.reason, "cleared_validation_job_id": public_id("job", row.cleared_validation_job_id) if row.cleared_validation_job_id else None, "updated_at": at("updated_at")}


def prune_inactive(sessions: sessionmaker[Session]) -> int:
    cutoff, deleted = utcnow() - timedelta(days=30), 0
    with sessions() as session:
        candidates = list(session.execute(select(SpecIsolation.id, SpecIsolation.gpu_id).where(SpecIsolation.is_active.is_(False), SpecIsolation.updated_at < cutoff).order_by(SpecIsolation.updated_at, SpecIsolation.id).limit(100)))
    for identifier, gpu_id in candidates:
        with sessions.begin() as session:
            gpu = session.scalar(select(Gpu).where(Gpu.id == gpu_id).with_for_update(skip_locked=True))
            if gpu is None:
                continue
            row = session.get(SpecIsolation, identifier)
            if row is not None and not row.is_active and row.updated_at < cutoff:
                session.delete(row)
                deleted += 1
    return deleted
