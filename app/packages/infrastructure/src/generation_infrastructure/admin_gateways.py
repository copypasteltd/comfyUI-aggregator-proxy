from __future__ import annotations

from ipaddress import ip_network
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.gateway import BindingCreate, BindingPatch, GatewayCreate, GatewayPatch
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id
from sqlalchemy import select

from generation_infrastructure.admin_base import (
    AdminBase,
    mutation,
    require_revision,
    scope_ids,
    timestamp,
)
from generation_infrastructure.admin_keys import KeyAdministration
from generation_infrastructure.models import ApiKey, ExternalIdentityBinding, GatewayService
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.transactions import record_change


def gateway_projection(row: GatewayService) -> dict[str, Any]:
    return {"id": public_id("gateway", row.id), "name": row.name, "issuer": row.issuer, "enabled": row.enabled, "revision": row.revision, "allowed_cidrs": row.allowed_cidrs, "created_at": timestamp(row.created_at)}


def binding_projection(row: ExternalIdentityBinding) -> dict[str, Any]:
    return {"id": public_id("bind", row.id), "gateway_id": public_id("gateway", row.gateway_id), "tenant_id": public_id("ten", row.tenant_id), "project_id": public_id("prj", row.project_id), "key_id": public_id("key", row.key_id), "external_tenant_ref": row.external_tenant_ref, "external_key_ref": row.external_key_ref, "enabled": row.enabled, "revision": row.revision, "created_at": timestamp(row.created_at)}


def cidrs(values: list[str]) -> list[str]:
    try:
        return sorted({str(ip_network(value, strict=True)) for value in values})
    except ValueError as exc:
        raise DomainError("invalid_cidr", "请输入合法的来源 CIDR 网络地址", 422) from exc


class GatewayAdministration(AdminBase):
    def gateways(self, page: int = 1) -> dict[str, Any]:
        with self.sessions() as session:
            rows = list(session.scalars(select(GatewayService).order_by(GatewayService.created_at.desc(), GatewayService.id).offset((page - 1) * 50).limit(51)))
            return {"data": [gateway_projection(row) for row in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def create_gateway(self, actor: AdminIdentity, data: GatewayCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "gateway.create", token, data.model_dump())
            if idem.result:
                return {**idem.result, "secret_available": False}
            identifier, secret = issue_secret("gateway")
            row = GatewayService(name=data.name, issuer=data.issuer, allowed_cidrs=cidrs(data.allowed_cidrs), credential_identifier=identifier, credential_verifier=verifier(secret, self.settings.credential_pepper.get_secret_value()), pepper_version=self.settings.credential_pepper_version)
            session.add(row)
            session.flush()
            idem.result = {"gateway": gateway_projection(row)}
            record_change(session, public_id("usr", actor.user_id), "gateway.created", row.id, request_id, revision_before=None, revision_after=row.revision)
            return {**idem.result, "secret": secret, "secret_available": True}

    def patch_gateway(self, actor: AdminIdentity, identifier: UUID, data: GatewayPatch, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "gateway.patch:" + str(identifier), token, data.model_dump())
            if idem.result:
                return {**idem.result, "secret_available": False}
            row = session.execute(select(GatewayService).where(GatewayService.id == identifier).with_for_update()).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "网关不存在", 404)
            require_revision(row.revision, data.expected_revision)
            if data.name is not None:
                row.name = data.name
            if data.allowed_cidrs is not None:
                row.allowed_cidrs = cidrs(data.allowed_cidrs)
            if data.enabled is not None:
                row.enabled = data.enabled
            secret = None
            if data.rotate_credential:
                credential, secret = issue_secret("gateway")
                row.credential_identifier, row.credential_verifier = credential, verifier(secret, self.settings.credential_pepper.get_secret_value())
                row.pepper_version = self.settings.credential_pepper_version
            row.revision += 1
            idem.result = {"gateway": gateway_projection(row)}
            record_change(session, public_id("usr", actor.user_id), "gateway.updated", row.id, request_id, revision_before=data.expected_revision, revision_after=row.revision)
            return {**idem.result, "secret": secret, "secret_available": secret is not None}

    def bindings(self, page: int = 1) -> dict[str, Any]:
        with self.sessions() as session:
            rows = list(session.scalars(select(ExternalIdentityBinding).order_by(ExternalIdentityBinding.created_at.desc(), ExternalIdentityBinding.id).offset((page - 1) * 50).limit(51)))
            return {"data": [binding_projection(row) for row in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def create_binding(self, actor: AdminIdentity, data: BindingCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "gateway.binding.create", token, data.model_dump())
            if idem.result:
                return idem.result
            gateway = session.get(GatewayService, parse_id("gateway", data.gateway_id))
            if not gateway or not gateway.enabled:
                raise DomainError("not_found", "网关不存在或已停用", 404)
            tenant, project = scope_ids(session, data.tenant_id, data.project_id)
            policy = data.policy.model_dump()
            KeyAdministration(self.sessions, self.settings)._validate_policy(session, tenant, project, policy)
            key = ApiKey(tenant_id=tenant, project_id=project, name=data.name, policy=policy, issuer="gateway:" + gateway.id.hex, external_ref=fingerprint([data.external_tenant_ref, data.external_key_ref]))
            session.add(key)
            session.flush()
            binding = ExternalIdentityBinding(gateway_id=gateway.id, tenant_id=tenant, project_id=project, key_id=key.id, external_tenant_ref=data.external_tenant_ref, external_key_ref=data.external_key_ref)
            session.add(binding)
            session.flush()
            idem.result = binding_projection(binding)
            record_change(session, public_id("usr", actor.user_id), "gateway.binding_created", binding.id, request_id, revision_before=None, revision_after=binding.revision)
            return idem.result

    def patch_binding(self, actor: AdminIdentity, identifier: UUID, data: BindingPatch, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "gateway.binding.patch:" + str(identifier), token, data.model_dump())
            if idem.result:
                return idem.result
            row = session.execute(select(ExternalIdentityBinding).where(ExternalIdentityBinding.id == identifier).with_for_update()).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "外部主体绑定不存在", 404)
            require_revision(row.revision, data.expected_revision)
            row.enabled = data.enabled
            row.revision += 1
            idem.result = binding_projection(row)
            record_change(session, public_id("usr", actor.user_id), "gateway.binding_updated", row.id, request_id, revision_before=data.expected_revision, revision_after=row.revision)
            return idem.result
