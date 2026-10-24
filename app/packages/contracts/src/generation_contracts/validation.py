from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, model_validator


@lru_cache
def validator(name: str) -> Draft202012Validator:
    schema = json.loads(files("generation_contracts").joinpath("schemas", name + ".schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(name: str, value: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(validator(name).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        raise ValueError(f"{'/'.join(str(p) for p in error.path)}: {error.message}")
    return value


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class JobCreate(StrictDTO):
    model: str
    task_type: str
    input: dict[str, Any]
    parameters: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = None

    @model_validator(mode="after")
    def check_schema(self) -> JobCreate:
        validate("job-create", self.model_dump(exclude_unset=True))
        return self


class Selection(StrictDTO):
    mode: Literal["selected", "all_authorized"]
    selected_ids: list[str]


class ResourceLimit(StrictDTO):
    mode: Literal["bounded", "unlimited"]
    value: int | None


class ResourceLimits(StrictDTO):
    rpm: ResourceLimit
    burst: ResourceLimit
    daily_jobs: ResourceLimit
    unfinished_jobs: ResourceLimit
    generating_jobs: ResourceLimit
    daily_image_count: ResourceLimit
    daily_video_milliseconds: ResourceLimit
    daily_audio_milliseconds: ResourceLimit = Field(default_factory=lambda: ResourceLimit(mode="bounded", value=0))


class SpecificationLimits(StrictDTO):
    image_max_pixels: int
    video_max_pixels: int
    video_max_milliseconds: int
    audio_max_milliseconds: int = Field(default=0, ge=0, le=600000)
    max_references: int
    allowed_qualities: list[Literal["draft", "standard", "high"]]


class ContentGrant(StrictDTO):
    mode: Literal["owner_only", "project_shared", "shared_creator_keys"]
    project_ids: list[str]
    creator_key_ids: list[str]


class AllowedNetworks(StrictDTO):
    mode: Literal["any", "allowlist"]
    values: list[str]


class DownloadPolicy(StrictDTO):
    mode: Literal["authenticated_proxy", "signed_url"]
    signed_url_max_seconds: int


class KeyPolicy(StrictDTO):
    scopes: list[str]
    models: Selection
    workflow_releases: Selection
    channels: Selection
    spec_limits: SpecificationLimits
    content: ContentGrant
    limits: ResourceLimits
    allowed_cidrs: AllowedNetworks
    download: DownloadPolicy
    period_timezone: Literal["Asia/Taipei"]

    @model_validator(mode="after")
    def check_schema(self) -> KeyPolicy:
        validate("key-policy", self.model_dump())
        return self


class WorkerEvent(StrictDTO):
    protocol_version: str
    event_id: str
    node_id: str
    boot_id: str
    job_id: str
    attempt_id: str
    fencing_token: int
    journal_seq: int
    observed_at: int
    type: str
    payload: dict[str, Any]

    @model_validator(mode="after")
    def check_schema(self) -> WorkerEvent:
        validate("worker-event", self.model_dump())
        return self
