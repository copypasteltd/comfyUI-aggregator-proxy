from __future__ import annotations

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO
from generation_contracts.worker import Renew


class OutputSpec(StrictDTO):
    position: int = Field(ge=0, le=99)
    bytes: int = Field(ge=1, le=2147483648)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(pattern=r"^(image/(png|jpeg|webp)|video/(mp4|webm)|audio/(wav|flac|mpeg|ogg))$")


class UploadAuthorization(Renew):
    outputs: list[OutputSpec] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def positions(self) -> UploadAuthorization:
        if [item.position for item in self.outputs] != list(range(len(self.outputs))):
            raise ValueError("outputs must be ordered with contiguous positions")
        return self


class Complete(Renew):
    upload_ids: list[str] = Field(min_length=1, max_length=100)
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
