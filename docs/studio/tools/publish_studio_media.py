"""Publish only this task's verified original media and redacted call receipts.

The GLB stays in ignored local storage because it is a large binary. Its exact
metadata, digest and actual browser download evidence remain reviewable.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/media/studio-131"
PRIVATE = ROOT / ".local/studio-ops"
FFMPEG = ROOT.parent / "生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    state = json.loads((PRIVATE / "live-media-state.json").read_text(encoding="utf-8"))
    live_path = ROOT / "docs/research/studio-implementation/live-media-131.json"
    live = json.loads(live_path.read_text(encoding="utf-8"))
    browser_path = sorted((ROOT / "docs/research/studio-implementation").glob("live-library-*.json"))[-1]
    browser = json.loads(browser_path.read_text(encoding="utf-8"))
    assert live["passed"] and browser["passed"] and all(browser["cleanup"].values())
    assert len(state["operations"]) == len(browser["artifacts"]) == 8
    manifest_path = DEST / "media-manifest.json"
    assert not manifest_path.exists(), "Keep published evidence immutable"
    DEST.mkdir(parents=True, exist_ok=True)
    rows, calls = [], []
    ui_submit = json.loads((PRIVATE / "ui-image-submit.json").read_text(encoding="utf-8"))
    for mode, operation in state["operations"].items():
        assert operation["job"]["status"] == "succeeded"
        original = operation["original"]
        metadata = original["metadata"]
        source = ROOT / ".local/studio-live-media" / original["file"]
        assert source.stat().st_size == metadata["bytes"] and digest(source) == metadata["sha256"]
        observed = next(item for item in browser["artifacts"] if item["mode"] == mode)
        assert observed["sha256"] == metadata["sha256"] and observed["download_verified"]
        row = {"mode": mode, "model": operation["model"], "job_id": operation["job_id"], "metadata": metadata,
               "independent_probe": original["independent_probe"], "browser_playback": observed["browser_playback"],
               "browser_download_verified": True, "browser_metadata_duration_delta_ms": observed.get("duration_metadata_delta_ms"),
               "existing_audio_validator_tolerance_ms": observed.get("existing_audio_validator_tolerance_ms"),
               "cloud_lifetime_seconds": metadata["expires_at"] - operation["job"]["completed_at"],
               "file": None, "publication_note": None}
        assert row["cloud_lifetime_seconds"] == 28800
        if metadata["media_type"] != "model/gltf-binary":
            target = DEST / original["file"]
            assert not target.exists()
            if metadata["media_type"] == "image/png":
                with Image.open(source) as image:
                    pixels = image.tobytes()
                    with Image.frombytes(image.mode, image.size, pixels) as clean:
                        clean.save(target, format="PNG")
                with Image.open(target) as clean:
                    assert clean.tobytes() == pixels and not clean.info
                row["pixel_sha256"] = hashlib.sha256(pixels).hexdigest()
            else:
                subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source), "-map", "0", "-map_metadata", "-1", "-c", "copy", str(target)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(target), "-threads", "1", "-f", "null", os.devnull], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            row["file"] = target.name
            row["sample_bytes"] = target.stat().st_size
            row["sample_sha256"] = digest(target)
            row["publication_note"] = "Public sample has embedded container/Workflow metadata removed; original download digest stays in metadata. PNG pixels unchanged; audio/video streams copied without re-encoding."
        else:
            row["publication_note"] = "Large GLB original retained privately; full browser download verified, not an interactive 3D viewer"
        rows.append(row)
        frozen = ui_submit if mode == "image" else operation
        body = frozen["body"]
        calls.append({"mode": mode, "submission_source": "actual deployed browser UI" if mode == "image" else "native QA client using actual published usage/presets",
                      "method": "POST", "path": "/v1/jobs", "authentication": "Bearer <business-key: omitted>",
                      "idempotency_key_sha256": hashlib.sha256(frozen.get("idempotencyKey", frozen.get("idempotency_key")).encode()).hexdigest(),
                      "expected_release_digest": frozen.get("releaseDigest", frozen.get("release_digest")),
                      "exact_body_sha256": hashlib.sha256(body.encode()).hexdigest(), "body": json.loads(body),
                      "accepted_status": 202, "terminal_job": operation["job"], "content_metadata": metadata})
    evidence = {"passed": True, "scope": "Eight actual modes on 3080 and 5070; one actual UI submission, seven native API submissions; browser image/audio/video previews and all eight original downloads. GLB has a download card, not an interactive viewer. Not all eleven aliases, physical phones, capacity or full fault-matrix acceptance.",
                "owned_test_material": "QA-generated ceramic toy robot and music using an instrumental-style prompt; fixed Workflow lyrics are not overridden by this claim. No customer assets or credentials.",
                "reports": [{"file": item.relative_to(ROOT).as_posix(), "sha256": digest(item)} for item in (live_path, browser_path)], "artifacts": rows}
    (DEST / "api-calls.json").write_text(json.dumps({"scope": "Sanitized actual request/response receipts; authentication omitted and idempotency tokens hashed. IDs refer to temporary, isolated QA resources.", "calls": calls}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"published": True, "originals": sum(bool(row["file"]) for row in rows), "large_glb_metadata_only": True, "credentials_published": False}))


if __name__ == "__main__":
    main()
