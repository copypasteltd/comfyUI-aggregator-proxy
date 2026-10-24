from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO, validate


class CatalogModelBinding(StrictDTO):
    model_id: str
    expected_revision: int = Field(ge=1, le=2147483647)


class CatalogRule(StrictDTO):
    usage_account_id: str
    group_ids: list[str] = Field(min_length=1, max_length=1024)
    price_version: str
    unit_price: str
    model_bindings: list[CatalogModelBinding] = Field(min_length=1, max_length=64)


class CatalogPublication(StrictDTO):
    expected_revision: int = Field(ge=0, le=2147483646)
    instance_id: str
    models: list[CatalogRule] = Field(max_length=256)

    @model_validator(mode="after")
    def check_schema(self) -> Self:
        validate("gateway-catalog-publication", self.model_dump())
        return self


class CatalogReplay(StrictDTO):
    action: Literal["retry", "rollback"]
    expected_revision: int = Field(ge=1, le=2147483647)
    source_revision: int = Field(ge=1, le=2147483647)

    @model_validator(mode="after")
    def check_schema(self) -> Self:
        validate("gateway-catalog-replay", self.model_dump())
        return self


class CatalogStamp(StrictDTO):
    instance_id: str
    revision: int = Field(ge=1, le=2147483647)
    digest: str

    @model_validator(mode="after")
    def check_schema(self) -> Self:
        validate("gateway-catalog-stamp", self.model_dump())
        return self


CatalogError = Literal["unsupported_configuration", "account_unavailable", "group_unavailable", "storage_unavailable", "apply_failed"]


class CatalogReceipt(CatalogStamp):
    status: Literal["applied", "rejected"]
    error_code: CatalogError | None = None

    @model_validator(mode="after")
    def check_schema(self) -> Self:
        validate("gateway-catalog-receipt", self.model_dump())
        return self


class CatalogReleaseSummary(StrictDTO):
    gateway_id: str
    revision: int
    instance_id: str
    digest: str
    action: Literal["publish", "retry", "rollback"]
    source_revision: int | None
    created_at: int


class CatalogReleaseView(CatalogReleaseSummary):
    configuration_json: str


class CatalogReceiptView(StrictDTO):
    instance_id: str
    revision: int
    digest: str
    status: Literal["applied", "rejected"]
    error_code: CatalogError | None
    observed_at: int


class CatalogState(StrictDTO):
    gateway_id: str
    revision: int
    instance_id: str | None
    managed: bool
    status: Literal["legacy", "pending", "confirmed", "rejected", "stale"]
    desired_stale: bool
    confirmed_stale: bool
    desired: CatalogReleaseView | None
    confirmed: CatalogReleaseView | None
    last_receipt: CatalogReceiptView | None


class CatalogReceiptResult(StrictDTO):
    receipt: CatalogReceiptView
    activated: bool
    state: CatalogState
