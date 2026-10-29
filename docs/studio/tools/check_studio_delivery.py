"""Read-only verification of the reviewed Studio mirror and published receipts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = ROOT / "aggregator-service"
DEST = REPOSITORY / "app/apps/studio_web"
MEDIA = ROOT / "docs/media/studio-131"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    source = read(DEST / "delivery-source.json")
    for name, digest in source["source_files"].items():
        assert sha(DEST / name) == sha(ROOT / "infinite-canvas" / name) == digest
    progress = (ROOT / "docs/10-开发进度与交付记录.md").read_text(encoding="utf-8")
    assert set(re.findall(r"^\| (F-\d{2}) \|", progress, re.M)) == {f"F-{n:02}" for n in range(1, 47)}
    media = read(MEDIA / "media-manifest.json")
    assert media["passed"] and len(media["artifacts"]) == 8
    published = 0
    for artifact in media["artifacts"]:
        assert artifact["browser_download_verified"] and artifact["cloud_lifetime_seconds"] == 28800
        if artifact["file"]:
            file = MEDIA / artifact["file"]
            assert file.stat().st_size == artifact["sample_bytes"] and sha(file) == artifact["sample_sha256"]
            published += 1
        else:
            assert artifact["metadata"]["media_type"] == "model/gltf-binary"
    assert published == 7
    for ref in media["reports"]:
        assert sha(ROOT / ref["file"]) == ref["sha256"]
    browser = read(ROOT / media["reports"][1]["file"])
    assert browser["passed"] and len(browser["checks"]) == 9 and all(browser["cleanup"].values())
    assert len(browser["artifacts"]) == 8 and all(item["download_verified"] for item in browser["artifacts"])
    for screenshot in browser["screenshots"]:
        assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    tour = read(MEDIA / "studio-focused-tour-manifest.json")
    assert tour["passed"] and sha(ROOT / tour["source_report"]) == tour["source_report_sha256"]
    for item in tour["files"]:
        file = MEDIA / item["file"]
        assert item["fully_decoded"] and file.stat().st_size == item["bytes"] and sha(file) == item["sha256"]
    calls = read(MEDIA / "api-calls.json")["calls"]
    assert len(calls) == 8
    for call in calls:
        assert call["accepted_status"] == 202 and call["terminal_job"]["status"] == "succeeded"
        assert call["authentication"] == "Bearer <business-key: omitted>"
        artifact = next(item for item in media["artifacts"] if item["mode"] == call["mode"])
        assert call["content_metadata"] == artifact["metadata"]
    api = read(ROOT / "docs/research/studio-implementation/live-api-131.json")
    revoked = [item for item in api["checks"] if item["name"] == "temporary validation Key disabled"]
    assert api["passed"] and len(revoked) == 2 and all(item["detail"]["revoked_access"] == 401 for item in revoked)
    for name in ("README.md", "README.en.md", "media-manifest.json", "api-calls.json"):
        assert sha(MEDIA / name) == sha(REPOSITORY / "docs/studio/media/studio-131" / name)
    print(json.dumps({"passed": True, "at": datetime.now(UTC).isoformat(),
                      "scope": "Read-only source-mirror, public media/report/screenshot hashes, eight call receipts, all46 IDs and temporary Key revocation; not full runtime/phone/fleet acceptance",
                      "source_files_verified": len(source["source_files"]), "actual_gpu_modes": 8,
                      "browser_checks": 9, "public_samples": published, "large_glb_metadata_only": True,
                      "requirements_retained": 46, "temporary_keys_disabled": 2, "virtualization_used": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
