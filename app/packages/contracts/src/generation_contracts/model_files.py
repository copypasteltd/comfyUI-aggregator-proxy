from __future__ import annotations

from typing import Literal, Self

from generation_domain.model_files import inferred_model_kind, model_file_path
from generation_domain.values import fingerprint
from pydantic import BaseModel, Field, model_validator

from generation_contracts.model_artifacts import ModelKind
from generation_contracts.validation import StrictDTO


class ModelFileEntry(StrictDTO):
    relative_path: str = Field(min_length=1, max_length=1024)
    bytes: int = Field(gt=0, le=1099511627776)
    modified_at_ns: int = Field(ge=0, le=2**63 - 1)
    kind: ModelKind

    @model_validator(mode="after")
    def valid_file(self) -> Self:
        model_file_path(self.relative_path)
        if self.kind != inferred_model_kind(self.relative_path):
            raise ValueError("model kind does not match discovered directory")
        return self


class ModelFileCatalog(StrictDTO):
    protocol: Literal["models-directory-v1"]
    status: Literal["ready", "partial", "unavailable"]
    entries: list[ModelFileEntry] = Field(max_length=1000)
    scanned_entries: int = Field(ge=0, le=5000)
    skipped_entries: int = Field(ge=0, le=5000)
    reason: Literal["not_found", "path_not_allowed", "read_failed", "entry_limit", "time_budget", "depth_limit", "file_limit"] | None
    catalog_digest: str = Field(pattern="^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def consistent_catalog(self) -> Self:
        paths = [e.relative_path for e in self.entries]
        if paths != sorted(paths) or len({p.casefold() for p in paths}) != len(paths):
            raise ValueError("catalog paths must be unique and ordered")
        if len(paths) > self.scanned_entries or self.skipped_entries > self.scanned_entries:
            raise ValueError("catalog counts inconsistent")
        if (self.status == "ready") != (self.reason is None) or (self.status == "unavailable" and self.entries):
            raise ValueError("catalog completeness inconsistent")
        if fingerprint(self.model_dump(exclude={"catalog_digest"})) != self.catalog_digest:
            raise ValueError("catalog digest mismatch")
        return self


class NodeClasses(StrictDTO):
    node_classes: list[str] = Field(max_length=10000)
    node_schema_digest: str = Field(pattern="^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def bounded_names(self) -> Self:
        if any(not 1 <= len(name) <= 256 for name in self.node_classes) or self.node_classes != sorted(set(self.node_classes)):
            raise ValueError("node class names must be unique, ordered and bounded")
        return self


class ModelInventoryReceipt(StrictDTO):
    success: Literal[True]
    applied_revision: int = Field(ge=1)
    inventory: NodeClasses
    model_catalog: ModelFileCatalog


class ModelFileInspection(ModelFileEntry):
    protocol: Literal["model-inspection-v1"]
    catalog_digest: str = Field(pattern="^[a-f0-9]{64}$")
    sha256: str = Field(pattern="^[a-f0-9]{64}$")


class ModelInspectionReceipt(StrictDTO):
    success: Literal[True]
    applied_revision: int = Field(ge=1)
    inspection: ModelFileInspection


class ModelFileReadRequest(StrictDTO):
    channel_id: str = Field(pattern="^ch_[a-f0-9]{32}$")
    expected_revision: int = Field(ge=1)
    catalog_digest: str = Field(pattern="^[a-f0-9]{64}$")
    relative_path: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def valid_path(self) -> Self:
        model_file_path(self.relative_path)
        return self


class ModelFileRegistration(StrictDTO):
    name: str = Field(min_length=1, max_length=256)
    kind: ModelKind


class ModelFileRow(ModelFileEntry):
    registered_count: int
    registration_status: Literal["unregistered", "unverified", "size_mismatch"]


class ModelFilesView(BaseModel):
    scope_root: Literal["models"] = "models"
    status: Literal["ready", "partial", "unavailable", "unsupported", "unknown"]
    stale: bool
    observed_at: int | None
    catalog_digest: str | None
    reason: str | None
    page: int
    total: int
    has_more: bool
    scanned_entries: int
    skipped_entries: int
    data: list[ModelFileRow]


class ModelFileReadView(BaseModel):
    id: str
    channel_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    stale: bool
    observed_at: int | None
    error_code: str | None
    inspection: ModelFileInspection | None
    matching_registrations: int
    conflicting_registrations: int
