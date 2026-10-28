from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from generation_application.ports import AdminIdentity
from generation_contracts.defaults import default_limits
from generation_contracts.scope_administration import (
    ProjectCreate,
    ScopePatch,
    ScopeQuery,
    TenantCreate,
)
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import String, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision, timestamp
from generation_infrastructure.models import Project, QuotaBucket, Tenant
from generation_infrastructure.queue_queries import begin_queue_read
from generation_infrastructure.transactions import record_change


def scope_search(model: type[Tenant] | type[Project], prefix: str, query: str) -> Any:
    identifier = literal(prefix + "_") + func.replace(cast(model.id, String), "-", "")
    return or_(model.name.icontains(query, autoescape=True), identifier == query)


def projections(session: Session, rows: Sequence[Tenant | Project], *, projects: bool, now: datetime) -> list[dict[str, Any]]:
    period = now.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
    scope_kind = "2_project" if projects else "1_tenant"
    buckets = {(bucket.scope_id, bucket.period): bucket for bucket in session.scalars(select(QuotaBucket).where(QuotaBucket.scope_kind == scope_kind, QuotaBucket.scope_id.in_([row.id for row in rows]), QuotaBucket.period.in_(("active", period))))} if rows else {}
    parents = {row.id: row for row in session.scalars(select(Tenant).where(Tenant.id.in_({row.tenant_id for row in rows if isinstance(row, Project)})))} if projects and rows else {}
    result = []
    for row in rows:
        active, daily = buckets.get((row.id, "active")), buckets.get((row.id, period))
        usage = {"period": period, **{name: getattr(active if name in ("unfinished_jobs", "generating_jobs") else daily, name, 0) for name in ("daily_jobs", "unfinished_jobs", "generating_jobs", "daily_image_count", "daily_video_milliseconds", "daily_audio_milliseconds", "daily_mesh_count")}}
        record = {"id": public_id("prj" if projects else "ten", row.id), "name": row.name, "enabled": row.enabled, "revision": row.revision, "limits": row.limits, "created_at": timestamp(row.created_at), "observed_at": int(now.timestamp()), "usage": usage}
        if isinstance(row, Project):
            tenant = parents[row.tenant_id]
            record.update({"tenant_id": public_id("ten", tenant.id), "tenant_name": tenant.name, "tenant_enabled": tenant.enabled, "tenant_limits": tenant.limits})
        result.append(record)
    return result


class ScopeAdministration(AdminBase):
    def tenants(self, query: ScopeQuery) -> dict[str, Any]:
        if query.project_id:
            raise DomainError("invalid_scope_filter", "租户目录不接受项目筛选", 422)
        with self.sessions.begin() as session:
            begin_queue_read(session)
            statement = select(Tenant)
            if query.tenant_id:
                statement = statement.where(Tenant.id == parse_id("ten", query.tenant_id))
            if query.q:
                statement = statement.where(scope_search(Tenant, "ten", query.q))
            if query.tenant_id:
                statement = statement.where(Tenant.id == parse_id("ten", query.tenant_id))
            if query.enabled is not None:
                statement = statement.where(Tenant.enabled.is_(query.enabled))
            rows = list(session.scalars(statement.order_by(Tenant.created_at.desc(), Tenant.id).offset((query.page - 1) * 50).limit(51)))
            now = utcnow()
            return {"data": projections(session, rows[:50], projects=False, now=now), "page": query.page, "has_more": len(rows) > 50, "observed_at": int(now.timestamp()), "default_limits": default_limits()}

    def projects(self, query: ScopeQuery) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            statement = select(Project).join(Tenant, Tenant.id == Project.tenant_id)
            if query.q:
                statement = statement.where(or_(scope_search(Project, "prj", query.q), scope_search(Tenant, "ten", query.q)))
            if query.tenant_id:
                statement = statement.where(Project.tenant_id == parse_id("ten", query.tenant_id))
            if query.project_id:
                statement = statement.where(Project.id == parse_id("prj", query.project_id))
            if query.enabled is not None:
                statement = statement.where(Project.enabled.is_(query.enabled))
            rows = list(session.scalars(statement.order_by(Project.created_at.desc(), Project.id).offset((query.page - 1) * 50).limit(51)))
            now = utcnow()
            return {"data": projections(session, rows[:50], projects=True, now=now), "page": query.page, "has_more": len(rows) > 50, "observed_at": int(now.timestamp()), "default_limits": default_limits()}

    def options(self, query: ScopeQuery) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            statement = select(Tenant, Project).join(Project, Project.tenant_id == Tenant.id)
            if query.project_id:
                statement = statement.where(Project.id == parse_id("prj", query.project_id))
            elif query.enabled is False:
                statement = statement.where(or_(Tenant.enabled.is_(False), Project.enabled.is_(False)))
            else:
                statement = statement.where(Tenant.enabled.is_(True), Project.enabled.is_(True))
            if query.tenant_id:
                statement = statement.where(Tenant.id == parse_id("ten", query.tenant_id))
            if query.q:
                statement = statement.where(or_(scope_search(Tenant, "ten", query.q), scope_search(Project, "prj", query.q)))
            rows = session.execute(statement.order_by(Tenant.name, Tenant.id, Project.name, Project.id).offset((query.page - 1) * 50).limit(51)).all()
            return {"data": [{"tenant_id": public_id("ten", tenant.id), "tenant_name": tenant.name, "project_id": public_id("prj", project.id), "project_name": project.name, "tenant_enabled": tenant.enabled, "project_enabled": project.enabled} for tenant, project in rows[:50]], "page": query.page, "has_more": len(rows) > 50}

    def get(self, identifier: UUID, *, project: bool) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            row = session.get(Project, identifier) if project else session.get(Tenant, identifier)
            if row is None:
                raise DomainError("not_found", "项目或租户不存在", 404)
            return projections(session, [row], projects=project, now=utcnow())[0]

    def create_tenant(self, actor: AdminIdentity, data: TenantCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "tenant.create", token, data.model_dump())
            if idem.result is not None:
                return idem.result
            row = Tenant(name=data.name, enabled=data.enabled, limits=data.limits.model_dump())
            session.add(row)
            session.flush()
            record_change(session, public_id("usr", actor.user_id), "tenant.created", row.id, request_id, {"revision": row.revision, "enabled": row.enabled}, revision_before=None, revision_after=row.revision)
            idem.result = projections(session, [row], projects=False, now=utcnow())[0]
            return idem.result

    def create_project(self, actor: AdminIdentity, data: ProjectCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "project.create", token, data.model_dump())
            if idem.result is not None:
                return idem.result
            tenant = session.get(Tenant, parse_id("ten", data.tenant_id))
            if tenant is None:
                raise DomainError("scope_not_found", "所属租户不存在", 404)
            row = Project(tenant_id=tenant.id, name=data.name, enabled=data.enabled, limits=data.limits.model_dump())
            session.add(row)
            session.flush()
            record_change(session, public_id("usr", actor.user_id), "project.created", row.id, request_id, {"tenant_id": public_id("ten", tenant.id), "revision": row.revision, "enabled": row.enabled}, revision_before=None, revision_after=row.revision)
            idem.result = projections(session, [row], projects=True, now=utcnow())[0]
            return idem.result

    def patch(self, actor: AdminIdentity, identifier: UUID, data: ScopePatch, token: str, request_id: str, *, project: bool) -> dict[str, Any]:
        kind = "project" if project else "tenant"
        with self.sessions.begin() as session:
            idem = mutation(session, actor, kind + ".patch:" + str(identifier), token, data.model_dump(exclude_none=True))
            if idem.result is not None:
                return idem.result
            row = session.scalar(select(Project).where(Project.id == identifier).with_for_update(key_share=True)) if project else session.scalar(select(Tenant).where(Tenant.id == identifier).with_for_update(key_share=True))
            if row is None:
                raise DomainError("not_found", "项目或租户不存在", 404)
            require_revision(row.revision, data.expected_revision)
            changes = data.model_dump(exclude={"expected_revision"}, exclude_none=True)
            changed = any(getattr(row, name) != value for name, value in changes.items())
            if changed:
                for name, value in changes.items():
                    setattr(row, name, value)
                row.revision += 1
                record_change(session, public_id("usr", actor.user_id), kind + ".updated", row.id, request_id, {"revision": row.revision, "enabled": row.enabled, "changed_fields": sorted(changes)}, revision_before=data.expected_revision, revision_after=row.revision)
            session.flush()
            idem.result = projections(session, [row], projects=project, now=utcnow())[0]
            return idem.result
