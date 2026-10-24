from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from generation_contracts.retries import RetryInfo
from generation_contracts.validation import StrictDTO

JobStatus = Literal["admission_pending", "queued", "leased", "preparing", "submitting", "running", "collecting", "uploading", "verifying", "reconciling", "retry_wait", "cancel_requested", "succeeded", "failed", "cancelled", "expired"]
TaskType = Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"]


class CollectionQuery(StrictDTO):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    status: JobStatus | None = None


class JobSummary(StrictDTO):
    id: str = Field(pattern=r"^job_[a-f0-9]{32}$")
    object: Literal["generation.job"]
    model: str
    task_type: TaskType
    status: JobStatus
    created_at: int = Field(ge=1)
    updated_revision: int = Field(ge=1)
    error: dict[str, str] | None = None
    completed_at: int | None = None
    retry: RetryInfo | None = None


class ContentSummary(StrictDTO):
    id: str = Field(pattern=r"^content_[a-f0-9]{32}$")
    job_id: str = Field(pattern=r"^job_[a-f0-9]{32}$")
    bytes: int = Field(ge=1, le=2147483648)
    media_type: Literal["image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm", "audio/wav", "audio/flac", "audio/mpeg", "audio/ogg"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: int = Field(ge=1)
    expires_at: int = Field(ge=1)


class GatewayContentSummary(ContentSummary):
    external_task_ref: str = Field(pattern=r"^[!-~]{1,128}$")
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    task_type: TaskType


class CursorPage[T](StrictDTO):
    object: Literal["list"]
    data: list[T] = Field(max_length=100)
    has_more: bool
    next_cursor: str | None = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def continuation_matches(self) -> CursorPage[T]:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("Continuation must match has_more")
        return self
