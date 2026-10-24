from __future__ import annotations

from datetime import timedelta
from typing import Final
from uuid import uuid4

from generation_contracts.audit_retention import AuditRetentionBatch, AuditRetentionView
from generation_domain.errors import DomainError
from pydantic import ValidationError
from sqlalchemy import Text, case, cast, delete, func, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.models import AuditEvent
from generation_infrastructure.transactions import record_change

LOCK = 624087063
BATCH_LIMIT: Final = 500
ACTION = "audit.retention_pruned"


def prune_audit_events(sessions: sessionmaker[Session], retention_days: int | None = None) -> int:
    if retention_days is None:
        return 0
    if type(retention_days) is not int or not 1 <= retention_days <= 3650:
        raise ValueError("Audit retention requires 1..3650 explicit days")
    with sessions.begin() as session:
        session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        session.execute(text("SET LOCAL lock_timeout = '250ms'"))
        if not session.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
            return 0
        now = session.scalar(select(func.clock_timestamp()))
        assert now is not None
        cutoff = now - timedelta(days=retention_days)
        # Freeze the bounded set before DELETE; a locking LIMIT subquery may be rescanned.
        candidates = list(session.scalars(select(AuditEvent.id).where(AuditEvent.created_at < cutoff).order_by(AuditEvent.created_at, AuditEvent.id).limit(BATCH_LIMIT).with_for_update(skip_locked=True)))
        if not candidates:
            return 0
        removed = list(session.scalars(delete(AuditEvent).where(AuditEvent.id.in_(candidates)).returning(AuditEvent.id)))
        if removed:
            record_change(session, "runner", ACTION, uuid4(), str(uuid4()), {"deleted": len(removed), "cutoff": int(cutoff.timestamp()), "retention_days": retention_days})
        return len(removed)


def retention_snapshot(sessions: sessionmaker[Session], retention_days: int | None) -> AuditRetentionView:
    try:
        with sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            now = session.scalar(select(func.clock_timestamp()))
            assert now is not None
            bounded = case((func.octet_length(cast(AuditEvent.detail, Text)) <= 1024, AuditEvent.detail), else_=literal(None, type_=JSONB))
            row = session.execute(select(AuditEvent.created_at, bounded).where(AuditEvent.actor == "runner", AuditEvent.action == ACTION).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1)).one_or_none()
            batch = AuditRetentionBatch.model_validate({**row[1], "completed_at": int(row[0].timestamp())}) if row and isinstance(row[1], dict) else None
            if row and batch is None:
                raise DomainError("audit_retention_unavailable", "审计保留状态暂不可用", 503)
            return AuditRetentionView(enabled=retention_days is not None, retention_days=retention_days, batch_limit=BATCH_LIMIT, snapshot_at=int(now.timestamp()), last_batch=batch)
    except (DBAPIError, ValidationError):
        raise DomainError("audit_retention_unavailable", "审计保留状态暂不可用", 503) from None
