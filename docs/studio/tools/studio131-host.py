"""Fixed root-only deployment payload verification for the authorised native host.

Input archives/digests are transferred together from the reviewed workspace.
No credentials are embedded or printed. No database migration is executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

TASK = Path("/var/lib/comfyui-aggregator-deploy/studio131")
UPLOAD = Path("/home/ubuntu/.comfy-aggregator-upload/studio131")
BASE = Path("/opt/comfyui-aggregator")
RELEASE_ID = "linux-20261030.131.1"
STATIC_ID = "studio-20261030.131.1"
RELEASE = BASE / "releases" / RELEASE_ID
STATIC = BASE / "studio-releases" / STATIC_ID
OLD = BASE / "releases/linux-20261029.129.3"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def unpack(name, target, manifest_name, expected):
    source = UPLOAD / name
    assert source.is_file() and not source.is_symlink() and digest(source) == expected
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(source) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and ".." not in path.parts
        archive.extractall(target, filter="data")
    manifest = json.loads((target / manifest_name).read_text())
    declared = set()
    for entry in manifest["files"]:
        path = (target / entry["path"]).resolve()
        assert path.is_relative_to(target.resolve()) and path.is_file() and not path.is_symlink()
        assert path.stat().st_size == entry["bytes"] and digest(path) == entry["sha256"]
        declared.add(path)
    assert declared == {path.resolve() for path in target.rglob("*") if path.is_file() and path != target / manifest_name}
    for path in target.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return manifest


def db_probe():
    # Runs with an explicit production config as root; only non-sensitive counts leave this process.
    sys.path[:0] = [str(path) for path in (BASE / "current/venv/lib").glob("python*/site-packages")]
    from generation_infrastructure.database import make_sessions
    from generation_infrastructure.settings import Settings
    from sqlalchemy import text
    settings = Settings(_env_file=Path("/etc/comfyui-aggregator/cloud.env"))
    assert settings.environment == "production"
    sessions = make_sessions(settings)
    try:
        with sessions() as session:
            result = {"database_head": session.scalar(text("SELECT version_num FROM alembic_version")), "fsync": session.scalar(text("SHOW fsync")), "synchronous_commit": session.scalar(text("SHOW synchronous_commit")), "unfinished_jobs": session.scalar(text("SELECT count(*) FROM jobs WHERE state NOT IN ('succeeded','failed','cancelled','expired')")), "gpu_occupancies": session.scalar(text("SELECT count(*) FROM gpu_occupancies")), "management_holds": session.scalar(text("SELECT count(*) FROM gpu_resources WHERE management_hold IS NOT NULL")), "jobs": session.scalar(text("SELECT count(*) FROM jobs")), "content": session.scalar(text("SELECT (SELECT count(*) FROM artifacts) + (SELECT count(*) FROM assets)"))}
    finally:
        sessions.kw["bind"].dispose()
    assert result["database_head"] == "03d128a00001" and result["fsync"] == "on" and result["synchronous_commit"] == "on"
    assert result["unfinished_jobs"] == result["gpu_occupancies"] == result["management_holds"] == 0
    return result


def check_backup():
    path = max(Path("/var/lib/comfyui-aggregator/backups").glob("control-*.json"), key=lambda p: p.stat().st_mtime)
    value = json.loads(path.read_text())
    archive = path.with_suffix(".dump")
    assert value["archive_list_verified"] and archive.stat().st_size == value["bytes"] and digest(archive) == value["sha256"]
    return {"manifest": path.name, "sha256": value["sha256"], "bytes": value["bytes"]}


def symlink(target, link):
    assert target.resolve().is_relative_to(BASE) and link.parent == BASE
    temporary = BASE / (link.name + "-studio131-pending")
    assert not temporary.exists() and not temporary.is_symlink()
    temporary.symlink_to(target)
    temporary.replace(link)


def switch(target):
    before_worker = command("systemctl", "show", "comfy-aggregator-http-worker.service", "--property=MainPID", "--value")
    before = db_probe()
    command("systemctl", "stop", "comfy-aggregator-api.service", "comfy-aggregator-runner.service", "comfy-aggregator-gc.service")
    original = (BASE / "current").resolve()
    try:
        stopped = db_probe()
        assert before["jobs"] == stopped["jobs"] and before["content"] == stopped["content"]
        symlink(target, BASE / "current")
        command("systemctl", "start", "comfy-aggregator-api.service", "comfy-aggregator-runner.service", "comfy-aggregator-gc.service")
        command("curl", "--fail", "--silent", "--retry", "20", "--retry-delay", "1", "--retry-connrefused", "--max-time", "5", "-H", "Host: 43.165.167.179", "http://127.0.0.1:32880/health/ready")
        after = db_probe()
        assert before["jobs"] == after["jobs"] and before["content"] == after["content"]
        assert before_worker == command("systemctl", "show", "comfy-aggregator-http-worker.service", "--property=MainPID", "--value")
        return {"old": str(original), "new": str(target), "before": before, "after": after, "http_worker_pid_unchanged": True}
    except BaseException:
        symlink(original, BASE / "current")
        command("systemctl", "start", "comfy-aggregator-api.service", "comfy-aggregator-runner.service", "comfy-aggregator-gc.service")
        raise


def verify_installed():
    manifest = json.loads((RELEASE / "linux-deployment.json").read_text())
    site = next((RELEASE / "venv/lib").glob("python*/site-packages"))
    import zipfile
    checked = 0
    for wheel in (RELEASE / "wheels").glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if ".dist-info/" in name or name.endswith("/"):
                    continue
                assert (site / name).read_bytes() == archive.read(name), name
                checked += 1
    result = {"passed": True, "release_id": RELEASE_ID, "wheel_payload_files_verified": checked, "source_revision": manifest["application_source_revision"], "at": datetime.now(UTC).isoformat()}
    (TASK / "prepared.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


def main():
    action = sys.argv[1]
    if action == "extract":
        assert (BASE / "current").resolve() == OLD and not RELEASE.exists() and not STATIC.exists()
        cloud = json.loads((TASK / "cloud-build.json").read_text())
        static = json.loads((TASK / "static-build.json").read_text())
        m = unpack(RELEASE_ID + ".tar.gz", RELEASE, "linux-deployment.json", cloud["archive_sha256"])
        s = unpack(STATIC_ID + ".tar.gz", STATIC, "studio-deployment.json", static["archive_sha256"])
        assert m["release_id"] == RELEASE_ID and s["release_id"] == STATIC_ID and not m["database_schema_changed"] and not m["worker_protocol_changed"]
        print(json.dumps({"extracted": [RELEASE_ID, STATIC_ID], "archive_hashes_verified": True}))
    elif action == "verify-installed":
        verify_installed()
    elif action in {"promote", "rehearse"}:
        assert (TASK / "prepare.state").read_text().strip() == "prepared"
        backup = check_backup()
        assert (BASE / "current").resolve() == (OLD if action == "promote" else RELEASE)
        steps = [switch(RELEASE)] if action == "promote" else [switch(OLD), switch(RELEASE)]
        result = {"passed": True, "action": action, "at": datetime.now(UTC).isoformat(), "backup": backup, "steps": steps, "database_migration_executed": False, "worker_restarted": False}
        (TASK / (action + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    elif action == "probe":
        print(json.dumps(db_probe()))
    else:
        raise SystemExit("Unknown fixed deployment action")


if __name__ == "__main__":
    main()
