from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from generation_contracts.audit_queries import AuditQuery
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from sqlalchemy import Text, case, cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError

from generation_infrastructure.admin_base import AdminBase, timestamp
from generation_infrastructure.database_diagnostics import record_database_failure
from generation_infrastructure.models import AdminUser, ApiKey, AuditEvent, GatewayService, Node

DETAIL_MAX_BYTES = 16384
PUBLIC_ID = re.compile(r"[a-z][a-z0-9]{0,15}_[0-9a-f]{32}")
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
DIGEST = re.compile(r"[0-9a-f]{64}")
NUMBER_FIELDS = {"revision", "revision_before", "revision_after", "version", "target_count", "tag_count", "count", "next_at", "deleted", "cutoff", "retention_days"}
BOOL_FIELDS = {"enabled", "changed", "is_active", "password_changed"}
ID_FIELDS = {"operation_id", "channel_id", "release_id", "job_id", "validation_job_id", "retry_of", "preview_id", "tenant_id"}
CODE_FIELDS = {"error_code", "role", "observation", "source", "reason"}
CHANGE_FIELDS = {"name", "enabled", "limits", "policy", "draft", "authorization", "base_url", "role", "password", "expires_at", "channel_ids", "release_id", "defaults"}


def safe_detail(detail: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(detail, dict) or len(detail) > 64:
        return {}, True
    safe: dict[str, Any] = {}
    for name, value in detail.items():
        if name in NUMBER_FIELDS and (value is None and name == "revision_before" or type(value) is int and 0 <= value <= 2**53 - 1):
            safe[name] = value
        elif name == "priority" and type(value) is int and -100 <= value <= 100:
            safe[name] = value
        elif name in BOOL_FIELDS and type(value) is bool:
            safe[name] = value
        elif name in ID_FIELDS and isinstance(value, str) and PUBLIC_ID.fullmatch(value):
            safe[name] = value
        elif name in {"source_digest", "candidate_digest"} and isinstance(value, str) and DIGEST.fullmatch(value):
            safe[name] = value
        elif name in CODE_FIELDS and isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
            safe[name] = value
        elif name == "evidence_id" and isinstance(value, str) and TOKEN.fullmatch(value):
            safe[name] = value
        elif name == "changed_fields" and isinstance(value, list) and len(value) <= 32 and all(isinstance(field, str) and field in CHANGE_FIELDS for field in value):
            safe[name] = value
        elif name == "dispatch" and isinstance(value, dict) and len(value) <= 6:
            dispatch: dict[str, Any] = {}
            for field in ("estimated_service_milliseconds", "effective_priority", "selected_at", "sequence"):
                number = value.get(field)
                if type(number) is int and -100 <= number <= 2**53 - 1:
                    dispatch[field] = number
            if value.get("policy_id") == "tenant-service-aging-v1":
                dispatch["policy_id"] = value["policy_id"]
            if value.get("cost_source") in ("approved_profile", "image_baseline", "video_baseline", "audio_baseline"):
                dispatch["cost_source"] = value["cost_source"]
            safe[name] = dispatch
    return safe, safe != detail


class AuditQueries(AdminBase):
    def snapshot(self, query: AuditQuery) -> dict[str, Any]:
        try:
            return self._snapshot(query)
        except DBAPIError as error:
            record_database_failure("audit_snapshot", error)
            raise DomainError("audit_query_unavailable", "审计查询暂时不可用，请缩小筛选范围后重试", 503) from None

    def _snapshot(self, query: AuditQuery) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            now = utcnow()
            conditions = [AuditEvent.created_at <= now]
            for name in ("actor", "action", "request_id"):
                if (value := getattr(query, name)) is not None:
                    conditions.append(getattr(AuditEvent, name) == value)
            if resource := query.resource_uuid():
                conditions.append(AuditEvent.resource == resource)
            if query.created_from is not None:
                conditions.append(AuditEvent.created_at >= datetime.fromtimestamp(query.created_from, UTC))
            if query.created_before is not None:
                conditions.append(AuditEvent.created_at < datetime.fromtimestamp(query.created_before, UTC))
            total = session.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
            # Materialize pagination before converting JSON to text. A plain
            # LIMIT can otherwise evaluate the projection on discarded rows.
            page = select(AuditEvent.id, AuditEvent.actor, AuditEvent.action, AuditEvent.resource, AuditEvent.request_id, AuditEvent.created_at, AuditEvent.detail).where(*conditions).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).offset((query.page - 1) * 50).limit(51).cte("audit_page").prefix_with("MATERIALIZED", dialect="postgresql")
            size_ok = func.octet_length(cast(page.c.detail, Text)) <= DETAIL_MAX_BYTES
            bounded_detail = case((size_ok, page.c.detail), else_=cast(literal("{}"), JSONB)).label("bounded_detail")
            rows = list(session.execute(select(page.c.id, page.c.actor, page.c.action, page.c.resource, page.c.request_id, page.c.created_at, bounded_detail, size_ok.label("size_ok")).order_by(page.c.created_at.desc(), page.c.id.desc())))
            actors = {row.actor for row in rows[:50]}
            names = {}
            for prefix, identifier_column, label in [("usr", AdminUser.id, AdminUser.username), ("key", ApiKey.id, ApiKey.name), ("node", Node.id, Node.name), ("gateway", GatewayService.id, GatewayService.name)]:
                identifiers = [UUID(actor[len(prefix)+1:]) for actor in actors if re.fullmatch(prefix + r"_[0-9a-f]{32}", actor)]
                if identifiers:
                    for identifier, name in session.execute(select(identifier_column, label).where(identifier_column.in_(identifiers))):
                        names[public_id(prefix, identifier)] = name
            data = []
            for row in rows[:50]:
                detail, redacted = safe_detail(row.bounded_detail)
                operation = detail.get("operation_id")
                if operation is None and (row.action.startswith("operation.") or row.action == "workflow.sync_requested"):
                    try:
                        operation = public_id("op", UUID(row.resource))
                    except ValueError:
                        operation = None
                data.append({"id": public_id("audit", row.id), "actor": row.actor, "actor_name": names.get(row.actor), "action": row.action, "resource": row.resource, "request_id": row.request_id, "created_at": timestamp(row.created_at), "detail": detail, "revision_before": detail.get("revision_before"), "revision_after": detail.get("revision_after", detail.get("revision")), "operation_id": operation, "detail_redacted": not row.size_ok or redacted})
            return {"data": data, "page": query.page, "has_more": len(rows) > 50, "total": total, "snapshot_at": int(now.timestamp()), "filters": query.filters()}
