from __future__ import annotations

from typing import Literal

from generation_domain.values import fingerprint
from generation_domain.workflow_catalogs import export_directory, parse_catalog_cursor
from generation_domain.workflow_imports import export_path
from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO


class WorkflowExportObservation(StrictDTO):
    protocol: Literal["local-directory-v1"]
    status: Literal["ready", "not_configured", "unavailable"]


class WorkflowExportStatus(StrictDTO):
    channel_id: str
    channel_revision: int
    status: Literal["ready", "not_configured", "unavailable", "unknown"]
    observation_status: Literal["fresh", "unknown"]
    observed_at: int | None
    catalog_supported: bool


class WorkflowCatalogRequest(StrictDTO):
    channel_id: str
    expected_revision: int = Field(ge=1)
    relative_directory: str = Field(default="", max_length=512)
    cursor: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def valid_directory(self) -> WorkflowCatalogRequest:
        export_directory(self.relative_directory)
        parse_catalog_cursor(self.cursor, self.relative_directory)
        return self


class WorkflowCatalogEntry(StrictDTO):
    name: str = Field(min_length=1, max_length=255)
    relative_path: str = Field(min_length=1, max_length=512)
    kind: Literal["directory", "file"]
    bytes: int | None = Field(default=None, ge=0)
    modified_at: int = Field(ge=0)
    status: Literal["directory", "ready", "invalid"]
    source_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    export_format: Literal["comfy-api", "manifest-v1"] | None = None
    node_count: int | None = Field(default=None, ge=1, le=2000)
    task_type: Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"] | None = None
    error_code: Literal["invalid_workflow_source", "ui_workflow_not_supported", "workflow_source_too_large", "workflow_export_not_allowed", "workflow_export_changed"] | None = None

    @model_validator(mode="after")
    def valid_entry(self) -> WorkflowCatalogEntry:
        if "/" in self.name or self.relative_path.rsplit("/", 1)[-1] != self.name:
            raise ValueError("invalid entry name")
        if self.kind == "directory":
            export_directory(self.relative_path)
            if self.status != "directory" or any(v is not None for v in (self.bytes, self.source_digest, self.export_format, self.node_count, self.task_type, self.error_code)):
                raise ValueError("invalid directory entry")
        else:
            export_path(self.relative_path)
            if self.bytes is None or self.status not in ("ready", "invalid"):
                raise ValueError("invalid file entry")
            if self.status == "ready":
                if self.source_digest is None or self.export_format is None or self.node_count is None or self.error_code is not None or self.bytes > 1048576:
                    raise ValueError("incomplete file entry")
            elif self.error_code is None or any(v is not None for v in (self.source_digest, self.export_format, self.node_count, self.task_type)):
                raise ValueError("invalid error entry")
        return self


class WorkflowCatalogPage(StrictDTO):
    protocol: Literal["local-directory-v1"]
    relative_directory: str = Field(max_length=512)
    directory_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: list[WorkflowCatalogEntry] = Field(max_length=20)
    skipped_count: int = Field(ge=0, le=2000)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def consistent_page(self) -> WorkflowCatalogPage:
        export_directory(self.relative_directory)
        names = [entry.name for entry in self.entries]
        if names != sorted(set(names)) or any(entry.relative_path != (self.relative_directory + "/" if self.relative_directory else "") + entry.name for entry in self.entries):
            raise ValueError("catalog order or path mismatch")
        if self.has_more:
            cursor = parse_catalog_cursor(self.next_cursor, self.relative_directory)
            if len(self.entries) != 20 or cursor is None or cursor["digest"] != self.directory_digest or cursor["after"] != names[-1]:
                raise ValueError("catalog continuation mismatch")
        elif self.next_cursor is not None:
            raise ValueError("unexpected catalog continuation")
        return self


class WorkflowCatalogReceipt(StrictDTO):
    success: Literal[True]
    applied_revision: int = Field(ge=1)
    catalog: WorkflowCatalogPage
    catalog_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_digest(self) -> WorkflowCatalogReceipt:
        if fingerprint(self.catalog.model_dump()) != self.catalog_digest:
            raise ValueError("catalog digest mismatch")
        return self


class WorkflowCatalogView(StrictDTO):
    id: str
    channel_id: str
    relative_directory: str
    status: str
    catalog: WorkflowCatalogPage | None
    error_code: str | None
