from __future__ import annotations

from typing import Any

from generation_contracts.model_catalog import ModelMappingOptions, ModelMappingQuery
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from pydantic import ValidationError
from sqlalchemy import Select, String, cast, column, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONB
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


class ModelMappingReads(AdminBase):
    def options(self, query: ModelMappingQuery) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                project = parse_id("prj", query.project_id)
                if session.scalar(select(Project.id).where(Project.id == project)) is None:
                    raise DomainError("not_found", "项目不存在", 404)
                now = int(utcnow().timestamp())
                if query.kind == "preset":
                    statement, context = self._presets(session, query)
                else:
                    statement, context = self._directory(session, query)
                total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
                rows = list(session.execute(statement.offset((query.page - 1) * 50).limit(51)).mappings())
                data = [self._option(query, dict(row), context) for row in rows[:50]]
                return ModelMappingOptions.model_validate({"kind": query.kind, "project_id": query.project_id, "snapshot_at": now, "page": query.page, "total": total, "has_more": len(rows) > 50, "data": data}).model_dump(exclude_none=True)
        except (DBAPIError, ValidationError, TypeError, KeyError):
            raise DomainError("model_catalog_unavailable", "模型目录暂不可用，请稍后重试", 503) from None

    def _directory(self, session: Session, query: ModelMappingQuery) -> tuple[Select[Any], dict[str, Any]]:
        project = parse_id("prj", query.project_id)
        statement: Select[Any]
        if query.kind == "workflow":
            statement = select(Workflow.id, Workflow.name, Workflow.task_type, Workflow.archived).where(Workflow.project_id == project)
            if not query.ids:
                statement = statement.where(Workflow.archived.is_(False))
            if query.task_type:
                statement = statement.where(Workflow.task_type == query.task_type)
            if query.q:
                statement = statement.where(Workflow.name.icontains(query.q, autoescape=True))
            if query.ids:
                statement = statement.where(Workflow.id.in_([parse_id("wf", value) for value in query.ids]))
            return statement.order_by(Workflow.created_at.desc(), Workflow.id), {}
        if query.kind == "channel":
            statement = select(Channel.id, Channel.name, Channel.enabled, Channel.draining, Channel.revision).where(Channel.project_id == project, Channel.deleted_at.is_(None))
            if query.model_id:
                model = session.execute(select(PublicModel.channel_ids).where(PublicModel.id == parse_id("mdl", query.model_id), PublicModel.project_id == project)).one_or_none()
                if model is None:
                    raise DomainError("not_found", "模型不存在", 404)
                statement = statement.where(Channel.id.in_([parse_id("ch", value) for value in model.channel_ids]))
            if query.q:
                statement = statement.where(Channel.name.icontains(query.q, autoescape=True))
            if query.ids:
                statement = statement.where(Channel.id.in_([parse_id("ch", value) for value in query.ids]))
            return statement.order_by(Channel.created_at.desc(), Channel.id), {}
        statement = select(WorkflowRelease.id, WorkflowRelease.workflow_id, WorkflowRelease.version, WorkflowRelease.validated, Workflow.task_type).join(Workflow, Workflow.id == WorkflowRelease.workflow_id).where(WorkflowRelease.project_id == project, Workflow.project_id == project)
        if query.workflow_id:
            parent = parse_id("wf", query.workflow_id)
            if session.scalar(select(Workflow.id).where(Workflow.id == parent, Workflow.project_id == project)) is None:
                raise DomainError("not_found", "Workflow 不存在", 404)
            statement = statement.where(WorkflowRelease.workflow_id == parent)
        if not query.ids:
            statement = statement.where(WorkflowRelease.validated.is_(True), Workflow.archived.is_(False))
        if query.task_type:
            statement = statement.where(Workflow.task_type == query.task_type)
        if query.q:
            statement = statement.where(cast(WorkflowRelease.version, String).icontains(query.q, autoescape=True))
        if query.ids:
            statement = statement.where(WorkflowRelease.id.in_([parse_id("wfr", value) for value in query.ids]))
        return statement.order_by(WorkflowRelease.version.desc(), WorkflowRelease.id), {}

    def _presets(self, session: Session, query: ModelMappingQuery) -> tuple[Select[Any], dict[str, Any]]:
        release = parse_id("wfr", query.release_id or "")
        context = session.execute(select(Workflow.task_type).join(WorkflowRelease, WorkflowRelease.workflow_id == Workflow.id).where(WorkflowRelease.id == release, WorkflowRelease.project_id == parse_id("prj", query.project_id), Workflow.project_id == parse_id("prj", query.project_id), WorkflowRelease.validated.is_(True))).one_or_none()
        if context is None:
            raise DomainError("not_found", "冻结发布版本不可用", 404)
        items = func.jsonb_array_elements(WorkflowRelease.manifest["presets"]).table_valued(column("value", JSONB), with_ordinality="ordinal").render_derived(name="preset_items")
        value = items.c.value
        partition = [func.coalesce(value[key], cast(literal("null"), JSONB)) for key in ("quality", "size", "seconds")]
        fields = [value[key].label(key) for key in ("quality", "size", "seconds", "audio_format", "sample_rate", "channels")]
        statement = select(items.c.ordinal, *fields, func.count().over(partition_by=partition).label("matches")).select_from(WorkflowRelease).join(items, true()).where(WorkflowRelease.id == release)
        return statement.order_by(items.c.ordinal), {"task_type": context.task_type}

    def _option(self, query: ModelMappingQuery, row: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if query.kind != "preset":
            row["id"] = public_id({"workflow": "wf", "release": "wfr", "channel": "ch"}[query.kind], row["id"])
            if query.kind == "release":
                row["workflow_id"] = public_id("wf", row["workflow_id"])
            return {"kind": query.kind, **row}
        defaults = {"n": 1, **{name: row[name] for name in ("quality", "size", "seconds") if row[name] is not None}}
        result = {"kind": "preset", "id": "preset_" + str(row["ordinal"] - 1), "release_id": query.release_id, "task_type": context["task_type"], "defaults": defaults, "usable": row["matches"] == 1}
        if context["task_type"] == "text_to_audio":
            result["audio"] = {"format": row["audio_format"], "sample_rate": row["sample_rate"], "channels": row["channels"]}
        return result
