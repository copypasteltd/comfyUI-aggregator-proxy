from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from generation_contracts.validation import StrictDTO

EvidenceId = Annotated[str, Field(pattern="^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")]
JobId = Annotated[str, Field(pattern="^job_[a-f0-9]{32}$")]


class SpecIsolationCreate(StrictDTO):
    job_id: JobId
    expected_revision: int = Field(ge=1)
    evidence_id: EvidenceId


class SpecIsolate(StrictDTO):
    action: Literal["isolate"]
    expected_revision: int = Field(ge=1)
    evidence_id: EvidenceId


class SpecResume(StrictDTO):
    action: Literal["resume"]
    expected_revision: int = Field(ge=1)
    validation_job_id: JobId


SpecIsolationAction = Annotated[SpecIsolate | SpecResume, Field(discriminator="action")]


class SpecIsolationView(BaseModel):
    id: str
    tenant_id: str
    project_id: str
    gpu_id: str
    release_id: str
    specification: dict[str, Any]
    spec_digest: str
    is_active: bool
    revision: int
    failure_count: int
    last_failure_at: int | None
    last_failure_job_id: str | None
    last_error_code: Literal["comfy_oom", "comfy_output_invalid"] | None
    last_success_generation_at: int | None
    isolated_at: int | None
    reason: Literal["repeated_failure", "manual_investigation"] | None
    cleared_validation_job_id: str | None
    updated_at: int


class SpecIsolationPage(BaseModel):
    data: list[SpecIsolationView]
    page: int
    has_more: bool
