from __future__ import annotations

import json
import shutil
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.media_types import AUDIO_MIME_TYPES

from generation_infrastructure.audio_media import inspect_audio
from generation_infrastructure.content import validate_image
from generation_infrastructure.model3d_media import inspect_glb


def inspect_media(path: Path, media_type: str) -> dict[str, Any]:
    if media_type == "model/gltf-binary":
        return inspect_glb(path)
    if media_type.startswith("audio/"):
        return inspect_audio(path, media_type)
    if media_type.startswith("image/"):
        with path.open("rb") as source:
            metadata = validate_image(source, 32 * 1024 * 1024)
        if metadata["media_type"] != media_type:
            raise DomainError("media_type_mismatch", "输出文件实际格式与声明不一致", 422)
        return metadata
    executable = shutil.which("ffprobe")
    if executable is None:
        raise DomainError("media_probe_unavailable", "原生 ffprobe 不可用，视频不能标记为已验证", 503)
    try:
        # Fixed probe, server-owned local file only; no shell or remote protocols.
        completed = subprocess.run([executable, "-v", "error", "-protocol_whitelist", "file", "-show_entries", "stream=codec_type,width,height:format=duration,format_name", "-of", "json", str(path.resolve())], capture_output=True, timeout=30, check=True)  # noqa: S603
        data = json.loads(completed.stdout)
        videos = [s for s in data["streams"] if s.get("codec_type") == "video"]
        if len(videos) != 1:
            raise ValueError
        duration = Decimal(data["format"]["duration"])
        format_names = data["format"]["format_name"].split(",")
        if not duration.is_finite() or duration <= 0 or (media_type == "video/mp4" and "mp4" not in format_names) or (media_type == "video/webm" and "webm" not in format_names):
            raise ValueError
        video = videos[0]
        if not 0 < video["width"] * video["height"] <= 25000000:
            raise ValueError
        return {"media_type": media_type, "width": video["width"], "height": video["height"], "duration_milliseconds": int(duration * 1000)}
    except (subprocess.SubprocessError, ValueError, KeyError, InvalidOperation, TypeError) as exc:
        raise DomainError("invalid_video", "输出视频无法通过媒体验证", 422) from exc


def check_specification(metadata: dict[str, Any], execution: dict[str, Any], *, audio_spec: dict[str, Any] | None = None) -> None:
    if metadata["media_type"] == "model/gltf-binary":
        if any(name in execution for name in ("size", "seconds", "fps", "strength")):
            raise DomainError("output_spec_mismatch", "3D 输出不能套用图像或音视频规格", 422)
        return
    if metadata["media_type"].startswith("audio/"):
        if not audio_spec or metadata["media_type"] != AUDIO_MIME_TYPES[audio_spec["audio_format"]] or any(metadata[name] != audio_spec[name] for name in ("sample_rate", "channels")) or not 0 < metadata["duration_milliseconds"] <= int(Decimal(str(execution["seconds"])) * 1000):
            raise DomainError("output_spec_mismatch", "输出音频与冻结格式、采样率、声道或时长上限不符", 422)
        return
    size = execution.get("size")
    if size and size != f"{metadata['width']}x{metadata['height']}":
        raise DomainError("output_spec_mismatch", "输出分辨率与冻结规格不符", 422)
    if "seconds" in execution and abs(metadata.get("duration_milliseconds", 0) - int(Decimal(str(execution["seconds"])) * 1000)) > 100:
        raise DomainError("output_spec_mismatch", "输出时长与冻结规格不符", 422)
