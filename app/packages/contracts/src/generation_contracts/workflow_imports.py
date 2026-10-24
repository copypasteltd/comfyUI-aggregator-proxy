from __future__ import annotations

from typing import Annotated, Any, Literal

from generation_domain.workflow_imports import export_path
from pydantic import BaseModel, Field, field_validator

from generation_contracts.responses import WorkflowView
from generation_contracts.validation import StrictDTO


class WorkflowPull(StrictDTO):
    channel_id: str
    expected_revision: int = Field(ge=1)
    relative_path: str = Field(min_length=1, max_length=512)
    expected_source_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("relative_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        # Domain errors are mapped by the HTTP boundary, without echoing paths.
        return export_path(value)


class NewImportTarget(StrictDTO):
    mode: Literal["create"]
    name: str = Field(min_length=1, max_length=128)
    task_type: Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"]


class UpdateImportTarget(StrictDTO):
    mode: Literal["update"]
    workflow_id: str
    expected_revision: int = Field(ge=1)


class WorkflowImportPreview(StrictDTO):
    source_operation_id: str
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: Annotated[NewImportTarget | UpdateImportTarget, Field(discriminator="mode")]


class WorkflowImportCommit(StrictDTO):
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkflowImportResult(BaseModel):
    workflow: WorkflowView
    changed: bool


class ImportNodeDiff(BaseModel):
    count: int
    ids: list[str]
    truncated: bool


class ImportDiff(BaseModel):
    nodes: dict[str, ImportNodeDiff]
    changed_fields: list[str]


class ImportValidation(BaseModel):
    publishable: bool
    code: str
    message: str


class WorkflowImportPreviewView(BaseModel):
    id: str
    source_operation_id: str
    channel_id: str
    source_digest: str
    export_format: str
    candidate_digest: str
    target: dict[str, Any]
    diff: ImportDiff
    validation: ImportValidation
    expires_at: int
    status: Literal["pending", "expired", "committed"]
    result: WorkflowImportResult | None
