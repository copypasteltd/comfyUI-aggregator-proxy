from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, utcnow
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.models import AdminMutation, Project
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings


def require_revision(actual: int, expected: int) -> None:
    if actual != expected:
        raise DomainError("config_revision_conflict", "配置已由其他操作更新，请刷新后重试", 409)


def timestamp(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value else None


def expiration(value: int | None) -> datetime | None:
    result = datetime.fromtimestamp(value, UTC) if value is not None else None
    if result and result <= utcnow():
        raise DomainError("expiry_in_past", "过期时间必须晚于当前时间", 422)
    return result


def configured_url(value: str) -> str:
    url = urlparse(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise DomainError("invalid_channel_url", "请输入不含账号、查询参数或路径的 ComfyUI HTTP(S) 地址", 422)
    if url.hostname in ("169.254.169.254", "metadata.google.internal"):
        raise DomainError("invalid_channel_url", "不允许云元数据地址", 422)
    return value.rstrip("/")


def scope_ids(session: Session, tenant: str, project: str) -> tuple[UUID, UUID]:
    tenant_id, project_id = parse_id("ten", tenant), parse_id("prj", project)
    if session.scalar(select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)) is None:
        raise DomainError("scope_not_found", "租户或项目不存在", 404)
    return tenant_id, project_id


def mutation(session: Session, actor: AdminIdentity, operation: str, token: str, request: dict[str, Any]) -> AdminMutation:
    if not 1 <= len(token) <= 128:
        raise DomainError("idempotency_required", "管理操作必须提供 Idempotency-Key", 400)
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    digest = fingerprint(request)
    session.execute(insert(AdminMutation).values(actor_id=actor.user_id, operation=operation, token_digest=token_digest, request_digest=digest).on_conflict_do_nothing())
    row = session.execute(select(AdminMutation).where(AdminMutation.actor_id == actor.user_id, AdminMutation.operation == operation, AdminMutation.token_digest == token_digest).with_for_update()).scalar_one()
    if row.request_digest != digest:
        raise DomainError("idempotency_conflict", "同一管理幂等键对应不同请求")
    return row


class AdminBase:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings
        self.vault = SecretVault(settings.encryption_key.get_secret_value())
