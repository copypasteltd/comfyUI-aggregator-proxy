"""Bind a native candidate's selected source bytes to a stable Git commit."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from release_bundle import ReleaseError, digest_file

SOURCE_FOLDERS = ("packages", "apps/control_api", "apps/control_runner", "apps/windows_worker", "migrations", "config", "scripts", "integrations/sub2api", "deploy")
SOURCE_SUFFIXES = {".py", ".toml", ".json", ".ini", ".mako", ".go", ".mod", ".sum", ".ps1", ".sql", ".yml", ".yaml", ".example"}
SOURCE_FILES = ("pyproject.toml", "uv.lock", "alembic.ini", "apps/admin_web/package-lock.json", "apps/admin_web/package.json", "apps/admin_web/index.html", "apps/admin_web/vite.config.ts", "apps/admin_web/tsconfig.json")


def source_path(root: Path, name: str) -> Path:
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ReleaseError("source_input_path")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ReleaseError("source_input_path")
    return path


def source_snapshot(root: Path) -> dict[str, str]:
    names = set(SOURCE_FILES)
    for folder in SOURCE_FOLDERS:
        names.update(path.relative_to(root).as_posix() for path in (root / folder).rglob("*") if path.is_file() and path.suffix in SOURCE_SUFFIXES and "__pycache__" not in path.parts)
    names.update(path.relative_to(root).as_posix() for path in (root / "apps/admin_web/src").rglob("*") if path.is_file())
    return {name: digest_file(source_path(root, name)) for name in sorted(names)}


def _git(root: Path, arguments: list[str]) -> bytes:
    executable = shutil.which("git.exe") or shutil.which("git")
    if executable is None:
        raise ReleaseError("source_git_required")
    environment = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(name, None)
    try:
        result = subprocess.run([executable, "--no-pager", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *arguments], cwd=root, env=environment, capture_output=True, timeout=30, check=False)  # noqa: S603 -- fixed read-only Git operations in the explicit checkout
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseError("source_git_unavailable") from None
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ReleaseError("source_git_unavailable")
    return result.stdout


def source_revision(root: Path, sources: dict[str, str]) -> str:
    """Check real HEAD blobs, including files hidden by Git's stat cache."""
    root = root.resolve()
    repository = root.parent
    try:
        actual_root = Path(_git(root, ["rev-parse", "--show-toplevel"]).decode("utf-8").strip()).resolve()
        revision = _git(root, ["rev-parse", "--verify", "HEAD^{commit}"]).decode("ascii").strip()
    except UnicodeError:
        raise ReleaseError("source_git_revision") from None
    if actual_root != repository or root.name != "app" or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ReleaseError("source_git_revision")
    if _git(repository, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "app", ".github"]):
        raise ReleaseError("source_uncommitted")
    tree = {}
    for entry in _git(repository, ["ls-tree", "-r", "-z", revision, "--", "app"]).split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            tree[name.decode("utf-8")] = metadata.split()
    for name, expected in sources.items():
        metadata = tree.get("app/" + name)
        if metadata is None or metadata[0] not in (b"100644", b"100755") or metadata[1] != b"blob":
            raise ReleaseError("source_untracked_input")
        content = source_path(root, name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ReleaseError("source_inputs_changed")
        blob = hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content, usedforsecurity=False).hexdigest()
        if blob != metadata[2].decode("ascii"):
            raise ReleaseError("source_not_committed")
    if _git(root, ["rev-parse", "--verify", "HEAD^{commit}"]).decode("ascii").strip() != revision:
        raise ReleaseError("source_revision_changed")
    return revision


def verify_source_unchanged(root: Path, sources: dict[str, str], revision: str) -> None:
    if source_snapshot(root) != sources:
        raise ReleaseError("source_inputs_changed")
    if source_revision(root, sources) != revision:
        raise ReleaseError("source_revision_changed")
