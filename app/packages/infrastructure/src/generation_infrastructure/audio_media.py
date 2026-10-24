from __future__ import annotations

import json
import shutil
import subprocess
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.media_types import MAX_AUDIO_BYTES, MAX_AUDIO_MILLISECONDS

FORMATS = {
    "audio/wav": ("wav", {"pcm_s16le", "pcm_s24le"}),
    "audio/flac": ("flac", {"flac"}),
    "audio/mpeg": ("mp3", {"mp3"}),
    "audio/ogg": ("ogg", {"opus"}),
}


def inspect_audio(path: Path, media_type: str) -> dict[str, Any]:
    probe, decoder = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe or not decoder:
        raise DomainError("media_probe_unavailable", "音频验证需要原生 ffprobe 与 ffmpeg", 503)
    if media_type not in FORMATS or not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        raise DomainError("invalid_audio", "音频类型或文件大小不受支持", 422)
    try:
        options = ["-v", "error", "-protocol_whitelist", "file", "-format_whitelist", "wav,flac,mp3,ogg"]
        probe_result = subprocess.run([probe, *options, "-show_entries", "stream=codec_type,codec_name,sample_rate,channels:format=duration,format_name", "-of", "json", str(path.resolve())], capture_output=True, timeout=30, check=True)  # noqa: S603 -- fixed local-file probe, no shell/network
        if len(probe_result.stdout) > 65536:
            raise ValueError("probe output limit")
        data = json.loads(probe_result.stdout)
        streams = data["streams"]
        container, codecs = FORMATS[media_type]
        if len(streams) != 1 or streams[0].get("codec_type") != "audio" or streams[0]["codec_name"] not in codecs or data["format"]["format_name"] != container:
            raise ValueError("container or codec mismatch")
        stream = streams[0]
        sample_rate, channels = int(stream["sample_rate"]), int(stream["channels"])
        declared = Decimal(data["format"]["duration"]) * 1000
        if not 8000 <= sample_rate <= 96000 or channels not in (1, 2) or not declared.is_finite() or not 0 < declared <= MAX_AUDIO_MILLISECONDS + 100:
            raise ValueError("audio specification out of bounds")
        # A null sink decodes every sample without buffering PCM or creating a
        # second media file. Progress contains bounded machine-readable timings.
        decoded = subprocess.run([decoder, "-nostdin", "-hide_banner", "-xerror", "-err_detect", "explode", *options, "-i", str(path.resolve()), "-map", "0:a:0", "-vn", "-sn", "-dn", "-stats_period", "3600", "-progress", "pipe:1", "-f", "null", "-"], capture_output=True, timeout=120, check=True)  # noqa: S603 -- fixed full local-file decode
        if len(decoded.stdout) > 65536 or len(decoded.stderr) > 65536:
            raise ValueError("decoder output limit")
        progress = dict(line.split("=", 1) for line in decoded.stdout.decode("ascii").splitlines() if "=" in line)
        microseconds = int(progress["out_time_us"])
        duration = int((Decimal(microseconds) / 1000).to_integral_value(rounding=ROUND_CEILING))
        if progress.get("progress") != "end" or not 0 < duration <= MAX_AUDIO_MILLISECONDS or abs(Decimal(microseconds) / 1000 - declared) > 100:
            raise ValueError("empty, truncated or oversized audio")
        return {"media_type": media_type, "container": container, "codec": stream["codec_name"], "sample_rate": sample_rate, "channels": channels, "duration_milliseconds": duration}
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError, InvalidOperation, UnicodeError) as exc:
        raise DomainError("invalid_audio", "输出音频未通过容器、规格与完整解码验证", 422) from exc
