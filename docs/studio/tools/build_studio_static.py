"""Package exactly the production bytes already exercised by the browser check."""
from __future__ import annotations

import hashlib
import argparse
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "infinite-canvas/web/dist"
PRIVATE = ROOT / ".local/studio-cloud-build"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--browser-report", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--storage-report", type=Path)
    args = parser.parse_args()
    RELEASE = args.release
    assert RELEASE.startswith("studio-") and "/" not in RELEASE and "\\" not in RELEASE and ".." not in RELEASE
    report_path = (ROOT / args.browser_report).resolve()
    build_path = (ROOT / args.build_report).resolve()
    assert report_path.is_relative_to(ROOT) and build_path.is_relative_to(ROOT)
    build = json.loads(build_path.read_text(encoding="utf-8"))
    assert build["passed"] and build["source_unchanged"] and build["mode"] == "build"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] and report["static_artifact_unchanged"] and all(report["cleanup"].values())
    files = {path.relative_to(DIST).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(DIST.rglob("*")) if path.is_file()}
    assert files == report["static_artifact_sha256"]
    manifest = {"format_version": 1, "release_id": RELEASE, "at": datetime.now(UTC).isoformat(), "browser_report": report_path.relative_to(ROOT).as_posix(), "browser_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(), "source_sha256": build["source_after"], "build_report": build_path.relative_to(ROOT).as_posix(), "build_report_sha256": hashlib.sha256(build_path.read_bytes()).hexdigest(), "files": [{"path": "studio/" + name, "sha256": digest, "bytes": (DIST / name).stat().st_size} for name, digest in files.items()], "native_only": True, "live_gpu_acceptance": False}
    if args.storage_report:
        storage_path = (ROOT / args.storage_report).resolve()
        assert storage_path.is_relative_to(ROOT)
        storage = json.loads(storage_path.read_text(encoding="utf-8"))
        assert storage["passed"] and all(storage["cleanup"].values()) and files == storage["static_artifact_sha256"]
        storage_version = storage["browser_storage_version"]
        assert type(storage_version) is int and storage_version > 0, "Use a report that actually records the exercised database version"
        manifest.update({"browser_storage_version": storage_version, "browser_storage_downgrade_supported": False, "storage_report": storage_path.relative_to(ROOT).as_posix(), "storage_report_sha256": hashlib.sha256(storage_path.read_bytes()).hexdigest()})
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
    (ROOT / f"docs/research/studio-implementation/static-build-{RELEASE}.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
