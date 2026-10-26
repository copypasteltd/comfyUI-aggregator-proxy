"""Derive a Linux control-plane bundle from a verified portable release payload.

Does not install services, copy credentials, or carry Windows executables/wheels.
Linux dependency installation still requires the exported hashes and native checks.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rebuild-wheels", action="store_true", help="Build all project wheels from clean committed sources; Admin sources must still match the seed")
    parser.add_argument("--git", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.output.exists():
        raise SystemExit("Output already exists")
    with args.candidate.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != args.candidate_sha256:
            raise SystemExit("Candidate digest mismatch")
    version = subprocess.run([str(args.uv), "--version"], check=True, capture_output=True, text=True).stdout  # noqa: S603 -- fixed native operation with trusted deployment configuration
    if not version.startswith("uv 0.13.5 "):
        raise SystemExit("This Linux deployment uses uv 0.13.5")
    exported = subprocess.run(  # noqa: S603 -- fixed native operation with trusted deployment configuration
        [str(args.uv), "export", "--frozen", "--no-dev", "--no-emit-workspace", "--no-header", "--no-annotate", "--format", "requirements-txt"],
        cwd=root, check=True, capture_output=True,
    ).stdout
    payload: dict[str, bytes] = {}
    revision = None
    current_sources = {}
    if args.rebuild_wheels:
        if args.git is None:
            raise SystemExit("Rebuilding requires an explicit native Git binary")
        git = [str(args.git), "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]
        status = subprocess.run([*git, "status", "--porcelain", "--untracked-files=normal", "--", "app"], cwd=root.parent, check=True, capture_output=True).stdout  # noqa: S603 -- fixed native operation with trusted deployment configuration
        if status.strip():
            raise SystemExit("Commit application changes before building Linux wheels")
        revision = subprocess.run([*git, "rev-parse", "HEAD"], cwd=root.parent, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603 -- fixed native operation with trusted deployment configuration
        tracked = subprocess.run([*git, "ls-files", "-z", "--", "app"], cwd=root.parent, check=True, capture_output=True).stdout  # noqa: S603 -- fixed native operation with trusted deployment configuration
        for name in tracked.decode("utf-8").split("\0"):
            if name:
                current_sources[name.removeprefix("app/")] = digest((root.parent / name).read_bytes())
    wheel_root = None
    if args.rebuild_wheels:
        wheel_root = tempfile.TemporaryDirectory(prefix="linux-project-wheels-", dir=root / ".local")
        subprocess.run([str(args.uv), "build", "--all-packages", "--wheel", "--no-build-isolation", "--out-dir", wheel_root.name], cwd=root, check=True, capture_output=True)  # noqa: S603 -- fixed native operation with trusted deployment configuration
    changed_sources = []
    with zipfile.ZipFile(args.candidate) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise SystemExit("Duplicate archive members")
        manifest_bytes = archive.read("release-manifest.json")
        manifest = json.loads(manifest_bytes)
        sources = json.loads(archive.read("provenance/source-files.json"))
        for relative, expected in sources.items():
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or "\\" in relative:
                raise SystemExit("Unsafe source path")
            if digest((root / relative).read_bytes()) != expected:
                portable_change = relative.startswith(("packages/", "apps/control_api/", "apps/control_runner/", "apps/windows_worker/")) or relative == "config/environment.example"
                if not args.rebuild_wheels or not portable_change:
                    raise SystemExit(f"Candidate source changed: {relative}")
                changed_sources.append(relative)
        lock = (root / "uv.lock").read_bytes()
        if digest(lock) != manifest["runtime"]["lock_sha256"]:
            raise SystemExit("Runtime lock changed")
        projects = ("generation_application", "generation_contracts", "generation_domain", "generation_infrastructure", "render_control_api", "render_control_runner", "render_windows_worker")
        wheel_lines = []
        for record in manifest["files"]:
            name = record["path"]
            portable_wheel = name.startswith("wheels/") and any(PurePosixPath(name).name.startswith(p + "-") for p in projects)
            include = name.startswith(("admin/", "migrations/", "contracts/")) or name in ("alembic.ini", "scripts/migrate.py", "provenance/source-files.json") or portable_wheel
            if not include:
                continue
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise SystemExit("Unsafe payload path")
            data = archive.read(name)
            if len(data) != record["bytes"] or digest(data) != record["sha256"]:
                raise SystemExit(f"Payload digest mismatch: {name}")
            if portable_wheel:
                if not name.endswith("-py3-none-any.whl"):
                    raise SystemExit("Project wheel is not portable")
                if wheel_root is not None:
                    data = (Path(wheel_root.name) / path.name).read_bytes()
                package, version_number = path.name.split("-")[:2]
                wheel_lines.append(f"{package}=={version_number} --hash=sha256:{digest(data)}")
            payload[name] = data
        if len(wheel_lines) != len(projects):
            raise SystemExit("Expected all seven portable project wheels")
    payload["provenance/uv.lock"] = lock
    payload["provenance/seed-release-manifest.json"] = manifest_bytes
    if current_sources:
        payload["provenance/current-source-files.json"] = (json.dumps(current_sources, indent=2) + "\n").encode()
    payload["linux-runtime-requirements.txt"] = exported
    payload["project-requirements.txt"] = ("\n".join(sorted(wheel_lines)) + "\n").encode()
    for name, expected in current_sources.items():
        if digest((root / name).read_bytes()) != expected:
            raise SystemExit("Source changed during Linux build")
    deployment = {
        "format_version": 1, "platform": "linux-amd64-cp313", "created_at": datetime.now(UTC).isoformat(),
        "application_source_revision": revision or manifest["source_revision"],
        "seed_release_id": manifest["release_id"], "seed_archive_sha256": args.candidate_sha256,
        "seed_source_files_checked": len(sources), "unchanged_seed_source_files": len(sources) - len(changed_sources), "current_source_files_recorded": len(current_sources), "database_head": manifest["database"]["head"],
        "project_wheels_rebuilt": args.rebuild_wheels, "changed_seed_sources": changed_sources,
        "uv": "0.13.5", "python": "3.13.1", "auto_migrate": False,
        "gateway_included": False, "worker_service_included": False,
        "files": [{"path": name, "bytes": len(data), "sha256": digest(data)} for name, data in sorted(payload.items())],
    }
    payload["linux-deployment.json"] = (json.dumps(deployment, indent=2) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w:gz", format=tarfile.PAX_FORMAT) as target:
        for name, data in sorted(payload.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            target.addfile(info, io.BytesIO(data))
    with args.output.open("rb") as source:
        print(json.dumps({"archive": args.output.name, "sha256": hashlib.file_digest(source, "sha256").hexdigest(), "bytes": args.output.stat().st_size, "files": len(payload), "seed_source_files_checked": len(sources), "changed_seed_sources": changed_sources}))
    if wheel_root is not None:
        wheel_root.cleanup()


if __name__ == "__main__":
    main()
