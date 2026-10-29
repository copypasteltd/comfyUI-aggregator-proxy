"""Render the reviewed current live UI tour with the existing native FFmpeg."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/media/studio-131"
FFMPEG = ROOT.parent / "生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe"


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", choices=("studio-tour", "studio-focused-tour"), default="studio-tour")
    args = parser.parse_args()
    reports = sorted((ROOT / "docs/research/studio-implementation").glob("live-controls-*.json"))
    report_path = reports[-1]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] and all(report["cleanup"].values())
    original = ROOT / report["private_recording"]
    assert original.resolve().is_relative_to((ROOT / ".local").resolve())
    DEST.mkdir(parents=True, exist_ok=True)
    evidence = DEST / ("tour-manifest.json" if args.name == "studio-tour" else args.name + "-manifest.json")
    assert not evidence.exists()
    results = []
    for suffix in ("mp4", "gif"):
        target = DEST / (args.name + "." + suffix)
        assert not target.exists()
        command = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(original), "-an"]
        if suffix == "mp4":
            command += ["-vf", "scale=1008:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "2"]
        else:
            command += ["-filter_complex", "fps=8,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle", "-loop", "0", "-threads", "2"]
        subprocess.run(command + [str(target)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(target), "-map", "0:v:0", "-threads", "1", "-f", "null", "-"], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        results.append({"file": target.name, "bytes": target.stat().st_size, "sha256": sha(target), "fully_decoded": True})
    result = {"passed": True, "scope": "actual public Studio UI tour at original speed; desktop/mobile emulation, not a full-duration GPU generation recording or physical phone", "source_report": report_path.relative_to(ROOT).as_posix(), "source_report_sha256": sha(report_path), "private_source_sha256": sha(original), "files": results, "virtualization_used": False}
    evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
