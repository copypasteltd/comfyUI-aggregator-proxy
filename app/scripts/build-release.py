"""Build a native Windows candidate from locked wheels and explicit artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import venv
import zipfile
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path

from release_bundle import (
    PROJECTS,
    TASK_TYPES,
    UPSTREAM_COMMIT,
    canonical_digest,
    digest_file,
    remove_created,
    validate_manifest,
    verify_archive,
    verify_candidate,
    verify_media_runtime,
    verify_service_runtime,
)

ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str], log: Path, *, environment: dict[str, str]) -> None:
    with log.open("ab") as output:
        subprocess.run(arguments, cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=600, check=True)  # noqa: S603 -- fixed native build/download tools, explicit destinations


def copy(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError("Artifact source must be a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as wheel:
        names = [name for name in wheel.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
        if len(names) != 1:
            raise ValueError("Invalid wheel metadata")
        data = BytesParser().parsebytes(wheel.read(names[0]))
    return re.sub(r"[-_.]+", "-", data["Name"]).lower(), data["Version"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", args.release_id):
        parser.error("Invalid candidate release ID")
    if sys.platform != "win32" or sys.version_info[:2] != (3, 13) or sys.maxsize <= 2**32:
        parser.error("Build requires native Windows amd64 CPython 3.13")
    output = ROOT / ".local/releases" / args.release_id
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    stage = output / "payload"
    stage.mkdir()
    environment = {**os.environ, "UV_CACHE_DIR": str(ROOT / ".local/uv-cache"), "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("PYTHONPATH", None)
    uv = ROOT / ".venv/Scripts/uv.exe"
    python = str(Path(sys.base_prefix) / "python.exe")
    log = output / "build.log"
    try:
        run([str(uv), "lock", "--check"], log, environment=environment)
        tools = output / "build-tools"
        venv.EnvBuilder(with_pip=False, symlinks=False).create(tools)
        tool_python = tools / "Scripts/python.exe"
        tool_requirements = output / "build-requirements.txt"
        runtime_export = output / "runtime-export.txt"
        run([str(uv), "export", "--locked", "--only-group", "release-build", "--no-emit-workspace", "--no-header", "--output-file", str(tool_requirements)], log, environment=environment)
        run([str(uv), "pip", "sync", "--python", str(tool_python), "--require-hashes", "--index-url", "https://pypi.org/simple", str(tool_requirements)], log, environment=environment)
        wheels = stage / "wheels"
        wheels.mkdir()
        run([str(uv), "build", "--all-packages", "--wheel", "--no-build-isolation", "--offline", "--python", str(tool_python), "--out-dir", str(wheels)], log, environment=environment)
        run([str(uv), "export", "--locked", "--all-packages", "--no-default-groups", "--no-emit-workspace", "--no-header", "--output-file", str(runtime_export)], log, environment=environment)
        run([python, "-I", "-m", "pip", "--isolated", "download", "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--no-deps", "--require-hashes", "-r", str(runtime_export), "--dest", str(wheels)], log, environment=environment)
        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        locked = {(package["name"], package["version"]): {item["hash"].removeprefix("sha256:") for item in package.get("wheels", [])} for package in lock["package"]}
        requirements: dict[str, str] = {}
        for wheel in sorted(wheels.glob("*.whl")):
            name, version = wheel_metadata(wheel)
            digest = digest_file(wheel)
            if name in requirements or (name in PROJECTS and version != "0.1.0"):
                raise ValueError("Duplicate or unversioned application wheel")
            if name not in PROJECTS and digest not in locked.get((name, version), set()):
                raise ValueError("Runtime wheel does not match uv.lock")
            requirements[name] = f"{name}=={version} --hash=sha256:{digest}"
        if not PROJECTS <= set(requirements):
            raise ValueError("Seven application wheels required")
        (stage / "runtime-requirements.txt").write_text("\n".join(value for _, value in sorted(requirements.items())) + "\n", encoding="ascii")
        sources: dict[str, str] = {}
        for folder in ("packages", "apps/control_api", "apps/control_runner", "apps/windows_worker", "migrations", "config", "scripts", "integrations/sub2api", "deploy"):
            for path in sorted((ROOT / folder).rglob("*")):
                if path.is_file() and path.suffix in (".py", ".toml", ".json", ".ini", ".mako", ".go", ".mod", ".sum", ".ps1", ".sql", ".yml", ".yaml", ".example") and "__pycache__" not in path.parts:
                    sources[path.relative_to(ROOT).as_posix()] = digest_file(path)
        for path in sorted((ROOT / "apps/admin_web/src").rglob("*")):
            if path.is_file():
                sources[path.relative_to(ROOT).as_posix()] = digest_file(path)
        for name in ("pyproject.toml", "uv.lock", "alembic.ini", "apps/admin_web/package-lock.json", "apps/admin_web/package.json", "apps/admin_web/index.html", "apps/admin_web/vite.config.ts", "apps/admin_web/tsconfig.json"):
            sources[name] = digest_file(ROOT / name)
        for path in sorted((ROOT / "apps/admin_web/dist").rglob("*")):
            if path.is_file():
                relative = path.relative_to(ROOT / "apps/admin_web/dist")
                if path.suffix not in (".html", ".js", ".css", ".svg", ".png", ".ico", ".woff2"):
                    raise ValueError("Unexpected Admin build artifact")
                copy(path, stage / "admin" / relative)
        if not (stage / "admin/index.html").is_file():
            raise ValueError("Build Admin production assets before packaging")
        for path in (ROOT / "migrations").rglob("*"):
            if path.is_file() and path.suffix in (".py", ".mako"):
                copy(path, stage / path.relative_to(ROOT))
        for name in ("alembic.ini", "scripts/release_bundle.py", "scripts/release-entrypoint.py", "scripts/native_services.py", "scripts/service-deploy.py", "scripts/service-process.py", "scripts/migrate.py", "scripts/verify-recovery.py", "config/environment.example", "config/worker.example.json", "config/worker.workflow-pull.example.json", "config/contract-hashes.json", "integrations/sub2api/upstream-lock.json"):
            copy(ROOT / name, stage / name)
        copy(ROOT / "packages/contracts/src/generation_contracts/schemas/release-manifest.schema.json", stage / "config/release-manifest.schema.json")
        for name in ("prometheus.example.yml", "monitoring.env.example"):
            copy(ROOT / "deploy/observability" / name, stage / "deploy/observability" / name)
        media_lock = json.loads((ROOT / "config/media-tools.lock.json").read_text(encoding="utf-8"))
        copy(ROOT / "config/media-tools.lock.json", stage / "config/media-tools.lock.json")
        for name in media_lock["files"]:
            copy(ROOT / ".local/toolchains/ffmpeg-8.1" / name, stage / "media" / name)
        verify_media_runtime(stage)
        copy(ROOT / "config/service-tools.lock.json", stage / "config/service-tools.lock.json")
        copy(ROOT / "deploy/native-windows/services.example.json", stage / "deploy/native-windows/services.example.json")
        service_lock = json.loads((ROOT / "config/service-tools.lock.json").read_text(encoding="utf-8"))
        for name in service_lock["files"]:
            copy(ROOT / ".local/toolchains/winsw-2.12.0" / name, stage / "service-tools" / name)
        verify_service_runtime(stage)
        copy(ROOT / "uv.lock", stage / "provenance/uv.lock")
        (stage / "provenance/source-files.json").write_text(json.dumps(sources, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        backend = ROOT / ".local/bin/sub2api-provider.exe"
        previous = json.loads((ROOT.parent / "docs/evidence" / datetime.now().date().isoformat() / "sub2api-provider-native-build.json").read_text(encoding="utf-8"))
        marker = json.loads((ROOT / ".local/sub2api-overlay-workspace.json").read_text(encoding="utf-8"))
        if not previous["passed"] or not marker["passed"] or previous["binary_sha256"] != digest_file(backend) or previous["source_commit"] != UPSTREAM_COMMIT or previous["frontend_embedded"]:
            raise ValueError("Native Provider provenance mismatch")
        generated = Path(marker["backend"])
        for name, digest in previous["overlay_files_sha256"].items():
            if digest_file(generated / name) != digest:
                raise ValueError("Native Provider overlay drift")
        copy(backend, stage / "gateway/sub2api-provider.exe")
        licenses = list((ROOT / ".local/upstream").glob("*/LICENSE"))
        if len(licenses) != 1:
            raise ValueError("Pinned upstream license must be located unambiguously")
        copy(licenses[0], stage / "gateway/LICENSE")
        (stage / "provenance/provider-build.json").write_text(json.dumps({key: previous[key] for key in ("source_commit", "source_archive_sha256", "binary_sha256", "overlay_files_sha256", "frontend_embedded")}, indent=2) + "\n", encoding="utf-8")
        migrations = {path.relative_to(stage).as_posix(): digest_file(path) for path in (stage / "migrations").rglob("*") if path.is_file()}
        revisions = []
        parents = set()
        for path in (ROOT / "migrations/versions").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            revisions.extend(re.findall(r'^revision(?:\s*:\s*[^=]+)?\s*=\s*["\x27]([0-9a-f]{12})', text, re.M))
            parents.update(re.findall(r'^down_revision(?:\s*:\s*[^=]+)?\s*=\s*["\x27]([0-9a-f]{12})', text, re.M))
        heads = set(revisions) - parents
        if len(heads) != 1:
            raise ValueError("A single migration head is required")
        files = [{"path": path.relative_to(stage).as_posix(), "bytes": path.stat().st_size, "sha256": digest_file(path)} for path in sorted(stage.rglob("*")) if path.is_file()]
        manifest = validate_manifest({"format_version": 1, "release_id": args.release_id, "created_at": datetime.now(UTC).isoformat(), "platform": "windows-amd64-cp313", "application_version": "0.1.0", "source_revision": None, "source_tree_sha256": canonical_digest(sources), "python": {"implementation": "CPython", "major": 3, "minor": 13, "build_version": sys.version.split()[0]}, "contracts": {"version": "0.5.0", "sha256": canonical_digest(json.loads((ROOT / "config/contract-hashes.json").read_text()))}, "database": {"head": heads.pop(), "migrations_sha256": canonical_digest(migrations), "auto_migrate": False}, "worker": {"protocols": ["0.4.0"], "journal_source_sha256": digest_file(ROOT / "apps/windows_worker/src/windows_worker/journal.py")}, "workflows": {"required_task_types": TASK_TYPES, "validated_releases": []}, "gateway": {"upstream_repository": "https://github.com/Wei-Shaw/sub2api", "upstream_commit": UPSTREAM_COMMIT, "provider_binary_sha256": digest_file(backend), "frontend_embedded": False}, "runtime": {"requires_python": ">=3.13,<3.14", "lock_sha256": digest_file(ROOT / "uv.lock"), "requirements_sha256": digest_file(stage / "runtime-requirements.txt")}, "files": files, "acceptance": {key: False for key in ("production", "linux_runtime", "real_fleet", "real_workflows", "private_s3")}})
        (stage / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        verify_candidate(stage, manifest)
        archive = output / (args.release_id + ".zip")
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(stage).as_posix())
        digest = digest_file(archive)
        verify_archive(archive, digest)
        (output / (args.release_id + ".sha256")).write_text(digest + "  " + archive.name + "\n", encoding="ascii")
        copy(ROOT / "scripts/release_bundle.py", output / "release_bundle.py")
        remove_created(stage, output)
        remove_created(tools, output)
        print(json.dumps({"passed": True, "release_id": args.release_id, "archive": str(archive), "archive_sha256": digest, "files": len(files), "wheels": len(requirements), "production_accepted": False}))
    except BaseException:
        (output / "FAILED").write_text("Build failed; candidate must not be installed. Use a new release ID.\n", encoding="ascii")
        raise


if __name__ == "__main__":
    main()
