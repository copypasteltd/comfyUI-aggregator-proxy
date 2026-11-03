from __future__ import annotations

import json
import shutil
import subprocess
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO

from generation_domain.errors import DomainError
from generation_domain.workflow_inputs import INPUT_KINDS

from generation_infrastructure.audio_media import inspect_audio


def inspect_input_video(path: Path, media_type: str) -> dict[str, Any]:
    probe, decoder = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe or not decoder:
        raise DomainError("media_probe_unavailable", "视频输入需要原生 ffprobe 与 ffmpeg", 503)
    try:
        options = ["-v", "error", "-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm"]
        completed = subprocess.run([probe, *options, "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate:format=duration,format_name", "-of", "json", str(path.resolve())], capture_output=True, timeout=30, check=True)  # noqa: S603 -- bounded server-owned file, fixed arguments
        if len(completed.stdout) > 65536 or len(completed.stderr) > 65536:
            raise ValueError("probe output limit")
        data = json.loads(completed.stdout)
        videos = [s for s in data["streams"] if s.get("codec_type") == "video"]
        audios = [s for s in data["streams"] if s.get("codec_type") == "audio"]
        if len(videos) != 1 or len(audios) > 1 or len(data["streams"]) != len(videos) + len(audios):
            raise ValueError("unsupported streams")
        stream = videos[0]
        declared = Decimal(data["format"]["duration"]) * 1000
        numerator, denominator = (Decimal(part) for part in stream["avg_frame_rate"].split("/"))
        rate = numerator / denominator
        container = data["format"]["format_name"].split(",")
        if (media_type == "video/mp4" and "mp4" not in container) or (media_type == "video/webm" and "webm" not in container) or not 0 < stream["width"] * stream["height"] <= 25000000 or not rate.is_finite() or not 0 < rate <= 120 or not declared.is_finite() or not 0 < declared <= 120000:
            raise ValueError("unsupported video specification")
        decoded = subprocess.run([decoder, "-nostdin", "-hide_banner", "-threads", "2", "-xerror", "-err_detect", "explode", *options, "-i", str(path.resolve()), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-stats_period", "3600", "-progress", "pipe:1", "-f", "null", "-"], capture_output=True, timeout=120, check=True)  # noqa: S603 -- full decode, no shell/network or generated output
        if len(decoded.stdout) > 65536 or len(decoded.stderr) > 65536:
            raise ValueError("decoder output limit")
        progress = dict(line.split("=", 1) for line in decoded.stdout.decode("ascii").splitlines() if "=" in line)
        duration = Decimal(progress["out_time_us"]) / 1000
        if progress.get("progress") != "end" or not duration.is_finite() or not 0 < duration <= 120100 or abs(duration - declared) > 150:
            raise ValueError("truncated or invalid video")
        return {"media_type": media_type, "width": stream["width"], "height": stream["height"], "duration_milliseconds": int(max(duration, declared).to_integral_value(rounding=ROUND_CEILING)), "fps": float(rate), "has_audio": bool(audios), "codec": stream["codec_name"], "container": "mp4" if media_type == "video/mp4" else "webm"}
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError, ArithmeticError, InvalidOperation, UnicodeError) as exc:
        raise DomainError("invalid_input_video", "视频未通过容器、规格与完整解码验证", 422) from exc


def validate_input(source: BinaryIO, kind: str = "image") -> dict[str, Any]:
    if kind not in INPUT_KINDS:
        raise DomainError("unsupported_input_kind", "输入类型不受支持", 422)
    if kind == "image":
        # Import lazily: existing content validators stay backward compatible.
        from generation_infrastructure.content import validate_image
        return validate_image(source)
    source.seek(0, 2)
    size = source.tell()
    maximum = INPUT_KINDS[kind][0]
    if not 0 < size <= maximum:
        source.seek(0)
        raise DomainError("invalid_input_size", "素材字节数超过该类型限制", 413)
    source.seek(0)
    header = source.read(32)
    if kind == "model":
        media_type = "model/gltf-binary"
    elif kind == "video":
        media_type = "video/webm" if header.startswith(b"\x1aE\xdf\xa3") else "video/mp4"
    elif header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        media_type = "audio/wav"
    elif header.startswith(b"fLaC"):
        media_type = "audio/flac"
    elif header.startswith(b"OggS"):
        media_type = "audio/ogg"
    else:
        media_type = "audio/mpeg"
    source.seek(0)
    try:
        with TemporaryDirectory(prefix="render-input-") as directory:
            path = Path(directory) / "input.bin"
            copied = 0
            with path.open("xb") as destination:
                while block := source.read(1024 * 1024):
                    copied += len(block)
                    if copied > maximum:
                        raise DomainError("invalid_input_size", "素材字节数超过该类型限制", 413)
                    destination.write(block)
            if copied != size:
                raise DomainError("input_changed", "验证期间素材内容发生变化", 422)
            if kind == "model":
                from generation_infrastructure.model3d_media import inspect_glb
                return {"media_type": media_type, **inspect_glb(path)}
            if kind == "audio":
                return inspect_audio(path, media_type)
            return inspect_input_video(path, media_type)
    finally:
        source.seek(0)
