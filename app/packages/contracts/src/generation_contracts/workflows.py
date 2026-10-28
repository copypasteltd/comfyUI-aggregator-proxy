from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from generation_contracts.validation import StrictDTO


class Binding(StrictDTO):
    source: str
    node_id: str
    input_name: str


class OutputAdapter(StrictDTO):
    adapter: Literal["save-image-v1", "video-files-v1", "audio-files-v1", "model3d-glb-v1"]
    final_node_ids: list[str] = Field(min_length=1, max_length=20)
    expected_count: Literal[1]
    history_field: Literal["images", "gifs", "videos", "audio", "3d"]
    max_bytes: int = Field(gt=0, le=2147483648)
    mime_types: list[str] = Field(min_length=1)


class Preset(StrictDTO):
    quality: Literal["draft", "standard", "high"]
    size: str | None = Field(default=None, pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")
    seconds: int | float | None = None
    audio_format: Literal["wav", "flac", "mp3", "opus"] | None = None
    sample_rate: int | None = Field(default=None, ge=8000, le=96000)
    channels: Literal[1, 2] | None = None
    values: dict[str, str | int | float | bool]


class LockedModel(StrictDTO):
    path: str
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    bytes: int = Field(gt=0)


class LockedNode(StrictDTO):
    directory: str
    commit: str = Field(pattern="^[a-f0-9]{40}$")


class DependencyLock(StrictDTO):
    comfy_commit: str = Field(pattern="^[a-f0-9]{40}$")
    models: list[LockedModel]
    custom_nodes: list[LockedNode]


class ApiModelInput(StrictDTO):
    class_type: str = Field(min_length=1, max_length=256)
    input_name: str = Field(min_length=1, max_length=256)
    value: str = Field(min_length=1, max_length=1024)


class ApiOnlyDependencyLock(StrictDTO):
    mode: Literal["comfy-api-only-v1"]
    comfy_version: str = Field(min_length=1, max_length=128)
    node_classes: list[str] = Field(min_length=1, max_length=2000)
    model_inputs: list[ApiModelInput] = Field(max_length=2000)


class RuntimeProfile(StrictDTO):
    id: str
    gpu_hardware_id: str
    approved: bool = False
    evidence_id: str | None = None
    peak_vram_bytes: int | None = Field(default=None, ge=1)
    warm_p95_milliseconds: int | None = Field(default=None, ge=1)
    execution_timeout_seconds: int = Field(ge=1, le=7200)


class WorkflowManifest(StrictDTO):
    format_version: Literal["1.0", "1.1", "1.2"]
    task_type: Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"]
    workflow_api: dict[str, dict[str, Any]]
    bindings: list[Binding]
    output: OutputAdapter
    presets: list[Preset]
    dependency_lock: DependencyLock | ApiOnlyDependencyLock
    runtime_profiles: list[RuntimeProfile]
    supported_parameters: list[str]
