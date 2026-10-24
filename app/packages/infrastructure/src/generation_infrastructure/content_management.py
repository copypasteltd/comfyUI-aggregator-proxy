from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from generation_contracts.content_management import ContentOptionQuery, ContentQuery
from generation_contracts.queue_queries import QueueOptionQuery
from generation_domain.values import parse_id, public_id
from sqlalchemy import ColumnElement, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from generation_infrastructure.models import (
    ApiKey,
    Artifact,
    Asset,
    Job,
    JobAssetReference,
    ObjectRecord,
    WorkflowRelease,
)
from generation_infrastructure.queue_queries import filter_options

PREVIEW_MIME = frozenset({"image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm", "audio/wav", "audio/flac", "audio/mpeg", "audio/ogg"})


def status_expression(entity: type[Asset] | type[Artifact], now: datetime) -> ColumnElement[str]:
    return case((ObjectRecord.deleted_at.is_not(None), literal("reclaimed")), (or_(entity.deleted_at.is_not(None), ObjectRecord.delete_requested_at.is_not(None)), literal("pending_reclaim")), (ObjectRecord.expires_at <= now, literal("expired")), (or_(ObjectRecord.verified_at.is_(None), ObjectRecord.version_id.is_(None), ObjectRecord.version_id == ""), literal("unverified")), else_=literal("available"))


def content_status(row: Asset | Artifact, obj: ObjectRecord, now: datetime) -> str:
    if obj.deleted_at:
        return "reclaimed"
    if row.deleted_at or obj.delete_requested_at:
        return "pending_reclaim"
    if obj.expires_at <= now:
        return "expired"
    if not obj.verified_at or not obj.version_id:
        return "unverified"
    return "available"


def content_filters(query: ContentQuery, entity: type[Asset] | type[Artifact], now: datetime) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    for name, prefix in (("tenant_id", "ten"), ("project_id", "prj"), ("key_id", "key")):
        value = getattr(query, name)
        if value:
            conditions.append(getattr(entity, name) == parse_id(prefix, value))
    if query.tag is not None:
        conditions.append(entity.tags.contains([query.tag]))
    if query.created_from is not None:
        conditions.append(entity.created_at >= datetime.fromtimestamp(query.created_from, UTC))
    if query.created_before is not None:
        conditions.append(entity.created_at < datetime.fromtimestamp(query.created_before, UTC))
    if query.media_kind:
        conditions.append(ObjectRecord.media_type.startswith(query.media_kind + "/"))
    if query.state:
        conditions.append(status_expression(entity, now) == query.state)
    job_conditions: list[ColumnElement[bool]] = []
    if query.job_id:
        job_conditions.append(Job.id == parse_id("job", query.job_id))
    if query.model:
        job_conditions.append(Job.model_alias == query.model)
    if query.release_id:
        job_conditions.append(Job.release_id == parse_id("wfr", query.release_id))
    if query.workflow_id:
        job_conditions.append(exists(select(WorkflowRelease.id).where(WorkflowRelease.id == Job.release_id, WorkflowRelease.workflow_id == parse_id("wf", query.workflow_id))))
    if job_conditions:
        matching_jobs = select(Job.id).where(*job_conditions, Job.tenant_id == entity.tenant_id, Job.project_id == entity.project_id, Job.key_id == entity.key_id)
        if entity is Artifact:
            matching_jobs = matching_jobs.where(Job.id == Artifact.job_id)
        else:
            matching_jobs = matching_jobs.join(JobAssetReference, JobAssetReference.job_id == Job.id).where(JobAssetReference.asset_id == Asset.id)
        conditions.append(exists(matching_jobs.correlate(entity)))
    return conditions


def content_options(session: Session, query: ContentOptionQuery) -> dict[str, Any]:
    if query.kind != "key":
        return filter_options(session, QueueOptionQuery.model_validate(query.model_dump()))
    statement = select(ApiKey.id, ApiKey.name).where(func.strpos(func.lower(ApiKey.name), query.q.lower()) > 0)
    for name, prefix in (("tenant_id", "ten"), ("project_id", "prj")):
        value = getattr(query, name)
        if value:
            statement = statement.where(getattr(ApiKey, name) == parse_id(prefix, value))
    rows = list(session.execute(statement.order_by(ApiKey.name, ApiKey.id).offset((query.page-1)*50).limit(51)))
    return {"data": [{"value": public_id("key", identifier), "label": name} for identifier, name in rows[:50]], "page": query.page, "has_more": len(rows) > 50}
