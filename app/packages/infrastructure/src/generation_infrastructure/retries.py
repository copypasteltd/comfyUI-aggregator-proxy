from __future__ import annotations

import re
from uuid import uuid4

from generation_domain.retries import (
    MAX_RETRIES,
    OOM_CODE,
    RETRY_CODE,
    RETRY_CODES,
    confirmed_oom_evidence,
)
from generation_domain.state import transition
from generation_domain.values import utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.models import Attempt, GpuOccupancy, Job
from generation_infrastructure.transactions import lock_buckets, record_change


def promote_due_retries(sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        identifiers = list(session.scalars(select(Job.id).where(Job.state == "retry_wait", Job.retry_after <= utcnow()).order_by(Job.retry_after, Job.id).limit(100)))
    promoted = 0
    for identifier in identifiers:
        with sessions.begin() as session:
            preview = session.get(Job, identifier)
            if preview is None:
                continue
            lock_buckets(session, preview.tenant_id, preview.project_id, preview.key_id, preview.accepted_period)
            session.expunge(preview)
            job = session.execute(select(Job).where(Job.id == identifier).with_for_update()).scalar_one()
            now = utcnow()
            if (job.state != "retry_wait" or job.current_attempt_id is not None or job.retry_after is None
                    or job.retry_after > now or job.deadline <= now or not 1 <= job.retry_count <= MAX_RETRIES
                    or job.retry_error_code not in RETRY_CODES or job.terminal_at is not None or job.result_manifest is not None
                    or job.cancellation_requested or job.is_validation):
                continue
            source = session.get(Attempt, job.retry_source_attempt_id) if job.retry_source_attempt_id else None
            if source is None or source.job_id != job.id or source.closed_at is None or source.generation_released_at is None:
                continue
            release = (source.generation_evidence or {}).get("release")
            if not isinstance(release, dict) or release.get("execution_stop_confirmed") is not True or release.get("error_code") != job.retry_error_code:
                continue
            if not isinstance(release.get("evidence_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", release["evidence_digest"]):
                continue
            if job.retry_error_code == RETRY_CODE and (source.association_id is not None or source.prompt_id is not None):
                continue
            if job.retry_error_code == OOM_CODE and (source.outputs_collected_at is not None or not confirmed_oom_evidence(release, prompt_id=source.prompt_id, association_id=str(source.association_id) if source.association_id else None)):
                continue
            if session.scalar(select(GpuOccupancy.attempt_id).where(GpuOccupancy.attempt_id == source.id)) is not None:
                continue
            job.state = transition("retry_wait", "queued", "retry_due", {"old_attempt_closed", "retry_budget_available"})
            job.retry_after = None
            job.revision += 1
            record_change(session, "runner", "job.retry_due", job.id, str(uuid4()), {"count": job.retry_count})
            promoted += 1
    return promoted
