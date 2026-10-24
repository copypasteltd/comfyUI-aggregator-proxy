from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from generation_contracts.validation import StrictDTO

ModelKind = Literal["checkpoint", "diffusion_model", "unet", "vae", "lora", "text_encoder", "clip", "embedding", "upscaler", "controlnet", "other"]


class ModelArtifactCreate(StrictDTO):
    tenant_id: str
    project_id: str
    name: str = Field(min_length=1, max_length=256)
    kind: ModelKind
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0, le=1099511627776)
    relative_path: str = Field(min_length=1, max_length=1024)


class ModelArtifactPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> ModelArtifactPatch:
        if self.name is None and self.enabled is None:
            raise ValueError("至少指定名称或启停状态")
        return self


class ModelArtifactView(BaseModel):
    id: str
    tenant_id: str
    project_id: str
    name: str
    kind: str
    sha256: str | None
    bytes: int | None
    relative_path: str
    enabled: bool
    revision: int
    created_at: int
    deleted_at: int | None
    referenced_release_count: int


class ModelArtifactDeletion(BaseModel):
    id: str
    status: Literal["archived"]
