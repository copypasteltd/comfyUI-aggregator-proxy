from __future__ import annotations

from uuid import uuid4

from generation_domain.state import TERMINAL, transition, uncertain
from generation_domain.values import utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.access_events import prune_access_events
from generation_infrastructure.audit_retention import prune_audit_events
from generation_infrastructure.gateway import GatewayRepository
from generation_infrastructure.job_cancellations import advance_job_cancellations
from generation_infrastructure.jobs import terminal_accounting
from generation_infrastructure.models import Attempt, Gpu, GpuOccupancy, Job
from generation_infrastructure.retries import promote_due_retries
from generation_infrastructure.spec_isolation import prune_inactive
from generation_infrastructure.transactions import lock_buckets, record_change


class Maintenance:
    def __init__(self, sessions: sessionmaker[Session], access_retention_days: int = 30, audit_retention_days: int | None = None) -> None:
        self.sessions = sessions
        self.access_retention_days = access_retention_days
        self.audit_retention_days = audit_retention_days

    def tick(self) -> dict[str, int]:
        expired, uncertain_count = 0, 0
        cancellations = advance_job_cancellations(self.sessions)
        with self.sessions() as session:
            queued = list(session.scalars(select(Job.id).where(Job.state.in_(("queued", "retry_wait")), Job.deadline <= utcnow()).order_by(Job.id).limit(100)))
            leases = list(session.scalars(select(Attempt.id).join(Job, Job.current_attempt_id == Attempt.id).where(Attempt.closed_at.is_(None), Attempt.lease_expires_at <= utcnow(), Job.state.not_in(TERMINAL), Job.state != "reconciling").order_by(Attempt.id).limit(100)))
        for identifier in queued:
            with self.sessions.begin() as session:
                preview = session.get(Job, identifier)
                if not preview:
                    continue
                buckets = lock_buckets(session, preview.tenant_id, preview.project_id, preview.key_id, preview.accepted_period)
                session.expunge(preview)
                job = session.execute(select(Job).where(Job.id == identifier).with_for_update()).scalar_one()
                if job.state in ("queued", "retry_wait") and job.current_attempt_id is None and job.deadline <= utcnow():
                    target = transition(job.state, "expired", "queue_timeout", {"deadline_reached", "not_running_proven", "terminal_commit_once", "resources_accounted"})
                    terminal_accounting(session, job, buckets, target)
                    record_change(session, "runner", "job.queue_expired", job.id, str(uuid4()))
                    expired += 1
        for identifier in leases:
            with self.sessions.begin() as session:
                attempt_preview = session.get(Attempt, identifier)
                if not attempt_preview:
                    continue
                job_preview = session.get(Job, attempt_preview.job_id)
                assert job_preview
                lock_buckets(session, job_preview.tenant_id, job_preview.project_id, job_preview.key_id, job_preview.accepted_period)
                job_id = job_preview.id
                gpu_id = attempt_preview.gpu_id
                session.expunge(job_preview)
                session.expunge(attempt_preview)
                job = session.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
                session.execute(select(Gpu).where(Gpu.id == gpu_id).with_for_update()).scalar_one()
                attempt = session.execute(select(Attempt).where(Attempt.id == identifier).with_for_update()).scalar_one()
                if job.current_attempt_id != identifier or job.state in TERMINAL or job.state == "reconciling" or attempt.closed_at or attempt.lease_expires_at > utcnow():
                    continue
                job.state = uncertain(job.state)
                job.revision += 1
                occupancy = session.get(GpuOccupancy, gpu_id)
                if occupancy and occupancy.attempt_id == attempt.id:
                    occupancy.uncertain = True
                record_change(session, "runner", "job.lease_expired_reconcile", job.id, str(uuid4()))
                uncertain_count += 1
        prune_inactive(self.sessions)
        return {"queue_expired": expired, "lease_reconciling": uncertain_count, "cancellation_targets_processed": cancellations, "retry_queued": promote_due_retries(self.sessions), "admission_expired": GatewayRepository(self.sessions).expire_pending(), "access_events_deleted": prune_access_events(self.sessions, self.access_retention_days), "audit_events_deleted": prune_audit_events(self.sessions, self.audit_retention_days)}
