from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from generation_contracts.gateway import GatewayRequestIdentity
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from generation_infrastructure.models import GatewayRequestFence


def fence_matches(row: GatewayRequestFence, identity: GatewayRequestIdentity) -> bool:
    return all(getattr(row, name) == value for name, value in identity.model_dump().items())


def lock_request_fence(session: Session, gateway_id: UUID, identity: GatewayRequestIdentity) -> GatewayRequestFence:
    # Both unique identities participate in the INSERT conflict check. A different
    # request claiming the same public task must fail without aborting this tx.
    session.execute(insert(GatewayRequestFence).values(gateway_id=gateway_id, **identity.model_dump()).on_conflict_do_nothing())
    row = session.scalar(select(GatewayRequestFence).where(GatewayRequestFence.gateway_id == gateway_id, GatewayRequestFence.provider_request_id == identity.provider_request_id).with_for_update(key_share=True))
    if not row or not fence_matches(row, identity):
        raise DomainError("gateway_request_conflict", "网关请求身份与原记录不一致", 409)
    return row


@dataclass(frozen=True)
class PendingGateway:
    gateway_id: UUID
    binding_id: UUID
    binding_revision: int
    provider_request_id: str
    external_task_ref: str
    price_version: str
    authorization_ref: str
    external_tenant_ref: str
    external_key_ref: str
    catalog_stamp: dict[str, object] | None = None

    @property
    def identity(self) -> GatewayRequestIdentity:
        return GatewayRequestIdentity(provider_request_id=self.provider_request_id, external_task_ref=self.external_task_ref, price_version=self.price_version, authorization_ref=self.authorization_ref, external_tenant_ref=self.external_tenant_ref, external_key_ref=self.external_key_ref)

    @property
    def digest(self) -> str:
        intent: dict[str, object] = {"gateway_id": str(self.gateway_id), "binding_id": str(self.binding_id), "provider_request_id": self.provider_request_id, "external_task_ref": self.external_task_ref, "price_version": self.price_version, "authorization_ref": self.authorization_ref}
        if self.catalog_stamp is not None:
            intent["catalog"] = self.catalog_stamp
        return fingerprint(intent)
