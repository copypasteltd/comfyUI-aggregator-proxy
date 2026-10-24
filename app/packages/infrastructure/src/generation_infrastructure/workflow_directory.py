from __future__ import annotations

from typing import Any

from generation_contracts.workflow_directory import (
    WorkflowDirectoryPage,
    WorkflowDirectoryQuery,
    WorkflowSyncOptions,
    WorkflowSyncOptionsQuery,
)
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from pydantic import ValidationError
from sqlalchemy import Select, String, cast, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase
from generation_infrastructure.admin_channels import begin_channel_read
from generation_infrastructure.models import Channel, Workflow, WorkflowRelease


def workflow_rows(query: WorkflowDirectoryQuery) -> Select[Any]:
    statement = select(Workflow.id, Workflow.tenant_id, Workflow.project_id, Workflow.name, Workflow.task_type, Workflow.revision, Workflow.archived, Workflow.created_at)
    if query.project_id:
        statement = statement.where(Workflow.project_id == parse_id("prj", query.project_id))
    if query.workflow_id:
        statement = statement.where(Workflow.id == parse_id("wf", query.workflow_id))
    if query.task_type:
        statement = statement.where(Workflow.task_type == query.task_type)
    if query.status:
        statement = statement.where(Workflow.archived.is_(query.status == "archived"))
    if query.q:
        statement = statement.where(Workflow.name.icontains(query.q, autoescape=True))
    return statement.order_by(Workflow.created_at.desc(), Workflow.id)


class WorkflowDirectory(AdminBase):
    def catalog(self, query: WorkflowDirectoryQuery) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                statement = workflow_rows(query)
                total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
                rows = session.execute(statement.offset((query.page - 1) * 50).limit(51)).mappings().all()
                data = []
                for raw in rows[:50]:
                    row = dict(raw)
                    for name, prefix in [("id", "wf"), ("tenant_id", "ten"), ("project_id", "prj")]:
                        row[name] = public_id(prefix, row[name])
                    row["created_at"] = int(row["created_at"].timestamp())
                    data.append(row)
                return WorkflowDirectoryPage.model_validate({"data": data, "page": query.page, "has_more": len(rows) > 50, "total": total, "snapshot_at": int(utcnow().timestamp()), "filters": query.model_dump(exclude_none=True, exclude={"page"})}).model_dump()
        except (DBAPIError, ValidationError, TypeError, KeyError):
            raise DomainError("workflow_directory_unavailable", "Workflow 目录暂不可用，请稍后重试", 503) from None

    def options(self, query: WorkflowSyncOptionsQuery) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                statement = self._options(session, query)
                total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
                rows = session.execute(statement.offset((query.page - 1) * 50).limit(51)).mappings().all()
                data = [{**dict(row), "kind": query.kind, "id": public_id("wfr" if query.kind == "release" else "ch", row["id"])} for row in rows[:50]]
                return WorkflowSyncOptions.model_validate({"kind": query.kind, "project_id": query.project_id, "workflow_id": query.workflow_id, "page": query.page, "snapshot_at": int(utcnow().timestamp()), "total": total, "has_more": len(rows) > 50, "data": data}).model_dump()
        except (DBAPIError, ValidationError, TypeError, KeyError):
            raise DomainError("workflow_directory_unavailable", "Workflow 目录暂不可用，请稍后重试", 503) from None

    def _options(self, session: Session, query: WorkflowSyncOptionsQuery) -> Select[Any]:
        project, workflow = parse_id("prj", query.project_id), parse_id("wf", query.workflow_id)
        parent = session.scalar(select(Workflow.archived).where(Workflow.id == workflow, Workflow.project_id == project))
        if parent is None:
            raise DomainError("not_found", "同项目 Workflow 不存在", 404)
        if parent:
            raise DomainError("workflow_archived", "Workflow 已归档，请先解除归档", 409)
        statement: Select[Any]
        if query.kind == "release":
            statement = select(WorkflowRelease.id, WorkflowRelease.version, WorkflowRelease.digest, WorkflowRelease.validated).where(WorkflowRelease.workflow_id == workflow, WorkflowRelease.project_id == project)
            if query.ids:
                statement = statement.where(WorkflowRelease.id.in_([parse_id("wfr", value) for value in query.ids]))
            else:
                statement = statement.where(WorkflowRelease.validated.is_(True))
            if query.q:
                statement = statement.where(cast(WorkflowRelease.version, String).icontains(query.q, autoescape=True))
            return statement.order_by(WorkflowRelease.version.desc(), WorkflowRelease.id)
        statement = select(Channel.id, Channel.name, Channel.revision, Channel.enabled, Channel.draining).where(Channel.project_id == project, Channel.deleted_at.is_(None))
        if query.ids:
            statement = statement.where(Channel.id.in_([parse_id("ch", value) for value in query.ids]))
        if query.q:
            statement = statement.where(Channel.name.icontains(query.q, autoescape=True))
        return statement.order_by(Channel.created_at.desc(), Channel.id)
