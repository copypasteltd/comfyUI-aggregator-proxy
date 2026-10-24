from __future__ import annotations

AUDIO_MIME_TYPES = {"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg", "opus": "audio/ogg"}
MAX_AUDIO_BYTES = 256 * 1024 * 1024
MAX_AUDIO_MILLISECONDS = 600000


def task_media_kind(task_type: str) -> str:
    if task_type == "text_to_audio":
        return "audio"
    return "video" if task_type.endswith("video") else "image"


def output_mime(suffix: str) -> str | None:
    # Windows registry-dependent mimetypes may return audio/x-wav or audio/x-flac.
    return {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg", ".opus": "audio/ogg", ".ogg": "audio/ogg"}.get(suffix.lower())
