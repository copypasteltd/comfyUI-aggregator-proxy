from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from generation_contracts.queue_queries import QueueOptionQuery, QueueQuery
from generation_domain.state import TERMINAL
from generation_domain.values import parse_id, public_id
from sqlalchemy import Select, and_, exists, func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from generation_infrastructure.admin_base import timestamp
from generation_infrastructure.models import (
    Attempt,
    Channel,
    Job,
    Project,
    PublicModel,
    Tenant,
    Workflow,
    WorkflowRelease,
)


def begin_queue_read(session: Session) -> None:
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
    session.execute(text("SET LOCAL lock_timeout = '1000ms'"))


def job_filters(query: QueueQuery) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    for name, prefix in (("tenant_id", "ten"), ("project_id", "prj"), ("release_id", "wfr"), ("key_id", "key")):
        value = getattr(query, name)
        if value is not None:
            conditions.append(getattr(Job, name) == parse_id(prefix, value))
    if query.model is not None:
        conditions.append(Job.model_alias == query.model)
    if query.state is not None:
        conditions.append(Job.state == query.state)
    if query.workflow_id:
        conditions.append(exists(select(WorkflowRelease.id).where(WorkflowRelease.id == Job.release_id, WorkflowRelease.workflow_id == parse_id("wf", query.workflow_id))))
    if query.channel_id:
        conditions.append(or_(and_(Job.state.in_(("queued", "retry_wait")), Job.current_attempt_id.is_(None), Job.eligible_channels.contains([query.channel_id])), exists(select(Attempt.id).where(Attempt.id == Job.current_attempt_id, Attempt.channel_id == parse_id("ch", query.channel_id)).correlate(Job))))
    if query.created_from is not None:
        conditions.append(Job.created_at >= datetime.fromtimestamp(query.created_from, UTC))
    if query.created_before is not None:
        conditions.append(Job.created_at < datetime.fromtimestamp(query.created_before, UTC))
    return conditions


def channel_filters(query: QueueQuery) -> list[ColumnElement[bool]]:
    result: list[ColumnElement[bool]] = [Channel.deleted_at.is_(None)]
    for name, prefix in (("tenant_id", "ten"), ("project_id", "prj")):
        value = getattr(query, name)
        if value:
            result.append(getattr(Channel, name) == parse_id(prefix, value))
    if query.channel_id:
        result.append(Channel.id == parse_id("ch", query.channel_id))
    return result


def waiting_times(job: Job, first_assigned: datetime | None, now: datetime) -> dict[str, Any]:
    accepted = job.accepted_at
    end = first_assigned if first_assigned is not None else job.terminal_at if job.state in TERMINAL else now
    valid = (job.current_attempt_id is None or first_assigned is not None) and job.state != "admission_pending" and accepted is not None and end is not None and accepted <= end <= now
    if first_assigned is not None and job.terminal_at is not None and first_assigned > job.terminal_at:
        valid = False
    return {"release_id": public_id("wfr", job.release_id), "accepted_at": timestamp(accepted), "first_assigned_at": timestamp(first_assigned), "queue_wait_seconds": int((end - accepted).total_seconds()) if valid and end is not None and accepted is not None else None}


def filter_options(session: Session, query: QueueOptionQuery) -> dict[str, Any]:
    """Only value/label metadata, never serialize source entities."""
    statement: Select[Any]
    prefix: str | None
    entity: Any
    label: Any
    if query.kind == "tenant":
        entity, prefix, label = Tenant, "ten", Tenant.name
    elif query.kind == "project":
        entity, prefix, label = Project, "prj", Project.name
    elif query.kind == "workflow":
        entity, prefix, label = Workflow, "wf", Workflow.name
    elif query.kind == "channel":
        entity, prefix, label = Channel, "ch", Channel.name
    elif query.kind == "release":
        entity, prefix, label = WorkflowRelease, "wfr", func.concat(Workflow.name, " · v", WorkflowRelease.version)
    else:
        # Preserve aliases on historical jobs even after model publication changes.
        statements = []
        for model_entity, alias in ((PublicModel, PublicModel.alias), (Job, Job.model_alias)):
            part = select(alias.label("alias"))
            for name, id_prefix in (("tenant_id", "ten"), ("project_id", "prj")):
                value = getattr(query, name)
                if value:
                    part = part.where(getattr(model_entity, name) == parse_id(id_prefix, value))
            if query.workflow_id:
                part = part.where(exists(select(WorkflowRelease.id).where(WorkflowRelease.id == model_entity.release_id, WorkflowRelease.workflow_id == parse_id("wf", query.workflow_id))))
            statements.append(part)
        aliases = statements[0].union(statements[1]).subquery()
        statement = select(aliases.c.alias, aliases.c.alias).where(func.strpos(func.lower(aliases.c.alias), query.q.lower()) > 0).order_by(aliases.c.alias)
        rows = session.execute(statement.offset((query.page - 1) * 50).limit(51)).all()
        return {"data": [{"value": value, "label": name} for value, name in rows[:50]], "page": query.page, "has_more": len(rows) > 50}
    statement = select(entity.id, label)
    if query.kind == "release":
        statement = statement.join(Workflow, Workflow.id == WorkflowRelease.workflow_id)
        if query.workflow_id:
            statement = statement.where(WorkflowRelease.workflow_id == parse_id("wf", query.workflow_id))
    for name, id_prefix in (("tenant_id", "ten"), ("project_id", "prj")):
        value = getattr(query, name)
        if value and hasattr(entity, name):
            statement = statement.where(getattr(entity, name) == parse_id(id_prefix, value))
    if query.kind == "channel":
        statement = statement.where(Channel.deleted_at.is_(None))
    statement = statement.where(func.strpos(func.lower(label), query.q.lower()) > 0).order_by(label, entity.id).offset((query.page - 1) * 50).limit(51)
    rows = session.execute(statement).all()
    assert prefix is not None
    return {"data": [{"value": public_id(prefix, identifier), "label": name} for identifier, name in rows[:50]], "page": query.page, "has_more": len(rows) > 50}
