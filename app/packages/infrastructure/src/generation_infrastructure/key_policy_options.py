from __future__ import annotations

from typing import Any

from generation_contracts.key_policy_options import KeyPolicyOptionQuery
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import String, cast, func, literal, or_, select

from generation_infrastructure.admin_base import AdminBase
from generation_infrastructure.admin_scopes import scope_search
from generation_infrastructure.models import ApiKey, Project, Tenant
from generation_infrastructure.queue_queries import begin_queue_read


class KeyPolicyDirectory(AdminBase):
    def query(self, query: KeyPolicyOptionQuery) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            tenant = session.get(Tenant, parse_id("ten", query.tenant_id))
            if tenant is None:
                raise DomainError("not_found", "租户不存在", 404)
            now = utcnow()
            projects = query.kind == "project"
            statement = select(Project) if projects else select(Project, ApiKey).join(ApiKey, ApiKey.project_id == Project.id)
            statement = statement.where(Project.tenant_id == tenant.id)
            if not projects:
                statement = statement.where(ApiKey.tenant_id == tenant.id, ApiKey.project_id.in_([parse_id("prj", value) for value in query.project_ids]))
            if query.mode == "resolve":
                identifiers = [parse_id("prj" if projects else "key", value) for value in query.ids]
                statement = statement.where(Project.id.in_(identifiers) if projects else ApiKey.id.in_(identifiers))
            else:
                if query.q:
                    condition = scope_search(Project, "prj", query.q)
                    if not projects:
                        key_id = literal("key_") + func.replace(cast(ApiKey.id, String), "-", "")
                        condition = or_(condition, ApiKey.name.icontains(query.q, autoescape=True), key_id == query.q)
                    statement = statement.where(condition)
                statement = statement.order_by(Project.name, Project.id) if projects else statement.order_by(ApiKey.name, ApiKey.id)
                statement = statement.offset((query.page - 1) * 50).limit(51)
            rows = session.execute(statement).all()
            data = []
            for record in rows[:50] if query.mode == "browse" else rows:
                project = record[0]
                key = None if projects else record[1]
                enabled = project.enabled if key is None else key.enabled
                lifecycle = "active"
                if key is not None and key.deleted_at is not None:
                    lifecycle = "archived"
                elif key is not None and key.expires_at is not None and key.expires_at <= now:
                    lifecycle = "expired"
                elif not tenant.enabled or not project.enabled or not enabled:
                    lifecycle = "disabled"
                data.append({"id": public_id("prj", project.id) if key is None else public_id("key", key.id), "name": project.name if key is None else key.name, "tenant_id": query.tenant_id, "tenant_name": tenant.name, "project_id": public_id("prj", project.id), "project_name": project.name, "tenant_enabled": tenant.enabled, "project_enabled": project.enabled, "enabled": enabled, "lifecycle": lifecycle, "origin": None if key is None else "local" if key.issuer == "local" else "gateway"})
            if query.mode == "resolve":
                order = {value: index for index, value in enumerate(query.ids)}
                data.sort(key=lambda row: order[row["id"]])
            found = {row["id"] for row in data}
            return {"data": data, "unresolved_ids": [value for value in query.ids if value not in found], "page": query.page, "has_more": query.mode == "browse" and len(rows) > 50, "observed_at": int(now.timestamp())}
