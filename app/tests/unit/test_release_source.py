"""Actual native Git repositories exercise release provenance and drift guards."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import release_source as source  # noqa: E402
from release_bundle import ReleaseError  # noqa: E402


def git(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git.exe") or shutil.which("git")
    assert executable is not None, "Native Git is a required release dependency"
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(name, None)
    result = subprocess.run([executable, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(repository / ".git/no-hooks"), "-c", "user.name=Release Test", "-c", "user.email=release-test@example.invalid", *arguments], cwd=repository, env=environment, check=True, capture_output=True, timeout=30)  # noqa: S603 -- actual native Git restricted to this test's repository
    return result.stdout.decode("utf-8").strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repository = tmp_path / "checkout"
    repository.mkdir()
    git(repository, "init", "--template=")
    root = repository / "app"
    for name in (*source.SOURCE_FILES, "packages/core.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic source\n")
    (repository / ".gitattributes").write_bytes(b"* -text\n")
    (repository / ".gitignore").write_bytes(b"app/.local/\napp/packages/ignored.py\n")
    (repository / "README.md").write_bytes(b"synthetic documentation\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initial synthetic source")
    return root


def test_actual_commit_is_bound_to_selected_source_bytes(checkout: Path) -> None:
    inputs = source.source_snapshot(checkout)
    revision = source.source_revision(checkout, inputs)
    assert revision == git(checkout.parent, "rev-parse", "HEAD")
    assert len(inputs) == len(source.SOURCE_FILES) + 1
    source.verify_source_unchanged(checkout, inputs, revision)


def test_documentation_changes_and_ignored_build_output_do_not_dirty_source(checkout: Path) -> None:
    inputs = source.source_snapshot(checkout)
    revision = source.source_revision(checkout, inputs)
    (checkout.parent / "README.md").write_bytes(b"new documentation\n")
    (checkout / ".local").mkdir()
    (checkout / ".local/build.log").write_bytes(b"private build output\n")
    source.verify_source_unchanged(checkout, inputs, revision)


@pytest.mark.parametrize("staged", [False, True])
def test_uncommitted_source_is_rejected(checkout: Path, staged: bool) -> None:
    (checkout / "packages/core.py").write_bytes(b"changed source\n")
    if staged:
        git(checkout.parent, "add", "app/packages/core.py")
    with pytest.raises(ReleaseError, match="^source_uncommitted$"):
        source.source_revision(checkout, source.source_snapshot(checkout))


@pytest.mark.parametrize("name", ["packages/untracked.py", "tests/untracked.py", "../.github/workflows/untracked.yml"])
def test_untracked_application_or_ci_changes_are_rejected(checkout: Path, name: str) -> None:
    path = checkout / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"uncommitted input\n")
    with pytest.raises(ReleaseError, match="^source_uncommitted$"):
        source.source_revision(checkout, source.source_snapshot(checkout))


def test_git_ignored_input_cannot_silently_enter_a_candidate(checkout: Path) -> None:
    (checkout / "packages/ignored.py").write_bytes(b"ignored source\n")
    assert not git(checkout.parent, "status", "--porcelain")
    with pytest.raises(ReleaseError, match="^source_untracked_input$"):
        source.source_revision(checkout, source.source_snapshot(checkout))


def test_assume_unchanged_and_preserved_stat_cannot_hide_wrong_blob_bytes(checkout: Path) -> None:
    path = checkout / "packages/core.py"
    before = path.stat()
    git(checkout.parent, "update-index", "--assume-unchanged", "app/packages/core.py")
    path.write_bytes(b"modified! source\n")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not git(checkout.parent, "status", "--porcelain")
    with pytest.raises(ReleaseError, match="^source_not_committed$"):
        source.source_revision(checkout, source.source_snapshot(checkout))


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_source_changes_during_build_are_rejected(checkout: Path, change: str) -> None:
    inputs = source.source_snapshot(checkout)
    revision = source.source_revision(checkout, inputs)
    if change == "delete":
        (checkout / "packages/core.py").unlink()
    else:
        (checkout / "packages" / ("core.py" if change == "edit" else "new.py")).write_bytes(b"build-time change\n")
    with pytest.raises(ReleaseError, match="^source_inputs_changed$"):
        source.verify_source_unchanged(checkout, inputs, revision)


def test_commit_change_during_build_is_rejected_even_for_documentation(checkout: Path) -> None:
    inputs = source.source_snapshot(checkout)
    revision = source.source_revision(checkout, inputs)
    (checkout.parent / "README.md").write_bytes(b"committed while packaging\n")
    git(checkout.parent, "add", "README.md")
    git(checkout.parent, "commit", "-m", "concurrent documentation commit")
    with pytest.raises(ReleaseError, match="^source_revision_changed$"):
        source.verify_source_unchanged(checkout, inputs, revision)


def test_external_git_environment_cannot_redirect_provenance(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", str(checkout.parent / "not-this-repository"))
    monkeypatch.setenv("GIT_WORK_TREE", str(checkout / ".local"))
    assert source.source_revision(checkout, source.source_snapshot(checkout)) == git(checkout.parent, "rev-parse", "HEAD")


def test_source_digest_cannot_claim_different_bytes(checkout: Path) -> None:
    inputs = source.source_snapshot(checkout)
    inputs["packages/core.py"] = "0" * 64
    with pytest.raises(ReleaseError, match="^source_inputs_changed$"):
        source.source_revision(checkout, inputs)


def test_missing_git_refuses_source_attestation(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ReleaseError, match="^source_git_required$"):
        source.source_revision(checkout, source.source_snapshot(checkout))
