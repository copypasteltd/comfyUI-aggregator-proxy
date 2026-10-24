from __future__ import annotations

from typing import Literal

from pydantic import Field

from generation_contracts.validation import JobCreate, KeyPolicy, StrictDTO


class GatewayCreate(StrictDTO):
    name: str = Field(min_length=1, max_length=128)
    issuer: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    allowed_cidrs: list[str] = Field(min_length=1, max_length=64)


class GatewayPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_cidrs: list[str] | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    rotate_credential: bool = False


class BindingCreate(StrictDTO):
    gateway_id: str
    tenant_id: str
    project_id: str
    external_tenant_ref: str = Field(min_length=1, max_length=128)
    external_key_ref: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    policy: KeyPolicy


class BindingPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    enabled: bool


class AdmissionCreate(StrictDTO):
    provider_request_id: str = Field(min_length=16, max_length=128, pattern=r"^[\x21-\x7e]+$")
    external_task_ref: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")
    price_version: str = Field(min_length=1, max_length=64, pattern=r"^[\x21-\x7e]+$")
    authorization_ref: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")
    request: JobCreate


class SettlementAck(StrictDTO):
    ledger_ref: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")


class GatewayView(StrictDTO):
    id: str
    name: str
    issuer: str
    enabled: bool
    revision: int
    allowed_cidrs: list[str]
    created_at: int


class GatewayMutation(StrictDTO):
    gateway: GatewayView
    secret: str | None = None
    secret_available: bool = False


class BindingView(StrictDTO):
    id: str
    gateway_id: str
    tenant_id: str
    project_id: str
    key_id: str
    external_tenant_ref: str
    external_key_ref: str
    enabled: bool
    revision: int
    created_at: int


class AdmissionView(StrictDTO):
    id: str
    job_id: str
    provider_request_id: str
    external_task_ref: str
    status: Literal["pending", "confirmed", "aborted", "expired", "rejected"]
    expires_at: int
    job_status: str
    error_code: str | None = None


class GatewayRequestIdentity(StrictDTO):
    provider_request_id: str = Field(min_length=16, max_length=128, pattern=r"^[\x21-\x7e]+$")
    external_task_ref: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")
    price_version: str = Field(min_length=1, max_length=64, pattern=r"^[\x21-\x7e]+$")
    authorization_ref: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")
    external_tenant_ref: str = Field(min_length=1, max_length=128)
    external_key_ref: str = Field(min_length=1, max_length=128)


class GatewayRequestResolution(GatewayRequestIdentity):
    gateway_id: str = Field(pattern=r"^gateway_[a-f0-9]{32}$")
    status: Literal["unknown", "admitted", "closed"]
    closure_id: str | None = None
    admission: AdmissionView | None = None
