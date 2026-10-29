"""Package exactly the production bytes already exercised by the browser check."""
from __future__ import annotations

import hashlib
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "infinite-canvas/web/dist"
PRIVATE = ROOT / ".local/studio-cloud-build"
RELEASE = "studio-20261030.131.1"


if __name__ == "__main__":
    report_path = ROOT / "docs/research/studio-implementation/browser-2026-10-29T20-37-07-338Z.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] and report["static_artifact_unchanged"] and all(report["cleanup"].values())
    files = {path.relative_to(DIST).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(DIST.rglob("*")) if path.is_file()}
    assert files == report["static_artifact_sha256"]
    manifest = {"format_version": 1, "release_id": RELEASE, "at": datetime.now(UTC).isoformat(), "browser_report": report_path.relative_to(ROOT).as_posix(), "browser_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(), "source_sha256": "446225b9df132108591767c6458d0a68c5753612354d59bd6713084ceebb2f3f", "files": [{"path": "studio/" + name, "sha256": digest, "bytes": (DIST / name).stat().st_size} for name, digest in files.items()], "native_only": True, "live_gpu_acceptance": False}
    PRIVATE.mkdir(parents=True, exist_ok=True)
    target = PRIVATE / (RELEASE + ".tar.gz")
    assert not target.exists()
    manifest_file = PRIVATE / (RELEASE + ".json")
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with tarfile.open(target, "w:gz") as archive:
        for name in files:
            archive.add(DIST / name, arcname="studio/" + name)
        archive.add(manifest_file, arcname="studio-deployment.json")
    receipt = {"passed": True, "release_id": RELEASE, "archive_bytes": target.stat().st_size, "archive_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "file_count": len(files), "uncompressed_bytes": sum(item["bytes"] for item in manifest["files"]), "browser_report_sha256": manifest["browser_report_sha256"], "source_sha256": manifest["source_sha256"]}
    (PRIVATE / "static-build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (ROOT / "docs/research/studio-implementation/static-build-131.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
