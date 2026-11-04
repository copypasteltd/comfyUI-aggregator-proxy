from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from generation_contracts.validation import StrictDTO


class Binding(StrictDTO):
    source: str
    node_id: str
    input_name: str
    pointer: str | None = Field(default=None, min_length=2, max_length=512)


class VideoAudioPolicy(StrictDTO):
    mode: Literal["generated", "source_video", "reference_audio", "silent"]
    source_slot: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    description: str = Field(min_length=1, max_length=1000)


class OutputAdapter(StrictDTO):
    adapter: Literal["save-image-v1", "video-files-v1", "audio-files-v1", "model3d-glb-v1"]
    final_node_ids: list[str] = Field(min_length=1, max_length=20)
    expected_count: Literal[1]
    history_field: Literal["images", "gifs", "videos", "audio", "3d"]
    max_bytes: int = Field(gt=0, le=2147483648)
    mime_types: list[str] = Field(min_length=1)
    audio_policy: VideoAudioPolicy | None = None


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


class LockedNativeFile(StrictDTO):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    bytes: int = Field(strict=True, gt=0, le=1024**4)


class LockedNativeRoot(StrictDTO):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    files: list[LockedNativeFile] = Field(min_length=1, max_length=4096)


class DependencyLock(StrictDTO):
    comfy_commit: str = Field(pattern="^[a-f0-9]{40}$")
    models: list[LockedModel]
    custom_nodes: list[LockedNode]
    files: list[LockedModel] = Field(default_factory=list, max_length=2000)
    native_roots: list[LockedNativeRoot] = Field(default_factory=list, max_length=8)


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


class InputSlot(StrictDTO):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: Literal["image", "video", "audio", "model"]
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    required: bool
    max_bytes: int = Field(gt=0, le=512 * 1024**2)
    mime_types: list[str] = Field(min_length=1, max_length=4)
    path_mode: Literal["comfy_relative", "comfy_absolute"]


class TextSlot(StrictDTO):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    required: bool
    max_length: int = Field(ge=1, le=32000)


class ContextProfile(StrictDTO):
    family: Literal["h3-av-v1"]
    compatibility: str = Field(pattern=r"^[a-f0-9]{64}$")
    load: bool
    save_node_id: str | None = Field(default=None, min_length=1, max_length=64)


class WorkflowManifest(StrictDTO):
    format_version: Literal["1.0", "1.1", "1.2", "1.3"]
    task_type: Literal["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"]
    workflow_api: dict[str, dict[str, Any]]
    bindings: list[Binding]
    output: OutputAdapter
    presets: list[Preset]
    dependency_lock: DependencyLock | ApiOnlyDependencyLock
    runtime_profiles: list[RuntimeProfile]
    supported_parameters: list[str]
    input_slots: list[InputSlot] = Field(default_factory=list, max_length=16)
    text_slots: list[TextSlot] = Field(default_factory=list, max_length=16)
    context: ContextProfile | None = None
