from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from generation_contracts.admin import ExpectedRevision
from generation_contracts.validation import StrictDTO


class ModelDocumentation(StrictDTO):
    display_name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=4000)
    example_prompt: str = Field(default="", max_length=8000)
    notes: list[str] = Field(default_factory=list, max_length=12)
    tags: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("notes", "tags")
    @classmethod
    def bounded_strings(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("说明条目须为 1～500 字符的非空文本")
        return list(dict.fromkeys(value))


class ModelDocumentationPatch(ExpectedRevision):
    documentation: ModelDocumentation


class UsageEndpoints(StrictDTO):
    create: Literal["/v1/jobs"] = "/v1/jobs"
    poll: Literal["/v1/jobs/{job_id}"] = "/v1/jobs/{job_id}"
    upload: Literal["/v1/assets"] = "/v1/assets"
    content: Literal["/v1/content/{content_id}"] = "/v1/content/{content_id}"
    download: Literal["/v1/content/{content_id}/download"] = "/v1/content/{content_id}/download"


class ReferenceSlot(StrictDTO):
    path: str = Field(max_length=80)
    required: bool
    description: str = Field(max_length=500)
    weight_supported: bool = False


class ReferenceUsage(StrictDTO):
    supported: bool
    exact_reference_count: int = Field(ge=0, le=4)
    slots: list[ReferenceSlot] = Field(max_length=6)
    media_types: list[Literal["image/png", "image/jpeg", "image/webp"]] = Field(max_length=3)
    max_upload_bytes: int = Field(ge=1, le=20 * 1024**2)
    max_image_pixels: Literal[25000000] = 25000000
    upload_field: Literal["file"] = "file"
    asset_scope: Literal["same_api_key"] = "same_api_key"
    note: str = Field(max_length=1000)


class ParameterUsage(StrictDTO):
    path: str = Field(max_length=80)
    required: bool
    description: str = Field(max_length=500)
    rules: dict[str, Any]


class UsageOutput(StrictDTO):
    media_types: list[Literal["image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm", "audio/wav", "audio/flac", "audio/mpeg", "audio/ogg", "model/gltf-binary"]] = Field(min_length=1, max_length=9)
    expected_count: Literal[1]
    max_bytes: int = Field(ge=1, le=2147483648)


class ModelUsage(StrictDTO):
    schema_version: Literal["1.0"] = "1.0"
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    notes: list[str] = Field(max_length=12)
    tags: list[str] = Field(max_length=12)
    task_type: Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"]
    asynchronous: Literal[True] = True
    endpoints: UsageEndpoints = Field(default_factory=UsageEndpoints)
    request_content_type: Literal["application/json"] = "application/json"
    required_scopes: list[str] = Field(max_length=12)
    defaults: dict[str, str | int | float | bool]
    parameter_details: list[ParameterUsage] = Field(min_length=1, max_length=12)
    references: ReferenceUsage
    presets: list[dict[str, str | int | float | bool]] = Field(min_length=1, max_length=128)
    output: UsageOutput
    request_schema: dict[str, Any]
    example_request: dict[str, Any]
    output_retention_seconds: Literal[28800] = 28800
    availability_note: str = Field(max_length=1000)
