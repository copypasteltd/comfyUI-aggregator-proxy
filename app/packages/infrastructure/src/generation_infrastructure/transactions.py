from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_domain.values import public_id
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from generation_infrastructure.models import AuditEvent, OutboxEvent, QuotaBucket

GLOBAL_ID = UUID(int=0)


def lock_buckets(session: Session, tenant: UUID, project: UUID, key: UUID, period: str) -> dict[tuple[str, str], QuotaBucket]:
    return lock_bucket_periods(session, tenant, project, key, [period])


def lock_bucket_periods(session: Session, tenant: UUID, project: UUID, key: UUID, periods: list[str]) -> dict[tuple[str, str], QuotaBucket]:
    scopes = [("0_global", GLOBAL_ID), ("1_tenant", tenant), ("2_project", project), ("3_key", key)]
    days = sorted({"active", *periods})
    # Both statements visit the same global -> tenant -> project -> key order.
    # Batch round trips without weakening row locks or the fresh-counter read.
    session.execute(insert(QuotaBucket).values([
        {"scope_kind": kind, "scope_id": identifier, "period": day, "daily_jobs": 0, "unfinished_jobs": 0, "generating_jobs": 0, "daily_image_count": 0, "daily_video_milliseconds": 0, "daily_audio_milliseconds": 0, "daily_mesh_count": 0}
        for kind, identifier in scopes for day in days
    ]).on_conflict_do_nothing())
    buckets = list(session.scalars(select(QuotaBucket)
        .where(tuple_(QuotaBucket.scope_kind, QuotaBucket.scope_id).in_(scopes), QuotaBucket.period.in_(days))
        .order_by(QuotaBucket.scope_kind, QuotaBucket.scope_id, QuotaBucket.period)
        .with_for_update().execution_options(populate_existing=True)))
    if len(buckets) != len(scopes) * len(days):
        raise RuntimeError("quota_bucket_lock_incomplete")
    return {(bucket.scope_kind, bucket.period): bucket for bucket in buckets}


def record_change(session: Session, actor: str, action: str, identifier: UUID, request_id: str, detail: dict[str, Any] | None = None, *, revision_before: int | None = None, revision_after: int | None = None, operation_id: UUID | None = None) -> None:
    recorded = dict(detail or {})
    if revision_after is not None:
        recorded.update(revision_before=revision_before, revision_after=revision_after)
    if operation_id is not None:
        recorded["operation_id"] = public_id("op", operation_id)
    session.add(AuditEvent(actor=actor, action=action, resource=str(identifier), request_id=request_id, detail=recorded))
    session.add(OutboxEvent(aggregate_id=identifier, payload={"reason": action}))
