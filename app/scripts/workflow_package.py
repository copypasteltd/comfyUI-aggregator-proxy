"""Prepare immutable Workflow inputs from explicitly selected native local files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json
from generation_domain.workflow_imports import MAX_WORKFLOW_BYTES, parse_source
from generation_domain.workflows import relative_file, validate_manifest
from pydantic import ValidationError

FILES = frozenset({"manifest.json", "provenance.json", "checksums.json"})
MAX_MODEL_BYTES = 64 * 1024**3
MAX_DEPENDENCIES = 2000


class PackageError(ValueError):
    """Fixed, non-sensitive failure codes for the command-line boundary."""


def plain_path(path: Path) -> Path:
    path = path.absolute()
    if str(path).startswith("\\\\") or any(ord(c) < 32 for c in str(path)) or ".." in path.parts:
        raise PackageError("local_plain_path_required")
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise PackageError("linked_path_not_allowed")
    return path


def relative_path(root: Path, value: str) -> Path:
    relative_file(value)
    if len(value) > 512 or any(
        p[-1] in (".", " ") or re.search(r'[<>"|?*\x00-\x1f]', p)
        or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\..*)?", p)
        or p.casefold() == ".git"
        for p in value.split("/")
    ):
        raise PackageError("invalid_dependency_path")
    root = plain_path(root)
    target = plain_path(root / value)
    if not target.is_relative_to(root) or target == root:
        raise PackageError("dependency_path_escape")
    return target


def read_bytes(path: Path, maximum: int = MAX_WORKFLOW_BYTES) -> bytes:
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise PackageError("document_size_or_type_invalid")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise PackageError("document_size_or_type_invalid")
    return raw


def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in entries:
        if key in result:
            raise PackageError("duplicate_document_key")
        result[key] = value
    return result


def read_document(path: Path) -> dict[str, Any]:
    value = json.loads(read_bytes(path), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise PackageError("document_object_required")
    return value


def revision(root: Path) -> str:
    root = plain_path(root)
    # Requiring the repository marker prevents rev-parse finding an ancestor repo.
    plain_path(root / ".git")
    git = shutil.which("git")
    if not git:
        raise PackageError("native_git_required")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # Preserve native system/user line-ending policy; disabling it makes clean
    # Windows checkouts appear modified. Suppress locks/prompts and fsmonitor.
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")

    def run(arguments: list[str]) -> str:
        result = subprocess.run(  # noqa: S603 -- fixed read-only local Git operations; no shell/network/hooks
            [git, "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
            env=env, capture_output=True, check=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.stdout.decode("utf-8").strip()

    commit = run(["rev-parse", "--verify", "HEAD"])
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise PackageError("unsupported_repository_revision")
    if run(["status", "--porcelain=v1", "--untracked-files=normal"]):
        raise PackageError("dependency_repository_dirty")
    return commit


def hash_model(path: Path, maximum: int) -> tuple[str, int]:
    path = plain_path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise PackageError("model_size_or_type_invalid")
        size = 0
        while block := stream.read(1024 * 1024):
            size += len(block)
            if size > maximum:
                raise PackageError("model_size_or_type_invalid")
            digest.update(block)
        after = os.fstat(stream.fileno())
    current = plain_path(path).stat()
    def identity(info: os.stat_result) -> tuple[int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    # On native Windows fstat ctime is change time while path stat ctime may be
    # creation time. Compare it only between observations of the same handle.
    if identity(before) != identity(after) or identity(after) != identity(current) or before.st_ctime_ns != after.st_ctime_ns or size != before.st_size:
        raise PackageError("model_changed_during_read")
    return digest.hexdigest(), size


def lock_dependencies(root: Path, declared: Any, maximum: int) -> dict[str, Any]:
    root = plain_path(root)
    if not isinstance(declared, dict) or set(declared) - {"comfy_commit", "models", "custom_nodes"}:
        raise PackageError("dependency_declaration_invalid")
    models, nodes = declared.get("models", []), declared.get("custom_nodes", [])
    if not isinstance(models, list) or not isinstance(nodes, list) or len(models) + len(nodes) > MAX_DEPENDENCIES:
        raise PackageError("dependency_declaration_invalid")
    commit = revision(root)
    locked_models, locked_nodes = [], []
    seen: set[str] = set()
    for item in models:
        if not isinstance(item, dict) or set(item) - {"path", "sha256", "bytes"} or not isinstance(item.get("path"), str):
            raise PackageError("dependency_declaration_invalid")
        key = "model/" + item["path"].casefold()
        if key in seen:
            raise PackageError("duplicate_dependency")
        seen.add(key)
        digest, size = hash_model(relative_path(root / "models", item["path"]), maximum)
        locked_models.append({"path": item["path"], "sha256": digest, "bytes": size})
    for item in nodes:
        if not isinstance(item, dict) or set(item) - {"directory", "commit"} or not isinstance(item.get("directory"), str):
            raise PackageError("dependency_declaration_invalid")
        key = "node/" + item["directory"].casefold()
        if key in seen:
            raise PackageError("duplicate_dependency")
        seen.add(key)
        locked_nodes.append({"directory": item["directory"], "commit": revision(relative_path(root / "custom_nodes", item["directory"]))})
    if revision(root) != commit:
        raise PackageError("dependency_revision_changed")
    return {"comfy_commit": commit, "models": locked_models, "custom_nodes": locked_nodes}


def validate_document(document: dict[str, Any]) -> dict[str, Any]:
    WorkflowManifest.model_validate(document)
    return validate_manifest(document, document["task_type"])


def build(source: Path, root: Path, output: Path, *, maximum: int = MAX_MODEL_BYTES) -> dict[str, Any]:
    if type(maximum) is not int or not 1 <= maximum <= 1024**4:
        raise PackageError("model_byte_limit_invalid")
    source_raw = read_bytes(source)
    source_document, kind = parse_source(source_raw)
    if kind != "manifest-v1":
        raise PackageError("manifest_draft_required")
    document = deepcopy(source_document)
    document["dependency_lock"] = lock_dependencies(root, document.get("dependency_lock"), maximum)
    for profile in document.get("runtime_profiles", []):
        if not isinstance(profile, dict):
            raise PackageError("runtime_profile_invalid")
        profile.update(approved=False, evidence_id=None, peak_vram_bytes=None, warm_p95_milliseconds=None)
    result = validate_document(document)
    raw = (canonical_json(document) + "\n").encode("utf-8")
    # Apply exactly the same size/lossless parser used by the Admin import route.
    parse_source(raw)
    provenance = {
        "format_version": 1, "at": datetime.now(UTC).isoformat(),
        "task_type": document["task_type"], "manifest_digest": result["digest"],
        "source_sha256": hashlib.sha256(source_raw).hexdigest(),
        "declared_dependencies_verified": True, "dependency_completeness_verified": False,
        "runtime_verified": False, "production_approved": False,
        "model_count": len(document["dependency_lock"]["models"]),
        "custom_node_count": len(document["dependency_lock"]["custom_nodes"]),
    }
    payload = {"manifest.json": raw, "provenance.json": (canonical_json(provenance) + "\n").encode("utf-8")}
    checksums = {name: hashlib.sha256(content).hexdigest() for name, content in payload.items()}
    payload["checksums.json"] = (canonical_json(checksums) + "\n").encode("utf-8")
    output = output.absolute()
    plain_path(output.parent)
    output.mkdir(exist_ok=False)
    # The completion index is written last. An interrupted package fails verify.
    # No overwrite, weight copying, automatic cleanup or recursive deletion.
    for name, content in payload.items():
        with (output / name).open("xb") as stream:
            stream.write(content)
    return summary(document, result["digest"], dependencies=True)


def summary(document: dict[str, Any], digest: str, *, dependencies: bool) -> dict[str, Any]:
    return {"passed": True, "task_type": document["task_type"], "manifest_digest": digest,
            "models": len(document["dependency_lock"]["models"]),
            "custom_nodes": len(document["dependency_lock"]["custom_nodes"]),
            "local_dependencies_checked": dependencies, "runtime_verified": False, "production_approved": False}


def verify(package: Path, root: Path | None = None, *, maximum: int = MAX_MODEL_BYTES) -> dict[str, Any]:
    if type(maximum) is not int or not 1 <= maximum <= 1024**4:
        raise PackageError("model_byte_limit_invalid")
    package = plain_path(package)
    if {p.name for p in package.iterdir()} != FILES:
        raise PackageError("package_members_invalid")
    checksums = read_document(package / "checksums.json")
    if set(checksums) != FILES - {"checksums.json"}:
        raise PackageError("package_index_invalid")
    for name, expected in checksums.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected) or hashlib.sha256(read_bytes(package / name)).hexdigest() != expected:
            raise PackageError("package_digest_mismatch")
    document, kind = parse_source(read_bytes(package / "manifest.json"))
    if kind != "manifest-v1":
        raise PackageError("manifest_draft_required")
    result = validate_document(document)
    provenance = read_document(package / "provenance.json")
    if provenance.get("format_version") != 1 or provenance.get("manifest_digest") != result["digest"] or provenance.get("task_type") != document["task_type"] or provenance.get("declared_dependencies_verified") is not True or any(provenance.get(k) is not False for k in ("dependency_completeness_verified", "runtime_verified", "production_approved")):
        raise PackageError("package_provenance_invalid")
    if any(p.get("approved") is not False or any(p.get(k) is not None for k in ("evidence_id", "peak_vram_bytes", "warm_p95_milliseconds")) for p in document["runtime_profiles"]):
        raise PackageError("offline_runtime_approval_invalid")
    if root is not None and lock_dependencies(root, document["dependency_lock"], maximum) != document["dependency_lock"]:
        raise PackageError("local_dependency_drift")
    return summary(document, result["digest"], dependencies=root is not None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("build", help="Lock a manifest draft to existing local ComfyUI dependencies")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--comfy-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("verify", help="Verify a package; optionally recheck actual local dependencies")
    check.add_argument("--package", type=Path, required=True)
    check.add_argument("--comfy-root", type=Path)
    for command in (prepare, check):
        command.add_argument("--max-model-bytes", type=int, default=MAX_MODEL_BYTES)
    args = parser.parse_args()
    try:
        result = build(args.manifest, args.comfy_root, args.output, maximum=args.max_model_bytes) if args.command == "build" else verify(args.package, args.comfy_root, maximum=args.max_model_bytes)
    except PackageError as exc:
        result = {"passed": False, "error": str(exc)}
    except (DomainError, ValidationError, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        result = {"passed": False, "error": "workflow_package_input_or_environment_invalid"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["passed"] else 1
