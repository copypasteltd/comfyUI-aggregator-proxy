"""Native-panel identity and durable GPU ownership. No upstream I/O in transactions."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from generation_application.ports import AdminIdentity
from generation_contracts.origin import OriginGrantPatch
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision
from generation_infrastructure.auth import AuthRepository, consume_rate
from generation_infrastructure.dispatch import DISPATCH_LOCK
from generation_infrastructure.memory_reclamation import group
from generation_infrastructure.models import (
    ApiCredential,
    ApiKey,
    Channel,
    Gpu,
    GpuOccupancy,
    Node,
    OriginFile,
    OriginGrant,
    OriginPrompt,
    OriginReservation,
    OriginSession,
    OriginUserData,
    Project,
    Tenant,
)
from generation_infrastructure.security import (
    check_ip,
    issue_secret,
    secret_identifier,
    verifier,
    verify_secret,
)
from generation_infrastructure.transactions import record_change

TRUST_NOTICE = "原生模式仅授权可信操作者，可运行设备已安装的自定义节点；产物保存在设备，不自动进入云端内容及八小时清理。"
LIVE = ("acquiring", "active", "closing", "unknown")


@dataclass(frozen=True)
class OriginActor:
    session_id: UUID
    key_id: UUID
    project_id: UUID
    tenant_id: UUID
    expires_at: datetime


def channel_slug(channel: Channel) -> str:
    match = re.search(r"\b(3080|4090|5070)\b", channel.name)
    return "rtx" + match[1] if match else "device-" + channel.id.hex[:12]


def configuration(channel: Channel, members: list[Channel]) -> str:
    return fingerprint(
        {
            "channel": str(channel.id),
            "members": [
                {
                    "id": str(row.id),
                    "gpu": str(row.gpu_id),
                    "url": row.base_url,
                    "auth": row.encrypted_auth,
                    "revision": row.revision,
                    "enabled": row.enabled,
                    "draining": row.draining,
                    "desired": row.desired_revision,
                    "applied": row.applied_revision,
                    "verified": row.verified_revision,
                }
                for row in sorted(members, key=lambda row: row.id)
            ],
        }
    )


class OriginAccess(AdminBase):
    def login(self, api_key: str, peer: str) -> dict[str, Any]:
        with self.sessions.begin() as db:
            consume_rate(db, "origin-login:" + hashlib.sha256(peer.encode()).hexdigest(), 30, 10)
        identity = AuthRepository(self.sessions, self.settings).business(api_key, peer)
        with self.sessions.begin() as db:
            grant = db.get(OriginGrant, identity.key_id)
            if not grant or not grant.enabled or not grant.channel_ids:
                raise DomainError(
                    "origin_access_denied", "此 Key 尚未开通原生面板，请联系管理员设置设备授权", 403
                )
            credential = db.scalar(
                select(ApiCredential).where(
                    ApiCredential.identifier == secret_identifier("sk-gen", api_key)
                )
            )
            assert credential
            identifier, token = issue_secret("origin")
            now = utcnow()
            row = OriginSession(
                identifier=identifier,
                verifier=verifier(token, self.settings.credential_pepper.get_secret_value()),
                credential_id=credential.id,
                expires_at=now + timedelta(seconds=self.settings.session_seconds),
                last_activity_at=now,
            )
            db.add(row)
            db.flush()
            return {
                "token": token,
                "csrf_token": self.csrf(token),
                "expires_at": int(row.expires_at.timestamp()),
                "trust_notice": TRUST_NOTICE,
            }

    def csrf(self, token: str) -> str:
        return verifier("origin-csrf:" + token, self.settings.credential_pepper.get_secret_value())

    def authenticate(
        self, token: str, peer: str, *, csrf: str | None = None, write: bool = False
    ) -> OriginActor:
        identifier = secret_identifier("origin", token)
        now = utcnow()
        with self.sessions.begin() as db:
            result = db.execute(
                select(OriginSession, ApiCredential, ApiKey)
                .join(ApiCredential, OriginSession.credential_id == ApiCredential.id)
                .join(ApiKey, ApiCredential.key_id == ApiKey.id)
                .where(OriginSession.identifier == identifier)
            ).one_or_none()
            if not result:
                raise DomainError("invalid_credentials", "原生面板会话无效，请重新登录", 401)
            row, credential, key = result
            pepper = self.settings.credential_pepper.get_secret_value()
            if (
                not verify_secret(token, row.verifier, pepper)
                or row.revoked_at
                or row.expires_at <= now
                or row.last_activity_at + timedelta(seconds=self.settings.session_idle_seconds)
                <= now
            ):
                raise DomainError("invalid_credentials", "原生面板会话已过期，请重新登录", 401)
            tenant, project = db.get(Tenant, key.tenant_id), db.get(Project, key.project_id)
            if (
                not tenant
                or not project
                or not tenant.enabled
                or not project.enabled
                or key.issuer != "local"
                or not key.enabled
                or key.deleted_at
                or credential.revoked_at
                or credential.pepper_version != self.settings.credential_pepper_version
                or (credential.expires_at and credential.expires_at <= now)
                or (key.expires_at and key.expires_at <= now)
            ):
                raise DomainError("invalid_credentials", "API Key 已失效，请重新登录", 401)
            grant = db.get(OriginGrant, key.id)
            if not grant or not grant.enabled:
                raise DomainError("origin_access_denied", "原生面板访问权限已关闭", 403)
            check_ip(key.policy["allowed_cidrs"], peer)
            if write and (not csrf or not hmac.compare_digest(self.csrf(token), csrf)):
                raise DomainError("csrf_denied", "原生操作需要有效的会话校验", 403)
            if row.last_activity_at + timedelta(seconds=30) <= now:
                row.last_activity_at = now
            return OriginActor(row.id, key.id, key.project_id, key.tenant_id, row.expires_at)

    def logout(self, actor: OriginActor) -> None:
        with self.sessions.begin() as db:
            row = db.get(OriginSession, actor.session_id)
            if row:
                row.revoked_at = utcnow()
            # Persistent holds outlive the browser. The owner may explicitly release
            # before logout; otherwise expiry triggers safe reconciliation.

    def grant_view(self, key_id: UUID) -> dict[str, Any]:
        with self.sessions() as db:
            key = db.get(ApiKey, key_id)
            if not key or key.deleted_at:
                raise DomainError("not_found", "Key 不存在", 404)
            grant = db.get(OriginGrant, key.id)
            options = list(
                db.scalars(
                    select(Channel)
                    .where(
                        Channel.tenant_id == key.tenant_id,
                        Channel.project_id == key.project_id,
                        Channel.deleted_at.is_(None),
                    )
                    .order_by(Channel.name)
                    .limit(101)
                )
            )
            return {
                "key_id": public_id("key", key.id),
                "enabled": bool(grant and grant.enabled),
                "channel_ids": grant.channel_ids if grant else [],
                "revision": grant.revision if grant else 1,
                "options": [
                    {"id": public_id("ch", row.id), "name": row.name, "enabled": row.enabled}
                    for row in options[:100]
                ],
                "trust_notice": TRUST_NOTICE,
            }

    def patch_grant(
        self,
        actor: AdminIdentity,
        key_id: UUID,
        data: OriginGrantPatch,
        token: str,
        request_id: str,
    ) -> dict[str, Any]:
        with self.sessions.begin() as db:
            idem = mutation(db, actor, "origin.grant:" + str(key_id), token, data.model_dump())
            if idem.result is not None:
                return idem.result
            key = db.execute(
                select(ApiKey).where(ApiKey.id == key_id).with_for_update()
            ).scalar_one_or_none()
            if not key or key.deleted_at or key.issuer != "local":
                raise DomainError("not_found", "本地 Key 不存在", 404)
            grant = db.get(OriginGrant, key_id)
            require_revision(grant.revision if grant else 1, data.expected_revision)
            ids = list(dict.fromkeys(data.channel_ids))
            available = set(
                db.scalars(
                    select(Channel.id).where(
                        Channel.tenant_id == key.tenant_id,
                        Channel.project_id == key.project_id,
                        Channel.deleted_at.is_(None),
                    )
                )
            )
            if any(parse_id("ch", identifier) not in available for identifier in ids) or (
                data.enabled and not ids
            ):
                raise DomainError("origin_invalid_devices", "请选择此 Key 项目内的设备", 422)
            if not grant:
                grant = OriginGrant(key_id=key_id, revision=1)
                db.add(grant)
            grant.enabled, grant.channel_ids, grant.revision = data.enabled, ids, grant.revision + 1
            record_change(db, public_id("usr", actor.user_id), "origin.grant", key.id, request_id)
            idem.result = {
                "key_id": public_id("key", key.id),
                "enabled": grant.enabled,
                "channel_ids": ids,
                "revision": grant.revision,
                "trust_notice": TRUST_NOTICE,
            }
            return idem.result

    def _channels(self, db: Any, actor: OriginActor) -> list[Channel]:
        grant, key = db.get(OriginGrant, actor.key_id), db.get(ApiKey, actor.key_id)
        if not grant or not grant.enabled or not key:
            raise DomainError("origin_access_denied", "原生访问未授权", 403)
        allowed = set(grant.channel_ids)
        if key.policy["channels"]["mode"] == "selected":
            allowed &= set(key.policy["channels"]["selected_ids"])
        return [
            row
            for row in db.scalars(
                select(Channel)
                .where(
                    Channel.tenant_id == actor.tenant_id,
                    Channel.project_id == actor.project_id,
                    Channel.deleted_at.is_(None),
                )
                .order_by(Channel.id)
                .limit(1001)
            )
            if public_id("ch", row.id) in allowed
        ]

    def target(self, actor: OriginActor, slug: str) -> dict[str, Any]:
        with self.sessions() as db:
            rows = self._channels(db, actor)
            matches = [
                row
                for row in rows
                if slug == "device-" + row.id.hex
                or (
                    channel_slug(row) == slug
                    and sum(channel_slug(other) == slug for other in rows) == 1
                )
            ]
            if len(matches) != 1:
                raise DomainError("not_found", "设备不存在或未获授权", 404)
            row = matches[0]
            if (
                not row.enabled
                or row.draining
                or row.desired_revision != row.applied_revision
                or row.desired_revision != row.verified_revision
            ):
                raise DomainError("origin_device_unavailable", "设备尚未处于可操作状态", 409)
            node = db.get(Node, row.node_id)
            if not node or not node.enabled or node.archived_at:
                raise DomainError("origin_device_unavailable", "设备节点未启用", 409)
            return {
                "id": row.id,
                "slug": slug,
                "name": row.name,
                "base_url": row.base_url,
                "authorization": self.vault.decrypt(row.encrypted_auth)
                if row.encrypted_auth
                else None,
            }

    def devices(self, actor: OriginActor) -> dict[str, Any]:
        with self.sessions.begin() as db:
            result = []
            for row in self._channels(db, actor):
                members = group(db, row)
                gpu_ids = {c.gpu_id for c in members}
                busy = bool(
                    db.scalar(
                        select(Gpu.id)
                        .where(Gpu.id.in_(gpu_ids), Gpu.management_hold.is_not(None))
                        .limit(1)
                    )
                    or db.scalar(
                        select(GpuOccupancy.gpu_id).where(GpuOccupancy.gpu_id.in_(gpu_ids)).limit(1)
                    )
                )
                own = db.scalar(
                    select(OriginReservation)
                    .where(
                        OriginReservation.key_id == actor.key_id,
                        OriginReservation.channel_id == row.id,
                        OriginReservation.state.in_(LIVE),
                    )
                    .order_by(OriginReservation.created_at.desc())
                    .limit(1)
                )
                if own and own.state == "active":
                    own.expires_at = min(
                        actor.expires_at,
                        utcnow() + timedelta(seconds=self.settings.session_idle_seconds),
                    )
                slug = channel_slug(row)
                if sum(channel_slug(other) == slug for other in self._channels(db, actor)) > 1:
                    slug = "device-" + row.id.hex
                result.append(
                    {
                        "id": public_id("ch", row.id),
                        "slug": slug,
                        "name": row.name,
                        "enabled": row.enabled and not row.draining,
                        "busy": busy,
                        "reservation_state": own.state if own else None,
                        "url": "/origin/" + slug + "/",
                        "observed_at": int(row.observed_at.timestamp())
                        if row.observed_at
                        else None,
                    }
                )
            return {"data": result, "trust_notice": TRUST_NOTICE}

    def reserve(self, actor: OriginActor, channel_id: UUID) -> dict[str, Any]:
        now = utcnow()
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DISPATCH_LOCK})
            row = next((row for row in self._channels(db, actor) if row.id == channel_id), None)
            if row is None:
                raise DomainError("not_found", "设备未授权", 404)
            node = db.get(Node, row.node_id)
            if (
                not row.enabled
                or row.draining
                or row.desired_revision != row.applied_revision
                or row.desired_revision != row.verified_revision
                or not node
                or not node.enabled
                or node.archived_at
            ):
                raise DomainError("origin_device_unavailable", "设备配置尚不可用", 409)
            members = group(db, row)
            gpu_ids = sorted({c.gpu_id for c in members})
            gpus = list(
                db.scalars(
                    select(Gpu).where(Gpu.id.in_(gpu_ids)).order_by(Gpu.id).with_for_update()
                )
            )
            own = db.scalar(
                select(OriginReservation)
                .where(
                    OriginReservation.key_id == actor.key_id,
                    OriginReservation.channel_id == row.id,
                    OriginReservation.state.in_(LIVE),
                )
                .order_by(OriginReservation.created_at.desc())
                .limit(1)
            )
            if own:
                if (
                    own.state != "active"
                    or any(g.management_hold != own.id for g in gpus)
                    or own.config_digest != configuration(row, members)
                ):
                    raise DomainError(
                        "origin_reconciliation_required", "原生操作正在核对，请稍后刷新", 409
                    )
                own.expires_at = min(
                    actor.expires_at, now + timedelta(seconds=self.settings.session_idle_seconds)
                )
                return {"id": str(own.id), "state": own.state, "reused": True}
            if any(not g.enabled or g.management_hold is not None for g in gpus) or db.scalar(
                select(GpuOccupancy.gpu_id).where(GpuOccupancy.gpu_id.in_(gpu_ids)).limit(1)
            ):
                raise DomainError(
                    "origin_device_busy", "设备正在执行或维护，原有任务不会中断，请稍后重试", 409
                )
            reservation = OriginReservation(
                key_id=actor.key_id,
                channel_id=row.id,
                config_digest=configuration(row, members),
                gpu_ids=[str(g) for g in gpu_ids],
                expires_at=min(
                    actor.expires_at, now + timedelta(seconds=self.settings.session_idle_seconds)
                ),
                updated_at=now,
            )
            db.add(reservation)
            db.flush()
            for gpu in gpus:
                gpu.management_hold = reservation.id
            return {"id": str(reservation.id), "state": "acquiring", "reused": False}

    def activate(self, reservation_id: UUID, *, empty_queue: bool) -> None:
        with self.sessions.begin() as db:
            row = db.execute(
                select(OriginReservation)
                .where(OriginReservation.id == reservation_id)
                .with_for_update()
            ).scalar_one()
            if row.state != "acquiring":
                raise DomainError("origin_reconciliation_required", "设备占用状态已变化", 409)
            row.state, row.updated_at = ("active" if empty_queue else "closing"), utcnow()
            if not empty_queue:
                row.error_code = "origin_external_queue_busy"

    def active(self, actor: OriginActor, channel_id: UUID) -> UUID:
        with self.sessions.begin() as db:
            channel = next((row for row in self._channels(db, actor) if row.id == channel_id), None)
            row = db.scalar(
                select(OriginReservation)
                .where(
                    OriginReservation.channel_id == channel_id,
                    OriginReservation.key_id == actor.key_id,
                    OriginReservation.state == "active",
                )
                .limit(1)
            )
            if (
                not row
                or not channel
                or row.expires_at <= utcnow()
                or row.config_digest != configuration(channel, group(db, channel))
            ):
                raise DomainError("origin_reservation_required", "请先在设备列表申请独占操作", 409)
            if any(
                (gpu := db.get(Gpu, UUID(identifier))) is None or gpu.management_hold != row.id
                for identifier in row.gpu_ids
            ):
                raise DomainError("origin_reconciliation_required", "设备占用需要核对", 409)
            return row.id

    def closing(self, actor: OriginActor, channel_id: UUID) -> list[UUID]:
        with self.sessions.begin() as db:
            rows = list(
                db.scalars(
                    select(OriginReservation)
                    .where(
                        OriginReservation.key_id == actor.key_id,
                        OriginReservation.channel_id == channel_id,
                        OriginReservation.state.in_(LIVE),
                    )
                    .with_for_update()
                )
            )
            for row in rows:
                row.state, row.updated_at = "closing", utcnow()
            return [row.id for row in rows]

    def prompt_intent(
        self, actor: OriginActor, channel_id: UUID, body: dict[str, Any], idempotency: str
    ) -> tuple[UUID, str, dict[str, Any] | None]:
        if not 16 <= len(idempotency) <= 128:
            raise DomainError("idempotency_required", "提交需要 Idempotency-Key", 400)
        reservation_id = self.active(actor, channel_id)
        with self.sessions.begin() as db:
            reservation = db.execute(
                select(OriginReservation)
                .where(OriginReservation.id == reservation_id)
                .with_for_update()
            ).scalar_one()
            if reservation.state != "active" or reservation.expires_at <= utcnow():
                raise DomainError("origin_reservation_required", "操作已结束或需要核对", 409)
            idem, request_digest = (
                hashlib.sha256(idempotency.encode()).hexdigest(),
                fingerprint(body),
            )
            old = db.scalar(
                select(OriginPrompt).where(
                    OriginPrompt.key_id == actor.key_id,
                    OriginPrompt.channel_id == channel_id,
                    OriginPrompt.idempotency_digest == idem,
                )
            )
            if old:
                if old.request_digest != request_digest:
                    raise DomainError("idempotency_conflict", "同一提交标识对应不同流程", 409)
                if old.response is None:
                    raise DomainError(
                        "origin_submission_unknown", "原提交结果正在核对，请勿重复运行", 409
                    )
                return old.id, old.association, old.response
            if db.scalar(
                select(OriginPrompt.id)
                .where(
                    OriginPrompt.reservation_id == reservation.id,
                    OriginPrompt.state.in_(("intent", "unknown")),
                )
                .limit(1)
            ):
                raise DomainError("origin_submission_unknown", "已有未知提交，请先等待核对", 409)
            association = uuid4().hex
            row = OriginPrompt(
                key_id=actor.key_id,
                channel_id=channel_id,
                reservation_id=reservation.id,
                association=association,
                idempotency_digest=idem,
                request_digest=request_digest,
                updated_at=utcnow(),
            )
            db.add(row)
            db.flush()
            return row.id, association, None

    def prompt_receipt(
        self, identifier: UUID, receipt: dict[str, Any] | None, *, rejected: bool = False
    ) -> None:
        with self.sessions.begin() as db:
            row = db.get(OriginPrompt, identifier)
            assert row
            if rejected:
                row.state, row.response = "rejected", receipt
            elif (
                receipt
                and isinstance(receipt.get("prompt_id"), str)
                and len(receipt["prompt_id"]) <= 128
            ):
                row.prompt_id, row.response, row.state = receipt["prompt_id"], receipt, "submitted"
            else:
                row.state = "unknown"
            row.updated_at = utcnow()

    def prompt_ids(self, actor: OriginActor, channel_id: UUID) -> set[str]:
        with self.sessions() as db:
            return set(
                db.scalars(
                    select(OriginPrompt.prompt_id)
                    .where(
                        OriginPrompt.key_id == actor.key_id,
                        OriginPrompt.channel_id == channel_id,
                        OriginPrompt.prompt_id.is_not(None),
                    )
                    .order_by(OriginPrompt.created_at.desc())
                    .limit(2000)
                )
            )

    def remember_file(
        self, key_id: UUID, channel_id: UUID, filename: str, subfolder: str, file_type: str
    ) -> None:
        if (
            file_type not in {"input", "output", "temp"}
            or len(filename) > 255
            or len(subfolder) > 1024
        ):
            return
        with self.sessions.begin() as db:
            db.execute(
                insert(OriginFile)
                .values(
                    key_id=key_id,
                    channel_id=channel_id,
                    filename=filename,
                    subfolder=subfolder,
                    file_type=file_type,
                )
                .on_conflict_do_nothing()
            )

    def owns_file(
        self, actor: OriginActor, channel_id: UUID, filename: str, subfolder: str, file_type: str
    ) -> bool:
        with self.sessions() as db:
            return bool(
                db.scalar(
                    select(OriginFile.id)
                    .where(
                        OriginFile.key_id == actor.key_id,
                        OriginFile.channel_id == channel_id,
                        OriginFile.filename == filename,
                        OriginFile.subfolder == subfolder,
                        OriginFile.file_type == file_type,
                    )
                    .limit(1)
                )
            )

    def userdata(
        self,
        actor: OriginActor,
        channel_id: UUID,
        *,
        mutate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self.sessions.begin() as db:
            db.execute(
                insert(OriginUserData)
                .values(key_id=actor.key_id, channel_id=channel_id, data={})
                .on_conflict_do_nothing()
            )
            row = db.execute(
                select(OriginUserData)
                .where(
                    OriginUserData.key_id == actor.key_id, OriginUserData.channel_id == channel_id
                )
                .with_for_update()
            ).scalar_one()
            if mutate is not None:
                update = mutate(dict(row.data))
                if len(json.dumps(update).encode()) > 8 * 1024 * 1024:
                    raise DomainError(
                        "origin_userdata_limit",
                        "个人工作流存储超过 8 MiB，请先导出或删除旧文件",
                        413,
                    )
                row.data = update
            return dict(row.data)
