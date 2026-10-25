"""Standalone, standard-library-only verifier and offline Windows candidate installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
import venv
import zipfile
from datetime import datetime
from pathlib import Path

MAX_BYTES = 1024**3
MAX_FILES = 10000
MANIFEST = "release-manifest.json"
PROJECTS = frozenset({"generation-domain", "generation-application", "generation-contracts", "generation-infrastructure", "render-control-api", "render-control-runner", "render-windows-worker"})
LEGACY_TASK_TYPES = ["text_to_image", "text_to_video", "image_to_image", "references_to_image", "references_to_video"]
TASK_TYPES = [*LEGACY_TASK_TYPES, "text_to_audio"]
UPSTREAM_COMMIT = "7da09cab4e875463baa79fc150adde700c5438df"


class ReleaseError(ValueError):
    """Public errors carry fixed codes, never configuration or child-process output."""


def digest_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def strict_json(data: bytes | str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ReleaseError("duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ReleaseError("nonfinite_json")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReleaseError("invalid_json") from exc


def safe_member(name: object) -> str:
    if not isinstance(name, str) or not 1 <= len(name) <= 240 or not name.isascii():
        raise ReleaseError("unsafe_member")
    if unicodedata.normalize("NFC", name) != name or "\\" in name or ":" in name:
        raise ReleaseError("unsafe_member")
    for part in name.split("/"):
        if part in ("", ".", "..") or part != part.strip() or part.endswith("."):
            raise ReleaseError("unsafe_member")
        if re.search(r'[\x00-\x1f\x7f<>"|?*]', part):
            raise ReleaseError("unsafe_member")
        if re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", part.split(".")[0], re.I):
            raise ReleaseError("unsafe_member")
    return name


def _keys(value: object, names: str) -> dict:
    if type(value) is not dict or set(value) != set(names.split()):
        raise ReleaseError("manifest_fields")
    return value


def _hash(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReleaseError("manifest_hash")


def _constant(value: object, expected: object) -> None:
    if type(value) is not type(expected) or value != expected:
        raise ReleaseError("manifest_constant")


def validate_manifest(value: object) -> dict:
    """Semantic counterpart of docs/contracts/release-manifest.schema.json."""
    data = _keys(value, "format_version release_id created_at platform application_version source_revision source_tree_sha256 python contracts database worker workflows gateway runtime files acceptance")
    for field, expected in (("format_version", 1), ("platform", "windows-amd64-cp313"), ("application_version", "0.1.0")):
        _constant(data[field], expected)
    if not isinstance(data["release_id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", data["release_id"]):
        raise ReleaseError("manifest_release_id")
    if not isinstance(data["created_at"], str):
        raise ReleaseError("manifest_timestamp")
    try:
        if datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ReleaseError("manifest_timestamp") from exc
    if data["source_revision"] is not None and (not isinstance(data["source_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", data["source_revision"])):
        raise ReleaseError("manifest_source_revision")
    _hash(data["source_tree_sha256"])
    python = _keys(data["python"], "implementation major minor build_version")
    for key, expected in (("implementation", "CPython"), ("major", 3), ("minor", 13)):
        _constant(python[key], expected)
    if not isinstance(python["build_version"], str) or not re.fullmatch(r"3\.13\.\d+", python["build_version"]):
        raise ReleaseError("manifest_python")
    contracts = _keys(data["contracts"], "version sha256")
    if contracts["version"] not in ("0.4.0", "0.5.0"):
        raise ReleaseError("manifest_contract_version")
    _hash(contracts["sha256"])
    database = _keys(data["database"], "head migrations_sha256 auto_migrate")
    if not isinstance(database["head"], str) or not re.fullmatch(r"[0-9a-f]{12}", database["head"]):
        raise ReleaseError("manifest_database")
    _hash(database["migrations_sha256"])
    _constant(database["auto_migrate"], False)
    worker = _keys(data["worker"], "protocols journal_source_sha256")
    _constant(worker["protocols"], ["0.4.0"])
    _hash(worker["journal_source_sha256"])
    workflows = _keys(data["workflows"], "required_task_types validated_releases")
    _constant(workflows["required_task_types"], TASK_TYPES if contracts["version"] == "0.5.0" else LEGACY_TASK_TYPES)
    _constant(workflows["validated_releases"], [])
    gateway = _keys(data["gateway"], "upstream_repository upstream_commit provider_binary_sha256 frontend_embedded")
    _constant(gateway["upstream_repository"], "https://github.com/Wei-Shaw/sub2api")
    _constant(gateway["upstream_commit"], UPSTREAM_COMMIT)
    _constant(gateway["frontend_embedded"], False)
    _hash(gateway["provider_binary_sha256"])
    runtime = _keys(data["runtime"], "requires_python lock_sha256 requirements_sha256")
    _constant(runtime["requires_python"], ">=3.13,<3.14")
    _hash(runtime["lock_sha256"])
    _hash(runtime["requirements_sha256"])
    acceptance = _keys(data["acceptance"], "production linux_runtime real_fleet real_workflows private_s3")
    for accepted in acceptance.values():
        _constant(accepted, False)
    if type(data["files"]) is not list or not 1 <= len(data["files"]) <= MAX_FILES:
        raise ReleaseError("manifest_files")
    seen: set[str] = set()
    total = 0
    for item in data["files"]:
        item = _keys(item, "path bytes sha256")
        name = safe_member(item["path"])
        if name == MANIFEST or name.casefold() in seen:
            raise ReleaseError("duplicate_member")
        seen.add(name.casefold())
        if type(item["bytes"]) is not int or not 0 <= item["bytes"] <= MAX_BYTES:
            raise ReleaseError("member_size")
        total += item["bytes"]
        _hash(item["sha256"])
    if total > MAX_BYTES:
        raise ReleaseError("bundle_size")
    for name in seen:
        parts = name.split("/")
        if any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            raise ReleaseError("member_parent_is_file")
    return data


def verify_archive(archive: Path, expected_sha256: str) -> dict:
    _hash(expected_sha256)
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > MAX_BYTES:
        raise ReleaseError("invalid_archive")
    if digest_file(archive) != expected_sha256:
        raise ReleaseError("archive_digest_mismatch")
    try:
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            if not 2 <= len(members) <= MAX_FILES + 1:
                raise ReleaseError("archive_file_count")
            seen: set[str] = set()
            total = 0
            for info in members:
                name = safe_member(info.filename)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFREG) or info.is_dir() or info.external_attr & 0x10 or info.flag_bits & 1:
                    raise ReleaseError("archive_special_member")
                if name.casefold() in seen:
                    raise ReleaseError("duplicate_member")
                seen.add(name.casefold())
                total += info.file_size
                if info.file_size > MAX_BYTES or info.file_size / max(1, info.compress_size) > 200:
                    raise ReleaseError("archive_size_limit")
            if total > MAX_BYTES + 4 * 1024**2 or MANIFEST not in source.namelist() or source.getinfo(MANIFEST).file_size > 4 * 1024**2:
                raise ReleaseError("archive_size_limit")
            manifest = validate_manifest(strict_json(source.read(MANIFEST)))
            inventory = {item["path"]: item for item in manifest["files"]}
            if set(inventory) | {MANIFEST} != set(source.namelist()):
                raise ReleaseError("archive_inventory_mismatch")
            for name, item in inventory.items():
                if source.getinfo(name).file_size != item["bytes"]:
                    raise ReleaseError("member_size_mismatch")
                with source.open(name) as member:
                    if hashlib.file_digest(member, "sha256").hexdigest() != item["sha256"]:
                        raise ReleaseError("member_digest_mismatch")
            return manifest
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, EOFError) as exc:
        raise ReleaseError("invalid_archive") from exc


def verify_payload(payload: Path, expected_manifest_sha256: str | None = None) -> dict:
    manifest_path = payload / MANIFEST
    if expected_manifest_sha256 is not None and digest_file(manifest_path) != expected_manifest_sha256:
        raise ReleaseError("manifest_digest_mismatch")
    manifest = validate_manifest(strict_json(manifest_path.read_bytes()))
    expected = {item["path"] for item in manifest["files"]} | {MANIFEST}
    actual: set[str] = set()
    for path in payload.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ReleaseError("payload_link")
        if path.is_file():
            actual.add(path.relative_to(payload).as_posix())
    if actual != expected:
        raise ReleaseError("payload_inventory_mismatch")
    for item in manifest["files"]:
        path = payload / item["path"]
        if path.stat().st_size != item["bytes"] or digest_file(path) != item["sha256"]:
            raise ReleaseError("payload_digest_mismatch")
    return manifest


def clean_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith(("PYTHON", "PIP_", "UV_", "GEN_")) and key.upper() not in ("VIRTUAL_ENV", "CONDA_PREFIX")}
    environment.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CONFIG_FILE": os.devnull})
    return environment


def verify_candidate(payload: Path, manifest: dict) -> None:
    """Check that provenance fields actually describe the authenticated payload."""
    contracts = strict_json((payload / "config/contract-hashes.json").read_bytes())
    sources = strict_json((payload / "provenance/source-files.json").read_bytes())
    if not isinstance(contracts, dict) or not 17 <= len(contracts) <= 128 or canonical_digest(contracts) != manifest["contracts"]["sha256"]:
        raise ReleaseError("candidate_contract_provenance")
    for name, digest in contracts.items():
        safe_member(name)
        _hash(digest)
        if "/" in name or not name.endswith(".json"):
            raise ReleaseError("candidate_contract_provenance")
    if not isinstance(sources, dict) or canonical_digest(sources) != manifest["source_tree_sha256"]:
        raise ReleaseError("candidate_source_provenance")
    for name, digest in sources.items():
        safe_member(name)
        _hash(digest)
    for path, expected in (("provenance/uv.lock", manifest["runtime"]["lock_sha256"]), ("runtime-requirements.txt", manifest["runtime"]["requirements_sha256"]), ("gateway/sub2api-provider.exe", manifest["gateway"]["provider_binary_sha256"])):
        if digest_file(payload / path) != expected:
            raise ReleaseError("candidate_file_provenance")
    migrations = {path.relative_to(payload).as_posix(): digest_file(path) for path in (payload / "migrations").rglob("*") if path.is_file()}
    if canonical_digest(migrations) != manifest["database"]["migrations_sha256"]:
        raise ReleaseError("candidate_migration_provenance")
    worker_wheels = list((payload / "wheels").glob("render_windows_worker-0.1.0-*.whl"))
    if len(worker_wheels) != 1:
        raise ReleaseError("candidate_worker_wheel")
    with zipfile.ZipFile(worker_wheels[0]) as wheel:
        if hashlib.sha256(wheel.read("windows_worker/journal.py")).hexdigest() != manifest["worker"]["journal_source_sha256"]:
            raise ReleaseError("candidate_journal_provenance")
    for path in ("admin/index.html", "gateway/LICENSE", "alembic.ini", "scripts/release-entrypoint.py", "scripts/migrate.py", "scripts/release_bundle.py"):
        if not (payload / path).is_file():
            raise ReleaseError("candidate_required_artifact")
    if manifest["contracts"]["version"] == "0.5.0":
        verify_media_runtime(payload)
    if any((payload / name).exists() for name in ("config/service-tools.lock.json", "scripts/native_services.py", "scripts/service-deploy.py", "scripts/service-process.py", "service-tools")):
        verify_service_runtime(payload)



SERVICE_TOOL_FILES = {
    "WinSW.NET461.exe": {"bytes": 655872, "sha256": "b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f"},
    "LICENSE.txt": {"bytes": 1158, "sha256": "1cdf703c10a70e5973bf3acf2a5eeabe7746237155b92db2034aeae26fdf7802"},
}

SERVICE_PYTHON_FILES = {"python-service.exe": "python.exe", "python313.dll": "python313.dll", "python3.dll": "python3.dll", "vcruntime140.dll": "vcruntime140.dll", "vcruntime140_1.dll": "vcruntime140_1.dll"}


def prepare_service_python(installation: Path) -> dict:
    """Copy the real host interpreter, avoiding the venv redirector process."""
    record = {"protocol": "host-cpython-service-v1", "python_version": sys.version.split()[0], "files": {}}
    for target_name, source_name in SERVICE_PYTHON_FILES.items():
        source = Path(sys.base_prefix) / source_name
        target = installation / "venv/Scripts" / target_name
        if source.is_symlink() or not source.is_file() or target.exists():
            raise ReleaseError("service_python_runtime_source_or_target")
        shutil.copyfile(source, target)
        record["files"][target_name] = {"bytes": target.stat().st_size, "sha256": digest_file(target)}
    return record


def verify_service_python(installation: Path, record: object) -> Path:
    value = _keys(record, "protocol python_version files")
    if value["protocol"] != "host-cpython-service-v1" or not isinstance(value["python_version"], str) or not re.fullmatch(r"3\.13\.\d+", value["python_version"]) or type(value["files"]) is not dict or set(value["files"]) != set(SERVICE_PYTHON_FILES):
        raise ReleaseError("service_python_receipt_invalid")
    for name, item in value["files"].items():
        item = _keys(item, "bytes sha256")
        _hash(item["sha256"])
        target = installation / "venv/Scripts" / name
        if any(path.exists() and path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT for path in (installation, installation / "venv", target.parent, target)):
            raise ReleaseError("service_python_runtime_reparse_point")
        if type(item["bytes"]) is not int or not 1 <= item["bytes"] <= 64 * 1024**2 or target.is_symlink() or not target.is_file() or target.stat().st_nlink != 1 or target.stat().st_size != item["bytes"] or digest_file(target) != item["sha256"]:
            raise ReleaseError("service_python_runtime_mismatch")
    return installation / "venv/Scripts/python-service.exe"


def verify_service_runtime(payload: Path) -> Path:
    """Authenticate the optional v85 fixed native service wrapper and its license."""
    lock = _keys(strict_json((payload / "config/service-tools.lock.json").read_bytes()), "protocol version platform source asset license digest_origin files")
    expected = {
        "protocol": "winsw-pin-v1", "version": "2.12.0", "platform": "windows-net461",
        "source": "https://github.com/winsw/winsw/releases/tag/v2.12.0",
        "asset": "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe",
        "license": "MIT", "digest_origin": "project-first-download-from-official-https-api-no-upstream-digest",
        "files": SERVICE_TOOL_FILES,
    }
    if lock != expected:
        raise ReleaseError("candidate_service_tool_pin")
    tools = payload / "service-tools"
    if tools.is_symlink() or not tools.is_dir() or {path.name for path in tools.iterdir()} != set(SERVICE_TOOL_FILES):
        raise ReleaseError("candidate_service_tool_inventory")
    for name, item in SERVICE_TOOL_FILES.items():
        target = tools / name
        if target.is_symlink() or not target.is_file() or target.stat().st_size != item["bytes"] or digest_file(target) != item["sha256"]:
            raise ReleaseError("candidate_service_tool_digest")
    for name in ("scripts/native_services.py", "scripts/service-deploy.py", "scripts/service-process.py", "deploy/native-windows/services.example.json"):
        if not (payload / name).is_file():
            raise ReleaseError("candidate_service_tool_entrypoint")
    return tools / "WinSW.NET461.exe"


def verify_media_runtime(payload: Path) -> Path:
    """Authenticate the bundled native decoder, probe and license before use."""
    lock = _keys(strict_json((payload / "config/media-tools.lock.json").read_bytes()), "format_version version platform source origin license files")
    for key, value in (("format_version", 1), ("version", "8.1-full_build-www.gyan.dev"), ("platform", "windows-amd64"), ("source", "https://www.gyan.dev/ffmpeg/builds/"), ("license", "GPL-3.0-or-later")):
        _constant(lock[key], value)
    if not isinstance(lock["origin"], str) or not 1 <= len(lock["origin"]) <= 512:
        raise ReleaseError("candidate_media_origin")
    files = _keys(lock["files"], "bin/ffmpeg.exe bin/ffprobe.exe LICENSE README.txt")
    for name, specification in files.items():
        item = _keys(specification, "bytes sha256")
        _hash(item["sha256"])
        target = payload / "media" / name
        if type(item["bytes"]) is not int or not 0 < item["bytes"] <= MAX_BYTES or target.is_symlink() or not target.is_file() or target.stat().st_size != item["bytes"] or digest_file(target) != item["sha256"]:
            raise ReleaseError("candidate_media_provenance")
    return payload / "media/bin"


def remove_created(destination: Path, parent: Path) -> None:
    """Delete only this invocation's newly created, resolved child, in this shell."""
    if destination.is_symlink() or destination.resolve().parent != parent.resolve() or not destination.name or destination == parent:
        raise ReleaseError("cleanup_ownership_mismatch")
    shutil.rmtree(destination)


def _requirements(payload: Path, manifest: dict) -> list[tuple[str, str]]:
    path = payload / "runtime-requirements.txt"
    if digest_file(path) != manifest["runtime"]["requirements_sha256"]:
        raise ReleaseError("requirements_digest_mismatch")
    result: list[tuple[str, str]] = []
    for line in path.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([a-z0-9][a-z0-9-]*)==([a-zA-Z0-9.+!_-]+) --hash=sha256:([0-9a-f]{64})", line)
        if not match:
            raise ReleaseError("invalid_locked_requirement")
        name, version, _ = match.groups()
        if name in {item[0] for item in result}:
            raise ReleaseError("duplicate_requirement")
        result.append((name, version))
    if not PROJECTS <= {item[0] for item in result}:
        raise ReleaseError("missing_application_wheel")
    return result


def install_archive(archive: Path, expected_sha256: str, destination: Path) -> dict:
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13) or sys.platform != "win32" or sys.maxsize <= 2**32:
        raise ReleaseError("unsupported_installer_platform")
    manifest = verify_archive(archive, expected_sha256)
    # Never resolve an existing target through a junction and accidentally overwrite it.
    if destination.exists() or destination.is_symlink():
        raise ReleaseError("destination_exists")
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination.name in ("", ".", ".."):
        raise ReleaseError("invalid_destination")
    destination.mkdir(exist_ok=False)
    try:
        payload = destination / "payload"
        payload.mkdir()
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                target = payload / safe_member(info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(info) as member, target.open("xb") as output:
                    shutil.copyfileobj(member, output)
        extracted = verify_payload(payload)
        if extracted != manifest or digest_file(archive) != expected_sha256:
            raise ReleaseError("archive_changed_during_install")
        requirements = _requirements(payload, manifest)
        verify_candidate(payload, manifest)
        venv.EnvBuilder(with_pip=True, symlinks=False).create(destination / "venv")
        python = destination / "venv/Scripts/python.exe"
        service_python = prepare_service_python(destination) if (payload / "config/service-tools.lock.json").is_file() else None
        subprocess.run([str(python), "-I", "-m", "pip", "--isolated", "install", "--no-index", "--find-links", str(payload / "wheels"), "--only-binary=:all:", "--no-deps", "--require-hashes", "-r", str(payload / "runtime-requirements.txt")], env=clean_environment(), cwd=destination, check=True, timeout=300, capture_output=True)  # noqa: S603 -- fixed offline installer, authenticated inventory and locked hashes
        probe = """import hashlib,importlib.metadata as m,importlib.util as u,json,sys
from importlib.resources import files
from pathlib import Path
payload=Path(sys.argv[1])
modules={name:str(Path(u.find_spec(name).origin).resolve()) for name in ('generation_domain','generation_application','generation_contracts','generation_infrastructure','control_api','control_runner','windows_worker')}
assert all(Path(sys.prefix).resolve() in Path(path).parents for path in modules.values())
hashes=json.loads((payload/'config/contract-hashes.json').read_text())
for name,expected in hashes.items():
 package='generation_domain' if name=='job-state-machine.json' else 'generation_contracts'
 relative=name if name in ('job-state-machine.json','endpoint-catalog.json') else 'schemas/'+name
 assert hashlib.sha256(files(package).joinpath(relative).read_bytes()).hexdigest()==expected
assert not any(json.loads(d.read_text('direct_url.json') or '{}').get('dir_info',{}).get('editable') for d in m.distributions())
print(json.dumps({'prefix':sys.prefix,'modules':list(modules),'contracts_verified':len(hashes),'distributions':{d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions()}}))"""
        completed = subprocess.run([str(python), "-I", "-c", probe, str(payload)], env=clean_environment(), cwd=destination, check=True, timeout=30, capture_output=True, text=True)  # noqa: S603 -- fixed local installed-distribution and packaged-contract probe
        installed = json.loads(completed.stdout)
        if service_python is not None:
            service_executable = verify_service_python(destination, service_python)
            service_probe = subprocess.run([str(service_executable), "-I", "-c", probe, str(payload)], env=clean_environment(), cwd=destination, check=True, timeout=30, capture_output=True, text=True)  # noqa: S603 -- freshly copied real CPython host runtime, same isolated installed-package probe
            service_installed = json.loads(service_probe.stdout)
            if service_installed != installed:
                raise ReleaseError("service_python_installed_probe_mismatch")
        if Path(installed["prefix"]).resolve() != (destination / "venv").resolve() or any(installed["distributions"].get(name) != version for name, version in requirements):
            raise ReleaseError("installed_distribution_mismatch")
        subprocess.run([str(python), "-I", "-m", "pip", "--isolated", "check"], env=clean_environment(), cwd=destination, check=True, timeout=30, capture_output=True)  # noqa: S603 -- fixed offline dependency compatibility check
        result = {"format_version": 1, "release_id": manifest["release_id"], "archive_sha256": expected_sha256, "manifest_sha256": digest_file(payload / MANIFEST), "python_version": sys.version.split()[0], "requirements": len(requirements), "application_modules": installed["modules"], "contracts_verified": installed["contracts_verified"], "offline": True, "editable": False}
        if service_python is not None:
            result["service_python"] = service_python
        (destination / "install.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        verify_payload(payload, result["manifest_sha256"])
        return result
    except BaseException:
        remove_created(destination, parent)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "install"))
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--sha256", required=True, help="Expected digest obtained through the trusted delivery channel")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "install":
            if args.destination is None:
                parser.error("install requires --destination")
            result = install_archive(args.archive.resolve(), args.sha256, args.destination.absolute())
        else:
            manifest = verify_archive(args.archive.resolve(), args.sha256)
            result = {"release_id": manifest["release_id"], "verified": True, "files": len(manifest["files"])}
        print(json.dumps(result, sort_keys=True))
    except (ReleaseError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, ReleaseError) else "release_operation_failed"}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
