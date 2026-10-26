from __future__ import annotations

import errno
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from generation_domain.errors import DomainError
from windows_worker import output_copy
from windows_worker.output_copy import copy_output


def paths(tmp_path: Path) -> tuple[Path, Path, Path, bytes]:
    root = tmp_path / "state"
    root.mkdir()
    source = tmp_path / "original.wav"
    payload = b"Synthetic durable output\x00" * 90000
    source.write_bytes(payload)
    target = root / "outputs" / ("att_" + "1" * 32) / "0.wav"
    return root, source, target, payload


def test_native_writer_exit_preserves_prefix_and_copy_continues(tmp_path: Path) -> None:
    root, source, target, payload = paths(tmp_path)
    target.parent.mkdir(parents=True)
    code = "import os,sys; from pathlib import Path; f=Path(sys.argv[2]).open('xb'); f.write(Path(sys.argv[1]).read_bytes()[:1048613]); f.flush(); os.fsync(f.fileno()); os._exit(73)"
    child = subprocess.run([sys.executable, "-I", "-c", code, str(source), str(target)], capture_output=True, timeout=30, check=False)  # noqa: S603 - fixed native crash fixture and owned paths
    assert child.returncode == 73 and target.read_bytes() == payload[:1048613]
    digest, size = copy_output(source, target, len(payload), root)
    assert target.read_bytes() == source.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest() and size == len(payload)


@pytest.mark.parametrize("variant", ["different", "late_difference", "longer"])
def test_existing_unique_or_longer_file_is_never_truncated_or_overwritten(tmp_path: Path, variant: str) -> None:
    root, source, target, payload = paths(tmp_path)
    target.parent.mkdir(parents=True)
    previous = b"different" if variant == "different" else payload[:1048576] + b"different" if variant == "late_difference" else payload + b"retained"
    target.write_bytes(previous)
    before = target.stat()
    with pytest.raises(DomainError) as failure:
        copy_output(source, target, len(payload), root)
    assert failure.value.code == "local_output_mismatch"
    assert target.read_bytes() == previous and target.stat().st_mtime_ns == before.st_mtime_ns
    assert source.read_bytes() == payload


def test_source_budget_rejection_does_not_touch_existing_file(tmp_path: Path) -> None:
    root, source, target, payload = paths(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"retained")
    with pytest.raises(DomainError) as failure:
        copy_output(source, target, len(payload) - 1, root)
    assert failure.value.code == "output_too_large" and target.read_bytes() == b"retained"


def test_fsync_failure_keeps_copy_for_verified_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, source, target, payload = paths(tmp_path)
    fsync = os.fsync

    def fail_once(fd: int) -> None:
        monkeypatch.setattr(output_copy.os, "fsync", fsync)
        raise OSError(errno.EIO, "Synthetic synchronization failure")

    monkeypatch.setattr(output_copy.os, "fsync", fail_once)
    with pytest.raises(OSError):
        copy_output(source, target, len(payload), root)
    assert target.read_bytes() == payload
    assert copy_output(source, target, len(payload), root) == (hashlib.sha256(payload).hexdigest(), len(payload))


def test_native_different_creation_and_modification_times_are_stable(tmp_path: Path) -> None:
    root, source, target, payload = paths(tmp_path)
    info = source.stat()
    os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns - 5_000_000_000))
    assert copy_output(source, target, len(payload), root) == (hashlib.sha256(payload).hexdigest(), len(payload))
    assert source.read_bytes() == target.read_bytes() == payload


def test_source_changes_during_prefix_validation_prevent_any_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, source, target, payload = paths(tmp_path)
    target.parent.mkdir(parents=True)
    prefix = payload[:100]
    target.write_bytes(prefix)
    original_open = Path.open

    class ChangingReader:
        def __init__(self, handle: Any) -> None:
            self.handle = handle

        def __enter__(self) -> ChangingReader:
            return self

        def __exit__(self, *args: Any) -> None:
            self.handle.close()

        def fileno(self) -> int:
            return self.handle.fileno()

        def read(self, size: int) -> bytes:
            value = self.handle.read(size)
            with original_open(source, "ab") as changing:
                changing.write(b"changed")
            return value

    def open_path(path: Path, *args: Any, **kwargs: Any) -> Any:
        opened = original_open(path, *args, **kwargs)
        return ChangingReader(opened) if path == source and args == ("rb",) else opened

    monkeypatch.setattr(Path, "open", open_path)
    with pytest.raises(DomainError) as failure:
        copy_output(source, target, len(payload) + 100, root)
    assert failure.value.code == "output_not_stable" and target.read_bytes() == prefix


def test_hardlinked_target_is_preserved(tmp_path: Path) -> None:
    root, source, target, payload = paths(tmp_path)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "retained.wav"
    outside.write_bytes(payload[:100])
    os.link(outside, target)
    with pytest.raises(DomainError) as failure:
        copy_output(source, target, len(payload), root)
    assert failure.value.code == "local_output_mismatch" and outside.read_bytes() == target.read_bytes() == payload[:100]


@pytest.mark.parametrize("level", ["root", "outputs", "attempt"])
def test_directory_reparse_target_cannot_redirect_copy(tmp_path: Path, level: str) -> None:
    root, source, target, payload = paths(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root if level == "root" else root / "outputs" if level == "outputs" else target.parent
    if level == "root":
        root.rmdir()
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        command = str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe")
        subprocess.run([command, "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=True, timeout=10)  # noqa: S603 - fixed junction fixture and owned paths
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(DomainError) as failure:
            copy_output(source, target, len(payload), root)
        assert failure.value.code == "local_output_mismatch"
        assert not list(outside.iterdir()) and source.read_bytes() == payload
    finally:
        if sys.platform == "win32":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("relative", ["outputs/../private.wav", "outputs/att/extra/0.wav", "other/att/0.wav"])
def test_copy_rejects_non_owned_output_paths(tmp_path: Path, relative: str) -> None:
    root, source, _, payload = paths(tmp_path)
    with pytest.raises(DomainError) as failure:
        copy_output(source, root / relative, len(payload), root)
    assert failure.value.code == "local_output_mismatch"
    assert list(root.iterdir()) == [] and source.read_bytes() == payload
