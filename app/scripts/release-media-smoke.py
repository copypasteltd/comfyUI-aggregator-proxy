"""Verify installed audio decoding using only the candidate's native tools."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

from generation_infrastructure.audio_media import inspect_audio


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = args.payload.resolve(strict=True)
    output = args.output.resolve(strict=True)
    assert Path(sys.prefix).resolve() == payload.parent / "venv"
    assert output != payload and payload not in output.parents
    assert shutil.which("ffmpeg") is None and shutil.which("ffprobe") is None
    os.environ["PATH"] = str(payload / "media/bin") + os.pathsep + os.environ.get("PATH", "")
    executable = payload / "media/bin/ffmpeg.exe"
    assert Path(shutil.which("ffmpeg") or "").resolve() == executable
    wav = output / "sample.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x10\x00\xf0\xff" * 6000)
    cases = [("wav", "audio/wav", "pcm_s16le"), ("flac", "audio/flac", "flac"), ("mp3", "audio/mpeg", "libmp3lame"), ("opus", "audio/ogg", "libopus")]
    checked = []
    for extension, media_type, codec in cases:
        path = wav if extension == "wav" else output / ("sample." + extension)
        if path != wav:
            subprocess.run([str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(wav), "-c:a", codec, *(["-ar", "48000"] if extension == "opus" else []), str(path)], check=True, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed synthetic local audio, bundled authenticated encoder
        metadata = inspect_audio(path, media_type)
        assert metadata["channels"] == 1 and 500 <= metadata["duration_milliseconds"] <= 600
        assert metadata["sample_rate"] == (48000 if extension == "opus" else 24000)
        checked.append(extension)
    print(json.dumps({"passed": True, "formats": checked, "global_media_tools_unavailable": True, "bundled_tools_used": True}))


if __name__ == "__main__":
    main()
