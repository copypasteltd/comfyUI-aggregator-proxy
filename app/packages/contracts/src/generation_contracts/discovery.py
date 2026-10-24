from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO, validate


class PublishedModel(StrictDTO):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    object: Literal["model"]
    created: int = Field(ge=1, le=253402300799)
    owned_by: Literal["render-control"]


class PublishedModels(StrictDTO):
    object: Literal["list"]
    data: list[PublishedModel] = Field(max_length=4096)


class ReleaseIdentity(StrictDTO):
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PublicPreset(StrictDTO):
    quality: Literal["draft", "standard", "high"]
    size: str | None = Field(default=None, pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")
    seconds: int | float | None = Field(default=None, gt=0, le=3600)
    audio_format: Literal["wav", "flac", "mp3", "opus"] | None = None
    sample_rate: int | None = Field(default=None, ge=8000, le=96000)
    channels: Literal[1, 2] | None = None


class PublicOutput(StrictDTO):
    media_types: list[Literal["image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm", "audio/wav", "audio/flac", "audio/mpeg", "audio/ogg"]] = Field(min_length=1, max_length=9)
    expected_count: Literal[1]
    max_bytes: int = Field(ge=1, le=2147483648)


class PublishedCapability(PublishedModel):
    task_type: Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"]
    asynchronous: Literal[True]
    release: ReleaseIdentity
    supported_parameters: list[Literal["size", "quality", "seed", "n", "seconds", "fps", "strength", "negative_prompt"]] = Field(max_length=8)
    presets: list[PublicPreset] = Field(min_length=1, max_length=128)
    output: PublicOutput

    @model_validator(mode="after")
    def check_discovery(self) -> PublishedCapability:
        validate("model-discovery", {"object": "list", "data": [self.model_dump(exclude_none=True)]})
        return self


class PublishedCapabilities(StrictDTO):
    object: Literal["list"]
    data: list[PublishedCapability] = Field(max_length=4096)
