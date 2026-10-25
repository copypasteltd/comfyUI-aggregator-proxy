"""Encode owned Playwright recordings with the pinned native FFmpeg tools."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT.parent / "docs/media"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="12-character owned capture run ID")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--catalog", action="store_true", help="Encode the separate gateway catalog capture")
    modes.add_argument("--confirmed-retry", action="store_true", help="Encode the confirmed OOM retry capture")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{12}", args.run):
        raise SystemExit("Invalid capture run ID")
    private = ROOT / (".local/confirmed-retry-browser" if args.confirmed_retry else ".local/gateway-catalog-browser" if args.catalog else ".local/readme-media") / args.run
    media = MEDIA / "confirmed-retry" if args.confirmed_retry else MEDIA / "gateway-catalog" if args.catalog else MEDIA
    capture = json.loads((media / "capture.json").read_text(encoding="utf-8"))
    assert capture["passed"] and capture["closed_contexts"] == (2 if args.confirmed_retry else 4 if args.catalog else 3) and capture["browser_closed"]
    lock = json.loads((ROOT / "config/media-tools.lock.json").read_text(encoding="utf-8"))
    tools = ROOT / ".local/toolchains/ffmpeg-8.1"
    for name in ("bin/ffmpeg.exe", "bin/ffprobe.exe"):
        target = tools / name
        if digest(target) != lock["files"][name]["sha256"]:
            raise SystemExit("Pinned media tool hash mismatch")
    log = private / "encoding.log"
    records: list[dict[str, Any]] = []
    with log.open("wb") as output:
        for name in (("confirmed-retry",) if args.confirmed_retry else ("gateway-catalog",) if args.catalog else ("admin-tour", "workflow-lifecycle")):
            source = Path(capture["recordings"][0]["path"]) if args.confirmed_retry else private / (name + ".webm")
            if args.confirmed_retry and (source.resolve().parent != (private / "recordings").resolve() or source.suffix != ".webm"):
                raise SystemExit("Recording does not belong to this capture run")
            for extension in ("mp4", "gif"):
                target = media / (name + "." + extension)
                command = [str(tools / "bin/ffmpeg.exe"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source), "-an"]
                if extension == "mp4":
                    command.extend(["-vf", "fps=24,scale=1280:-2:flags=lanczos", "-c:v", "libx264", "-threads", "2", "-preset", "medium", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
                else:
                    command.extend(["-filter_complex", "fps=8,scale=1008:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle", "-loop", "0"])
                result = subprocess.run([*command, str(target)], stdout=output, stderr=subprocess.STDOUT, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- pinned executable, fixed filters, owned paths
                if result.returncode:
                    raise SystemExit("Media encoding failed; inspect the owned encoding.log")
                probe = subprocess.run([str(tools / "bin/ffprobe.exe"), "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height", "-of", "json", str(target)], capture_output=True, check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- pinned probe and generated file
                metadata = json.loads(probe.stdout)
                if float(metadata["format"]["duration"]) < 3:
                    raise SystemExit("Recording unexpectedly short")
                subprocess.run([str(tools / "bin/ffmpeg.exe"), "-hide_banner", "-loglevel", "error", "-nostdin", "-protocol_whitelist", "file", "-i", str(target), "-map", "0:v:0", "-threads", "1", "-f", "null", "-"], stdout=output, stderr=subprocess.STDOUT, check=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed tool and generated local media
                records.append({"path": target.name, "bytes": target.stat().st_size, "sha256": digest(target), "probe": metadata})
    for path in sorted(media.glob("*.png")):
        records.append({"path": path.name, "bytes": path.stat().st_size, "sha256": digest(path)})
    counts = {name: sum(Path(item["path"]).suffix == "." + extension for item in records) for name, extension in (("screenshots", "png"), ("mp4", "mp4"), ("gif", "gif"))}
    manifest = {"at": datetime.now(UTC).isoformat(), "passed": True, "synthetic_data": True, "real_gpu_generation": False, "playwright": "1.58.0", "browser_version": capture["browser_version"], "native_media_tools": lock["version"], "no_authentication_recording": True, **counts, "files": records}
    (media / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, **counts, "total_MiB": round(sum(item["bytes"] for item in records) / 1024**2, 2)}))


if __name__ == "__main__":
    main()
