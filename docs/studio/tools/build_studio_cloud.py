"""Bind a native cloud-only update to committed source and verified Linux seed.

Rebuild all seven pure Python project wheels separately first. No database
migration, GPU Worker protocol change or remote dependency compilation occurs.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aggregator-service/app"
PRIVATE = ROOT / ".local/studio-cloud-build"
RELEASE = "linux-20261030.131.1"
SEED = ROOT.parent / "生图生视API封装/app/.local/audit-129/linux-20261029.129.3.tar.gz"
SEED_SHA = "757743ae8395001a289bbc324f5b53a1a440317430b03e5745c162504db5701e"
sys.path.insert(0, str(APP / "scripts"))
from release_source import source_revision, source_snapshot  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    snapshot = source_snapshot(APP)
    revision = source_revision(APP, snapshot)
    assert sha(SEED) == SEED_SHA
    PRIVATE.mkdir(parents=True, exist_ok=True)
    stage = PRIVATE / RELEASE
    stage.mkdir(exist_ok=False)
    with tarfile.open(SEED) as archive:
        archive.extractall(stage, filter="data")
    manifest = json.loads((stage / "linux-deployment.json").read_text())
    assert manifest["release_id"] == "linux-20261029.129.3"
    for item in manifest["files"]:
        path = (stage / item["path"]).resolve()
        assert path.is_relative_to(stage.resolve()) and path.stat().st_size == item["bytes"] and sha(path) == item["sha256"]
    changed = []
    git = subprocess.Popen(["git", "cat-file", "--batch"], cwd=APP.parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for name, digest in snapshot.items():
            git.stdin.write((manifest["application_source_revision"] + ":app/" + name + "\n").encode())
            git.stdin.flush()
            header = git.stdout.readline().split()
            if header[-1] == b"missing":
                changed.append(name)
                continue
            assert header[1] == b"blob"
            content = git.stdout.read(int(header[2]))
            assert git.stdout.read(1) == b"\n"
            if hashlib.sha256(content).hexdigest() != digest:
                changed.append(name)
    finally:
        git.stdin.close()
        assert git.wait(timeout=20) == 0
    assert not any(name.startswith(("migrations/", "apps/windows_worker/", "apps/control_runner/")) for name in changed), changed
    wheels = sorted((APP / ".local/studio-wheels").glob("*.whl"))
    assert len(wheels) == 7
    requirements = stage / "project-requirements.txt"
    raw = requirements.read_bytes()
    wheel_hashes = {}
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                if ".dist-info/" in name or name.endswith("/"):
                    continue
                matches = [path / name for folder in ("packages", "apps") for path in (APP / folder).glob("*/src") if (path / name).is_file()]
                assert len(matches) == 1 and matches[0].read_bytes() == archive.read(name), name
        target = stage / "wheels" / wheel.name
        previous = sha(target)
        assert raw.count(previous.encode()) == 1
        copy_hash = sha(wheel)
        raw = raw.replace(previous.encode(), copy_hash.encode())
        shutil.copyfile(wheel, target)
        wheel_hashes[wheel.name] = copy_hash
    requirements.write_bytes(raw)
    mapped_files_updated = []
    for target_file in stage.rglob("*"):
        if not target_file.is_file():
            continue
        relative = target_file.relative_to(stage)
        source_file = APP / relative
        if relative.parts[0] == "contracts":
            source_file = APP / "packages/contracts/src/generation_contracts" / ("schemas" if target_file.name.endswith(".schema.json") else "") / target_file.name
        if relative.parts[0] in {"scripts", "migrations", "config", "contracts"} and source_file.is_file() and source_file.read_bytes() != target_file.read_bytes():
            shutil.copyfile(source_file, target_file)
            mapped_files_updated.append(relative.as_posix())
    manifest.update({"created_at": datetime.now(UTC).isoformat(), "release_id": RELEASE, "base_linux_release": "linux-20261029.129.3", "base_linux_archive_sha256": SEED_SHA, "application_source_revision": revision, "current_source_sha256": snapshot, "current_source_files_recorded": len(snapshot), "changed_seed_sources": changed, "project_wheels_rebuilt": True, "rebuilt_projects": list(wheel_hashes), "admin_rebuilt": False, "database_schema_changed": False, "database_head": "03d128a00001", "worker_protocol_changed": False})
    manifest["mapped_seed_files_updated"] = mapped_files_updated
    manifest["files"] = [{"path": path.relative_to(stage).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)} for path in sorted(stage.rglob("*")) if path.is_file() and path.name != "linux-deployment.json"]
    (stage / "linux-deployment.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target = PRIVATE / (RELEASE + ".tar.gz")
    assert not target.exists()
    with tarfile.open(target, "w:gz") as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(stage).as_posix())
    assert source_snapshot(APP) == snapshot
    result = {"passed": True, "release_id": RELEASE, "archive_sha256": sha(target), "archive_bytes": target.stat().st_size, "application_source_revision": revision, "changed_sources": changed, "wheel_sha256": wheel_hashes, "source_files": len(snapshot), "new_database_migration": False, "worker_protocol_changed": False, "admin_rebuilt": False, "scope": "committed pure cloud wheel update from verified Linux 129.3 seed; frontend static artifact and ops backup repair tracked separately"}
    (PRIVATE / "build.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    public = APP.parent / "docs/evidence/studio-release-131/cloud-build.json"
    public.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
