"""Bounded local model metadata and selected-file hashing; never loads weights."""
from __future__ import annotations

import hashlib
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from generation_contracts.model_files import ModelFileCatalog, ModelFileEntry
from generation_domain.errors import DomainError
from generation_domain.model_files import MODEL_SUFFIXES, inferred_model_kind, model_file_path
from generation_domain.values import fingerprint

from windows_worker.capacity import is_pointer
from windows_worker.workflow_exports import _opened_path, _plain_path, export_directory_handle


def catalog_result(entries: list[dict[str, Any]], scanned: int, skipped: int, reason: str | None, *, unavailable: bool = False) -> dict[str, Any]:
    value = {"protocol": "models-directory-v1", "status": "unavailable" if unavailable else "partial" if reason else "ready", "entries": sorted(entries, key=lambda e: e["relative_path"]), "scanned_entries": scanned, "skipped_entries": skipped, "reason": reason}
    return ModelFileCatalog.model_validate({**value, "catalog_digest": fingerprint(value)}).model_dump()


def discover_models(comfy_root: Path, *, maximum_entries: int = 5000, maximum_files: int = 1000, maximum_depth: int = 8, seconds: float = 10) -> dict[str, Any]:
    root = comfy_root / "models"
    entries: list[dict[str, Any]] = []
    scanned = skipped = 0
    reason: str | None = None
    deadline = time.monotonic() + seconds
    if not root.exists():
        return catalog_result([], 0, 0, "not_found", unavailable=True)
    try:
        _plain_path(root)
        stack = [("", 0)]
        identities: set[str] = set()
        while stack:
            relative, depth = stack.pop()
            if depth > maximum_depth:
                reason = "depth_limit"
                continue
            try:
                with export_directory_handle(root, relative) as guarded, os.scandir(guarded) as listing:
                    for entry in listing:
                        if scanned >= maximum_entries or len(entries) >= maximum_files or time.monotonic() > deadline:
                            reason = "entry_limit" if scanned >= maximum_entries else "file_limit" if len(entries) >= maximum_files else "time_budget"
                            return catalog_result(entries, scanned, skipped, reason)
                        scanned += 1
                        path = "/".join(part for part in (relative, entry.name) if part)
                        # Windows DirEntry.stat reports st_nlink=0. lstat queries
                        # the real link count, required before accepting a file.
                        info = root.joinpath(*path.split("/")).lstat()
                        if is_pointer(info):
                            skipped += 1
                            reason = "path_not_allowed"
                        elif entry.name.startswith("."):
                            skipped += 1
                        elif stat.S_ISDIR(info.st_mode):
                            stack.append((path, depth + 1))
                        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            skipped += 1
                            reason = "path_not_allowed"
                        elif Path(entry.name).suffix.lower() in MODEL_SUFFIXES:
                            name = "models/" + path
                            try:
                                row = ModelFileEntry.model_validate({"relative_path": model_file_path(name), "bytes": info.st_size, "modified_at_ns": info.st_mtime_ns, "kind": inferred_model_kind(name)})
                                if name.casefold() in identities:
                                    raise ValueError("duplicate model path")
                            except (ValueError, DomainError):
                                skipped += 1
                                reason = "path_not_allowed"
                                continue
                            identities.add(name.casefold())
                            entries.append(row.model_dump())
                        else:
                            skipped += 1
            except (OSError, DomainError):
                reason = "read_failed"
                if not relative:
                    return catalog_result([], 0, 0, "path_not_allowed", unavailable=True)
        return catalog_result(entries, scanned, skipped, reason)
    except (OSError, DomainError):
        return catalog_result([], 0, 0, "path_not_allowed", unavailable=True)


@contextmanager
def readonly_model(path: Path) -> Iterator[BinaryIO]:
    if os.name != "nt":
        with path.open("rb", buffering=0) as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create("\\\\?\\" + str(path), 0x80000000, 0x1, None, 3, 0x08200000, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise DomainError("model_file_not_allowed", "模型文件无法以受控只读方式打开", 422)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
        close(handle)
        raise
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
        yield stream


def inspect_model(comfy_root: Path, payload: dict[str, Any], *, maximum_bytes: int, seconds: int) -> dict[str, Any]:
    name = model_file_path(payload["relative_path"])
    root = comfy_root / "models"
    relative = name.removeprefix("models/")
    parent = relative.rpartition("/")[0]
    target = comfy_root.joinpath(*name.split("/"))
    deadline = time.monotonic() + seconds
    try:
        with export_directory_handle(root, parent), readonly_model(target) as stream:
            before = os.fstat(stream.fileno())
            actual = _opened_path(stream, target)
            if not stat.S_ISREG(before.st_mode) or is_pointer(before) or before.st_nlink != 1 or actual != target or not actual.is_relative_to(root):
                raise DomainError("model_file_not_allowed", "模型文件不在允许的普通文件范围内", 422)
            _plain_path(target)
            if before.st_size != payload["bytes"] or before.st_mtime_ns != payload["modified_at_ns"]:
                raise DomainError("model_file_changed", "模型文件已变化，请重新扫描", 409)
            if before.st_size > maximum_bytes:
                raise DomainError("model_hash_budget_exceeded", "模型超过本地单文件哈希预算", 413)
            digest, count = hashlib.sha256(), 0
            while True:
                if time.monotonic() > deadline:
                    raise DomainError("model_hash_budget_exceeded", "模型哈希时间预算已用尽", 422)
                chunk = stream.read(min(2 * 1024**2, maximum_bytes - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                if count > maximum_bytes or count > before.st_size:
                    raise DomainError("model_file_changed", "模型文件大小在读取时变化", 409)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if after.st_nlink != 1 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or count != before.st_size:
                raise DomainError("model_file_changed", "模型文件在读取期间发生变化", 409)
            return {"protocol": "model-inspection-v1", "relative_path": name, "bytes": count, "modified_at_ns": before.st_mtime_ns, "kind": inferred_model_kind(name), "sha256": digest.hexdigest(), "catalog_digest": payload["catalog_digest"]}
    except OSError as exc:
        raise DomainError("model_file_not_allowed", "模型文件不存在或无法安全读取", 422) from exc
    except DomainError as exc:
        if exc.code.startswith("workflow_"):
            raise DomainError("model_file_not_allowed", "模型目录无法安全读取", 422) from exc
        raise
