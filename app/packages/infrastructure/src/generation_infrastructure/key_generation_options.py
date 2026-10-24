from __future__ import annotations

from typing import Any

from generation_contracts.key_generation_options import KeyGenerationOptions, KeyGenerationQuery
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from pydantic import ValidationError
from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase
from generation_infrastructure.admin_channels import begin_channel_read
from generation_infrastructure.models import (
    Channel,
    Project,
    PublicModel,
    Workflow,
    WorkflowRelease,
)


class KeyGenerationDirectory(AdminBase):
    def query(self, query: KeyGenerationQuery) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                statement = self._statement(session, query)
                total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
                if query.mode == "browse":
                    statement = statement.offset((query.page - 1) * 50).limit(51)
                else:
                    statement = statement.limit(1001)
                rows = [dict(row) for row in session.execute(statement).mappings()]
                data = [self._option(query.kind, row) for row in (rows[:50] if query.mode == "browse" else rows)]
                if query.mode == "resolve":
                    order = {value: index for index, value in enumerate(query.ids)}
                    data.sort(key=lambda row: order[row["value"]])
                found = {row["value"] for row in data}
                return KeyGenerationOptions.model_validate({"kind": query.kind, "mode": query.mode, "project_id": query.project_id, "page": query.page, "snapshot_at": int(utcnow().timestamp()), "total": total, "has_more": query.mode == "browse" and len(rows) > 50, "data": data, "unresolved_ids": [value for value in query.ids if value not in found]}).model_dump()
        except (DBAPIError, ValidationError, TypeError, KeyError):
            raise DomainError("key_generation_directory_unavailable", "Key 授权资源目录暂不可用，请稍后重试", 503) from None

    def _statement(self, session: Session, query: KeyGenerationQuery) -> Select[Any]:
        project = parse_id("prj", query.project_id)
        if session.scalar(select(Project.id).where(Project.id == project)) is None:
            raise DomainError("not_found", "项目不存在", 404)
        statement: Select[Any]
        if query.kind == "models":
            statement = select(PublicModel.id, PublicModel.alias.label("value"), PublicModel.task_type, PublicModel.published).where(PublicModel.project_id == project)
            if query.mode == "resolve":
                statement = statement.where(PublicModel.alias.in_(query.ids))
            elif query.q:
                statement = statement.where(PublicModel.alias.icontains(query.q, autoescape=True))
            return statement.order_by(PublicModel.alias, PublicModel.id)
        if query.kind == "channels":
            statement = select(Channel.id, Channel.name, Channel.enabled, Channel.draining, Channel.deleted_at.is_not(None).label("archived")).where(Channel.project_id == project)
            if query.mode == "resolve":
                statement = statement.where(Channel.id.in_([parse_id("ch", value) for value in query.ids]))
            elif query.q:
                statement = statement.where(Channel.name.icontains(query.q, autoescape=True))
            return statement.order_by(Channel.name, Channel.id)
        statement = select(WorkflowRelease.id, WorkflowRelease.workflow_id, Workflow.name, Workflow.task_type, WorkflowRelease.version, WorkflowRelease.validated, Workflow.archived).join(Workflow, Workflow.id == WorkflowRelease.workflow_id).where(WorkflowRelease.project_id == project, Workflow.project_id == project)
        if query.mode == "resolve":
            statement = statement.where(WorkflowRelease.id.in_([parse_id("wfr", value) for value in query.ids]))
        elif query.q:
            statement = statement.where(or_(Workflow.name.icontains(query.q, autoescape=True), cast(WorkflowRelease.version, String).icontains(query.q, autoescape=True)))
        return statement.order_by(Workflow.name, WorkflowRelease.version.desc(), WorkflowRelease.id)

    @staticmethod
    def _option(kind: str, row: dict[str, Any]) -> dict[str, Any]:
        identifier = row.pop("id")
        if kind == "models":
            row["id"] = public_id("mdl", identifier)
        else:
            row["value"] = public_id("ch" if kind == "channels" else "wfr", identifier)
        if kind == "workflow_releases":
            row["workflow_id"] = public_id("wf", row["workflow_id"])
        return {"kind": kind, **row}
