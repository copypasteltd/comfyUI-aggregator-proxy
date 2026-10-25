from __future__ import annotations

import hashlib
import math
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity, BusinessIdentity, GatewayIdentity
from generation_contracts.admin_sessions import SessionLifetime
from generation_domain.errors import DomainError
from generation_domain.policies import limit_value
from generation_domain.values import public_id, utcnow
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.database_diagnostics import database_operation
from generation_infrastructure.models import (
    AdminSession,
    AdminUser,
    ApiCredential,
    ApiKey,
    GatewayService,
    Node,
    Project,
    RateState,
    Tenant,
)
from generation_infrastructure.security import (
    check_ip,
    issue_secret,
    password_hash,
    secret_identifier,
    verifier,
    verify_password,
    verify_secret,
)
from generation_infrastructure.settings import Settings
from generation_infrastructure.transactions import record_change


def consume_rate(session: Session, identifier: str, rpm: int | None, burst: int | None) -> None:
    if rpm is None and burst is None:
        return
    capacity = burst if burst is not None else rpm
    assert capacity is not None
    refill = Decimal(rpm if rpm is not None else capacity) / Decimal(60)
    session.execute(insert(RateState).values(identity=identifier, tokens=capacity, updated_at=func.clock_timestamp(), recent=[]).on_conflict_do_nothing())
    row = session.execute(select(RateState).where(RateState.identity == identifier).with_for_update().execution_options(populate_existing=True)).scalar_one()
    now = session.scalar(select(func.clock_timestamp()))
    assert now is not None
    tokens = min(Decimal(capacity), row.tokens + Decimal(str((now - row.updated_at).total_seconds())) * refill)
    milliseconds = int(now.timestamp() * 1000)
    recent = [v for v in row.recent if v > milliseconds - 60000]
    if tokens < 1 or (rpm is not None and len(recent) >= rpm):
        raise DomainError("rate_limited", "请求过于频繁", 429)
    row.tokens = tokens - 1
    row.updated_at = now
    row.recent = [*recent, milliseconds]


def consume_business_rates(session: Session, tenant: Tenant, project: Project, key: ApiKey) -> None:
    for prefix, identifier, limits in (("tenant", tenant.id, tenant.limits), ("project", project.id, project.limits), ("business", key.id, key.policy["limits"])):
        consume_rate(session, prefix + ":" + str(identifier), limit_value(limits, "rpm"), limit_value(limits, "burst"))


def admin_session_deadline(row: AdminSession, settings: Settings) -> datetime:
    return min(row.expires_at, (row.last_activity_at or row.created_at) + timedelta(seconds=settings.session_idle_seconds))


def admin_session_active(row: AdminSession, settings: Settings, now: datetime) -> bool:
    activity = row.last_activity_at or row.created_at
    return row.revoked_at is None and activity <= now < admin_session_deadline(row, settings)


class AuthRepository:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings
        self.pepper = settings.credential_pepper.get_secret_value()
        self._dummy = password_hash(secrets.token_urlsafe(32))

    def gateway(self, token: str, peer: str) -> GatewayIdentity:
        identifier = secret_identifier("gateway", token)
        with database_operation("gateway_auth"), self.sessions.begin() as session:
            row = session.scalar(select(GatewayService).where(GatewayService.credential_identifier == identifier))
            if not row or not row.enabled or row.pepper_version != self.settings.credential_pepper_version or not verify_secret(token, row.credential_verifier, self.pepper):
                raise DomainError("invalid_credentials", "网关服务凭证无效", 401)
            check_ip({"mode": "allowlist", "values": row.allowed_cidrs}, peer)
            consume_rate(session, "gateway:" + str(row.id), 1200, 100)
            return GatewayIdentity(row.id, row.issuer)

    def login(self, username: str, password: str, peer: str, request_id: str) -> dict[str, Any]:
        # Rate accounting commits even when authentication fails.
        with self.sessions.begin() as session:
            consume_rate(session, "login:" + hashlib.sha256(peer.encode()).hexdigest(), 20, 5)
        with self.sessions.begin() as session:
            user = session.scalar(select(AdminUser).where(AdminUser.username == username.strip().casefold()))
            valid = verify_password(user.password_hash if user else self._dummy, password)
            if not user or not valid or not user.enabled:
                raise DomainError("invalid_credentials", "用户名或密码错误", 401)
            identifier, token = issue_secret("admin")
            csrf = verifier("csrf:" + token, self.pepper)
            now = utcnow()
            row = AdminSession(identifier=identifier, user_id=user.id, verifier=verifier(token, self.pepper), csrf_verifier=verifier(csrf, self.pepper), user_revision=user.revision, pepper_version=self.settings.credential_pepper_version, created_at=now, last_activity_at=now, expires_at=now + timedelta(seconds=self.settings.session_seconds))
            session.add(row)
            session.flush()
            record_change(session, public_id("usr", user.id), "admin.login", row.id, request_id)
            return {"token": token, "csrf_token": csrf, "user": {"id": public_id("usr", user.id), "username": user.username, "role": user.role}, "lifetime": self._lifetime(row, now).model_dump()}

    def _lookup_admin(self, session: Session, token: str, *, write: bool, origin: str | None, csrf: str | None, lock: bool = False) -> tuple[AdminSession, AdminUser, datetime]:
        identifier = secret_identifier("admin", token)
        statement = select(AdminSession).where(AdminSession.identifier == identifier)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        row = session.scalar(statement)
        # Read user state after acquiring the session lock. Do not lock the user:
        # account changes lock users before revoking their sessions.
        user = session.get(AdminUser, row.user_id, populate_existing=True) if row else None
        now = utcnow()
        if not row or not user or not admin_session_active(row, self.settings, now) or not user.enabled or row.user_revision != user.revision or row.pepper_version != self.settings.credential_pepper_version or not verify_secret(token, row.verifier, self.pepper):
            raise DomainError("invalid_credentials", "管理会话已失效", 401)
        if write and (origin != self.settings.public_origin or not csrf or not verify_secret(csrf, row.csrf_verifier, self.pepper)):
            raise DomainError("csrf_denied", "管理操作来源或 CSRF 校验失败", 403)
        return row, user, now

    def _lifetime(self, row: AdminSession, now: datetime) -> SessionLifetime:
        return SessionLifetime(absolute_expires_at=math.ceil(row.expires_at.timestamp()), idle_expires_at=math.ceil(admin_session_deadline(row, self.settings).timestamp()), server_time=int(now.timestamp()), idle_timeout_seconds=self.settings.session_idle_seconds)

    def admin(self, token: str, *, write: bool, origin: str | None, csrf: str | None) -> AdminIdentity:
        with self.sessions() as session:
            row, user, _ = self._lookup_admin(session, token, write=write, origin=origin, csrf=csrf)
            return AdminIdentity(user.id, user.username, user.role, row.id)

    def snapshot(self, token: str) -> dict[str, Any]:
        with self.sessions() as session:
            row, user, now = self._lookup_admin(session, token, write=False, origin=None, csrf=None)
            return {"user": {"id": public_id("usr", user.id), "username": user.username, "role": user.role}, "csrf_token": verifier("csrf:" + token, self.pepper), "lifetime": self._lifetime(row, now).model_dump()}

    def activity(self, token: str, *, origin: str | None, csrf: str | None) -> SessionLifetime:
        with self.sessions.begin() as session:
            row, _, now = self._lookup_admin(session, token, write=True, origin=origin, csrf=csrf, lock=True)
            row.last_activity_at = now
            return self._lifetime(row, now)

    def logout(self, identity: AdminIdentity, request_id: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(AdminSession, identity.session_id)
            if row:
                row.revoked_at = utcnow()
                record_change(session, public_id("usr", identity.user_id), "admin.logout", row.id, request_id)

    def business(self, token: str, peer: str, *, verified: Callable[[BusinessIdentity], None] | None = None) -> BusinessIdentity:
        identifier = secret_identifier("sk-gen", token)
        with self.sessions.begin() as session:
            result = session.execute(select(ApiCredential, ApiKey).join(ApiKey, ApiCredential.key_id == ApiKey.id).where(ApiCredential.identifier == identifier, ApiKey.issuer == "local")).one_or_none()
            if not result:
                raise DomainError("invalid_credentials", "API Key 无效", 401)
            cred, key = result
            if cred.pepper_version != self.settings.credential_pepper_version or not verify_secret(token, cred.verifier, self.pepper):
                raise DomainError("invalid_credentials", "API Key 无效", 401)
            identity = BusinessIdentity(key.tenant_id, key.project_id, key.id, key.policy)
            if verified:
                verified(identity)
            tenant, project = session.get(Tenant, key.tenant_id), session.get(Project, key.project_id)
            if not tenant or not project or not tenant.enabled or not project.enabled:
                raise DomainError("invalid_credentials", "API Key 无效", 401)
            now = utcnow()
            if cred.revoked_at or (cred.expires_at and cred.expires_at <= now) or not key.enabled or key.deleted_at or (key.expires_at and key.expires_at <= now):
                raise DomainError("invalid_credentials", "API Key 无效", 401)
            check_ip(key.policy["allowed_cidrs"], peer)
            consume_business_rates(session, tenant, project, key)
            return identity

    def worker(self, token: str) -> UUID:
        identifier = secret_identifier("worker", token)
        with self.sessions() as session:
            node = session.scalar(select(Node).where(Node.credential_identifier == identifier))
            if not node or not node.enabled or node.pepper_version != self.settings.credential_pepper_version or not verify_secret(token, node.credential_verifier, self.pepper):
                raise DomainError("invalid_credentials", "Worker 凭证无效", 401)
            return node.id
