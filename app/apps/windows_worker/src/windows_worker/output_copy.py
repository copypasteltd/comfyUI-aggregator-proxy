from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from generation_domain.errors import DomainError

BLOCK_BYTES = 1024 * 1024


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    # Windows handle/path ctime observations can differ for an unchanged file.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _plain(info: os.stat_result, *, directory: bool = False) -> bool:
    return (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1) and not bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _mismatch() -> DomainError:
    return DomainError("local_output_mismatch", "已有本地产物或暂存路径与当前输出不一致，保留文件待核对")


def _target_directory(root: Path, target: Path) -> None:
    root, target = root.absolute(), target.absolute()
    try:
        parts = target.relative_to(root).parts
    except ValueError as exc:
        raise _mismatch() from exc
    if len(parts) != 3 or parts[0] != "outputs" or any(p in {".", ".."} for p in parts) or not re.fullmatch(r"[0-9]{1,4}\.[a-zA-Z0-9]{1,8}", parts[-1]):
        raise _mismatch()
    if not _plain(root.lstat(), directory=True):
        raise _mismatch()
    parent = root
    for part in parts[:-1]:
        parent /= part
        try:
            info = parent.lstat()
        except FileNotFoundError:
            parent.mkdir()
            info = parent.lstat()
        if not _plain(info, directory=True):
            raise _mismatch()


def copy_output(source: Path, target: Path, maximum: int, root: Path) -> tuple[str, int]:
    """Continue an exact source prefix without truncating any existing bytes."""
    with source.open("rb") as incoming:
        original = os.fstat(incoming.fileno())
        if not stat.S_ISREG(original.st_mode):
            raise DomainError("invalid_output_path", "ComfyUI 输出不是普通文件")
        if original.st_size > maximum:
            raise DomainError("output_too_large", "输出超过发布包大小限制")
        _target_directory(root, target)
        try:
            existing = target.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and (not _plain(existing) or existing.st_size > original.st_size):
            raise _mismatch()
        with target.open("r+b" if existing is not None else "x+b") as outgoing:
            initial = os.fstat(outgoing.fileno())
            if not _plain(initial) or existing is not None and _signature(initial) != _signature(existing):
                raise _mismatch()
            digest, size = hashlib.sha256(), 0
            # Validate every existing byte before appending anything. A different
            # or longer file may be unique and must stay untouched.
            while size < initial.st_size:
                old = outgoing.read(min(BLOCK_BYTES, initial.st_size - size))
                original_bytes = incoming.read(len(old))
                if not old or old != original_bytes:
                    raise _mismatch()
                digest.update(original_bytes)
                size += len(old)
            if _signature(os.fstat(outgoing.fileno())) != _signature(initial):
                raise _mismatch()
            if _signature(os.fstat(incoming.fileno())) != _signature(original) or _signature(source.stat()) != _signature(original):
                raise DomainError("output_not_stable", "ComfyUI 输出文件仍在变化")
            while chunk := incoming.read(BLOCK_BYTES):
                size += len(chunk)
                if size > original.st_size:
                    raise DomainError("output_not_stable", "ComfyUI 输出文件仍在变化")
                outgoing.write(chunk)
                digest.update(chunk)
            if size != original.st_size or _signature(os.fstat(incoming.fileno())) != _signature(original) or _signature(source.stat()) != _signature(original):
                raise DomainError("output_not_stable", "ComfyUI 输出文件仍在变化")
            outgoing.flush()
            os.fsync(outgoing.fileno())
            outgoing.seek(0)
            confirmed, confirmed_size = hashlib.sha256(), 0
            while chunk := outgoing.read(BLOCK_BYTES):
                confirmed_size += len(chunk)
                if confirmed_size > original.st_size:
                    raise _mismatch()
                confirmed.update(chunk)
            current = target.lstat()
            if not _plain(current) or (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino) or confirmed_size != size or confirmed.digest() != digest.digest():
                raise _mismatch()
            if _signature(os.fstat(incoming.fileno())) != _signature(original) or _signature(source.stat()) != _signature(original):
                raise DomainError("output_not_stable", "ComfyUI 输出文件仍在变化")
            return digest.hexdigest(), size
