from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.admin import AdminUserCreate, AdminUserPatch
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision, timestamp
from generation_infrastructure.auth import admin_session_active
from generation_infrastructure.models import AdminSession, AdminUser
from generation_infrastructure.security import password_hash, verifier
from generation_infrastructure.transactions import record_change


def user_projection(user: AdminUser) -> dict[str, Any]:
    return {"id": public_id("usr", user.id), "username": user.username, "role": user.role, "enabled": user.enabled, "revision": user.revision, "created_at": timestamp(user.created_at)}


class UserAdministration(AdminBase):
    def list(self, page: int = 1) -> dict[str, Any]:
        with self.sessions() as session:
            rows = list(session.scalars(select(AdminUser).order_by(AdminUser.created_at, AdminUser.id).offset((page - 1) * 50).limit(51)))
            return {"data": [user_projection(row) for row in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def _lock(self, session: Session, actor: AdminIdentity) -> None:
        # A stable transaction lock serializes the last-administrator invariant.
        session.execute(text("SELECT pg_advisory_xact_lock(624087029)"))
        current = session.get(AdminUser, actor.user_id)
        login = session.get(AdminSession, actor.session_id)
        if not current or not current.enabled or current.role != "administrator" or not login or not admin_session_active(login, self.settings, utcnow()) or login.user_revision != current.revision:
            raise DomainError("invalid_credentials", "管理会话已失效", 401)

    def create(self, actor: AdminIdentity, data: AdminUserCreate, token: str, request_id: str) -> dict[str, Any]:
        password = data.password.get_secret_value()
        digest = {"username": data.username, "role": data.role, "password_hmac": verifier("admin-user-password:" + password, self.settings.credential_pepper.get_secret_value())}
        encoded = password_hash(password)
        with self.sessions.begin() as session:
            self._lock(session, actor)
            idem = mutation(session, actor, "admin_user.create", token, digest)
            if idem.result is not None:
                return idem.result
            if session.scalar(select(AdminUser.id).where(func.lower(AdminUser.username) == data.username)):
                raise DomainError("username_exists", "用户名已存在", 409)
            user = AdminUser(username=data.username, password_hash=encoded, role=data.role)
            session.add(user)
            session.flush()
            record_change(session, public_id("usr", actor.user_id), "admin_user.created", user.id, request_id, {"role": user.role}, revision_before=None, revision_after=user.revision)
            idem.result = user_projection(user)
            return idem.result

    def patch(self, actor: AdminIdentity, identifier: UUID, data: AdminUserPatch, token: str, request_id: str) -> dict[str, Any]:
        body = data.model_dump(mode="json", exclude={"password"}, exclude_none=True)
        password = data.password.get_secret_value() if data.password else None
        if password is not None:
            body["password_hmac"] = verifier("admin-user-password:" + password, self.settings.credential_pepper.get_secret_value())
        encoded = password_hash(password) if password is not None else None
        with self.sessions.begin() as session:
            self._lock(session, actor)
            idem = mutation(session, actor, "admin_user.patch:" + str(identifier), token, body)
            if idem.result is not None:
                return idem.result
            # NO KEY UPDATE is compatible with AdminMutation's actor foreign key.
            user = session.scalar(select(AdminUser).where(AdminUser.id == identifier).with_for_update(key_share=True))
            if not user:
                raise DomainError("not_found", "管理账号不存在", 404)
            require_revision(user.revision, data.expected_revision)
            enabled = user.enabled if data.enabled is None else data.enabled
            role = data.role or user.role
            if user.enabled and user.role == "administrator" and (not enabled or role != "administrator"):
                remaining = session.scalar(select(func.count()).select_from(AdminUser).where(AdminUser.id != user.id, AdminUser.enabled.is_(True), AdminUser.role == "administrator"))
                if not remaining:
                    raise DomainError("last_administrator", "必须至少保留一个启用的管理员", 409)
            changed = user.enabled != enabled or user.role != role or encoded is not None
            user.enabled, user.role = enabled, role
            if encoded is not None:
                user.password_hash = encoded
            if changed:
                user.revision += 1
                # Revision invalidates sessions immediately, including a login racing this update.
                record_change(session, public_id("usr", actor.user_id), "admin_user.updated", user.id, request_id, {"role": user.role, "enabled": user.enabled, "password_changed": encoded is not None, "revision": user.revision}, revision_before=data.expected_revision, revision_after=user.revision)
            idem.result = user_projection(user)
            return idem.result
