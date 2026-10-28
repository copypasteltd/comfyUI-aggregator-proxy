from __future__ import annotations

from datetime import timedelta
from ipaddress import ip_network
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.admin import KeyAction, KeyCreate, KeyPatch
from generation_contracts.defaults import default_key_policy
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import select

from generation_infrastructure.admin_base import (
    AdminBase,
    expiration,
    mutation,
    require_revision,
    scope_ids,
    timestamp,
)
from generation_infrastructure.models import (
    ApiCredential,
    ApiKey,
    Channel,
    Project,
    PublicModel,
    QuotaBucket,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.transactions import lock_buckets, record_change


def key_projection(key: ApiKey) -> dict[str, Any]:
    return {"id": public_id("key", key.id), "tenant_id": public_id("ten", key.tenant_id), "project_id": public_id("prj", key.project_id), "name": key.name, "enabled": key.enabled, "deleted_at": timestamp(key.deleted_at), "expires_at": timestamp(key.expires_at), "revision": key.revision, "issuer": key.issuer, "external_ref": key.external_ref, "policy": key.policy, "created_at": timestamp(key.created_at)}


class KeyAdministration(AdminBase):
    def list_keys(self, page: int = 1) -> dict[str, Any]:
        with self.sessions() as session:
            rows = list(session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id).offset((page - 1) * 50).limit(51)))
            return {"data": [key_projection(k) for k in rows[:50]], "page": page, "has_more": len(rows) > 50, "default_policy": default_key_policy()}

    def get_key(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            key = session.get(ApiKey, identifier)
            if not key:
                raise DomainError("not_found", "API Key 不存在", 404)
            result = key_projection(key)
            result["credentials"] = [{"id": public_id("cred", c.id), "prefix": "sk-gen_" + c.identifier[:8], "expires_at": timestamp(c.expires_at), "revoked_at": timestamp(c.revoked_at)} for c in session.scalars(select(ApiCredential).where(ApiCredential.key_id == identifier).order_by(ApiCredential.created_at.desc()))]
            return result

    def create_key(self, actor: AdminIdentity, data: KeyCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "key.create", token, data.model_dump())
            if idem.result:
                return {**idem.result, "secret_available": False}
            tenant, project = scope_ids(session, data.tenant_id, data.project_id)
            policy = data.policy.model_dump()
            self._validate_policy(session, tenant, project, policy)
            key = ApiKey(tenant_id=tenant, project_id=project, name=data.name, policy=policy, expires_at=expiration(data.expires_at))
            session.add(key)
            session.flush()
            credential_id, secret = issue_secret("sk-gen")
            credential = ApiCredential(key_id=key.id, identifier=credential_id, verifier=verifier(secret, self.settings.credential_pepper.get_secret_value()), pepper_version=self.settings.credential_pepper_version)
            session.add(credential)
            session.flush()
            result = {"key": key_projection(key), "credential_id": public_id("cred", credential.id)}
            idem.result = result  # Never retain the raw secret in a replay response.
            record_change(session, public_id("usr", actor.user_id), "key.created", key.id, request_id, revision_before=None, revision_after=key.revision)
            return {**result, "secret": secret, "secret_available": True}

    def _validate_policy(self, session: Any, tenant: UUID, project: UUID, policy: dict[str, Any]) -> None:
        for cidr in policy["allowed_cidrs"]["values"]:
            try:
                ip_network(cidr, strict=True)
            except ValueError as exc:
                raise DomainError("invalid_cidr", "CIDR 必须为合法网络地址", 422) from exc
        resources: tuple[tuple[str, type[Channel] | type[WorkflowRelease], str], ...] = (("channels", Channel, "ch"), ("workflow_releases", WorkflowRelease, "wfr"))
        for name, cls, prefix in resources:
            identifiers = [parse_id(prefix, value) for value in policy[name]["selected_ids"]]
            if identifiers and len(set(session.scalars(select(cls.id).where(cls.id.in_(identifiers), cls.tenant_id == tenant, cls.project_id == project)))) != len(identifiers):
                raise DomainError("cross_scope_policy", "授权资源不属于指定租户与项目", 422)
        aliases = policy["models"]["selected_ids"]
        if aliases:
            if len(set(session.scalars(select(PublicModel.alias).where(PublicModel.tenant_id == tenant, PublicModel.project_id == project, PublicModel.alias.in_(aliases))))) != len(aliases):
                raise DomainError("model_not_found", "授权模型不属于当前项目", 422)
        projects = [parse_id("prj", value) for value in policy["content"]["project_ids"]]
        if projects and len(set(session.scalars(select(Project.id).where(Project.tenant_id == tenant, Project.id.in_(projects))))) != len(projects):
            raise DomainError("cross_tenant_grant", "内容共享不能跨租户", 422)
        creators = [parse_id("key", value) for value in policy["content"]["creator_key_ids"]]
        if creators and len(set(session.scalars(select(ApiKey.id).where(ApiKey.tenant_id == tenant, ApiKey.id.in_(creators), ApiKey.project_id.in_(projects))))) != len(creators):
            raise DomainError("invalid_content_grant", "共享 Key 不在授权项目范围", 422)

    def patch_key(self, actor: AdminIdentity, identifier: UUID, data: KeyPatch, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            preview = session.get(ApiKey, identifier)
            if not preview:
                raise DomainError("not_found", "API Key 不存在", 404)
            lock_buckets(session, preview.tenant_id, preview.project_id, preview.id, utcnow().astimezone(__import__("zoneinfo").ZoneInfo("Asia/Taipei")).date().isoformat())
            session.expunge(preview)
            key = session.execute(select(ApiKey).where(ApiKey.id == identifier).with_for_update(key_share=True)).scalar_one()
            require_revision(key.revision, data.expected_revision)
            if key.deleted_at:
                raise DomainError("key_deleted", "已删除 Key 不能修改")
            if data.name is not None:
                key.name = data.name
            if data.policy:
                policy = data.policy.model_dump()
                self._validate_policy(session, key.tenant_id, key.project_id, policy)
                key.policy = policy
            if "expires_at" in data.model_fields_set:
                key.expires_at = expiration(data.expires_at)
            key.revision += 1
            record_change(session, public_id("usr", actor.user_id), "key.updated", key.id, request_id, revision_before=data.expected_revision, revision_after=key.revision)
            return key_projection(key)

    def key_action(self, actor: AdminIdentity, identifier: UUID, data: KeyAction, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "key." + data.action + ":" + str(identifier), token, data.model_dump())
            if idem.result:
                return {**idem.result, "secret_available": False}
            key = session.execute(select(ApiKey).where(ApiKey.id == identifier).with_for_update(key_share=True)).scalar_one_or_none()
            if not key or key.deleted_at:
                raise DomainError("not_found", "API Key 不存在", 404)
            require_revision(key.revision, data.expected_revision)
            result: dict[str, Any] = {}
            if data.action == "rotate":
                if key.issuer != "local":
                    raise DomainError("external_credential", "外部 Key 由来源网关管理")
                for credential in session.scalars(select(ApiCredential).where(ApiCredential.key_id == key.id, ApiCredential.revoked_at.is_(None)).order_by(ApiCredential.id)):
                    if data.overlap_seconds:
                        bound = utcnow() + timedelta(seconds=data.overlap_seconds)
                        credential.expires_at = min(credential.expires_at, bound) if credential.expires_at else bound
                    else:
                        credential.revoked_at = utcnow()
                credential_id, secret = issue_secret("sk-gen")
                credential = ApiCredential(key_id=key.id, identifier=credential_id, verifier=verifier(secret, self.settings.credential_pepper.get_secret_value()), pepper_version=self.settings.credential_pepper_version)
                session.add(credential)
                session.flush()
                result["credential_id"] = public_id("cred", credential.id)
                result["secret"] = secret
            else:
                key.enabled = data.action == "enable"
            key.revision += 1
            result["key"] = key_projection(key)
            idem.result = {k: v for k, v in result.items() if k != "secret"}
            record_change(session, public_id("usr", actor.user_id), "key." + data.action, key.id, request_id, revision_before=data.expected_revision, revision_after=key.revision)
            return result

    def delete_key(self, actor: AdminIdentity, identifier: UUID, expected_revision: int, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            key = session.execute(select(ApiKey).where(ApiKey.id == identifier).with_for_update(key_share=True)).scalar_one_or_none()
            if not key:
                raise DomainError("not_found", "API Key 不存在", 404)
            if key.deleted_at:
                return key_projection(key)
            require_revision(key.revision, expected_revision)
            key.enabled = False
            key.deleted_at = utcnow()
            key.revision += 1
            for credential in session.scalars(select(ApiCredential).where(ApiCredential.key_id == key.id)):
                credential.revoked_at = utcnow()
            record_change(session, public_id("usr", actor.user_id), "key.deleted", key.id, request_id, revision_before=expected_revision, revision_after=key.revision)
            return key_projection(key)

    def key_usage(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            if not session.get(ApiKey, identifier):
                raise DomainError("not_found", "API Key 不存在", 404)
            rows = list(session.scalars(select(QuotaBucket).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.scope_id == identifier).order_by(QuotaBucket.period.desc()).limit(32)))
            return {"key_id": public_id("key", identifier), "resource_usage": [{"period": r.period, "accepted_jobs": r.daily_jobs, "unfinished_jobs": r.unfinished_jobs, "generating_jobs": r.generating_jobs, "reserved_image_count": r.daily_image_count, "reserved_video_milliseconds": r.daily_video_milliseconds, "reserved_audio_milliseconds": r.daily_audio_milliseconds, "reserved_mesh_count": r.daily_mesh_count} for r in rows], "monetary_ledger": "sub2api"}
