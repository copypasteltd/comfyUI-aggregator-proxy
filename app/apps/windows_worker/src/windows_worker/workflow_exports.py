"""Read one explicitly configured local export; no discovery, network or shell."""
from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflow_catalogs import export_directory
from generation_domain.workflow_imports import MAX_WORKFLOW_BYTES, export_path, parse_source


def _denied() -> DomainError:
    return DomainError("workflow_export_not_allowed", "导出文件不可读或不在允许的本地目录中", 422)


def _plain_path(path: Path) -> None:
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise _denied()


def configured_export_root(value: Path) -> Path:
    path = value.absolute()
    if str(path).startswith("\\\\"):
        raise ValueError("workflow_export_root must be a local directory")
    _plain_path(path)
    if not path.is_dir():
        raise ValueError("workflow_export_root must be an existing directory")
    return path.resolve(strict=True)


def _windows_handle_path(handle: int) -> Path:
    import ctypes
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(handle, buffer, len(buffer), 0)
    if not 0 < length < len(buffer):
        raise _denied()
    value = buffer.value
    if not value.startswith("\\\\?\\") or value.startswith("\\\\?\\UNC\\"):
        raise _denied()
    return Path(value[4:])


def _opened_path(file: BinaryIO, expected: Path) -> Path:
    if os.name == "nt":
        import msvcrt
        return _windows_handle_path(msvcrt.get_osfhandle(file.fileno()))
    # The production Worker runs on Windows. Linux protocol fixtures also verify
    # the opened descriptor where procfs exists, otherwise compare file identity.
    descriptor = Path(f"/proc/self/fd/{file.fileno()}")
    if descriptor.exists():
        return descriptor.resolve(strict=True)
    if not os.path.samestat(os.fstat(file.fileno()), expected.stat()):
        raise _denied()
    return expected.resolve(strict=True)


@contextmanager
def export_directory_handle(root: Path, relative: str) -> Iterator[str | int]:
    """Keep enumeration tied to a checked directory, including parent renames."""
    export_directory(relative)
    directory = root.joinpath(*relative.split("/")) if relative else root
    try:
        _plain_path(directory)
        if not directory.resolve(strict=True).is_relative_to(root) or not directory.is_dir():
            raise _denied()
        with ExitStack() as stack:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                create, close = kernel.CreateFileW, kernel.CloseHandle
                create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                create.restype = wintypes.HANDLE
                close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
                chain = [*reversed(directory.parents), directory]
                if len(chain) > 64:
                    raise _denied()
                for part in chain:
                    # LIST_DIRECTORY is needed: metadata-only access does not
                    # enforce sharing restrictions. Deny write/delete sharing
                    # so a reparse point also cannot be changed in place.
                    handle = create("\\\\?\\" + str(part), 0x81, 0x1, None, 3, 0x02200000, None)
                    if handle is None or handle == ctypes.c_void_p(-1).value:
                        raise _denied()
                    stack.callback(close, handle)
                    if _windows_handle_path(handle) != part:
                        raise _denied()
                    _plain_path(part)
                yield str(directory)
            else:
                directory_flag, nofollow_flag = getattr(os, "O_DIRECTORY", None), getattr(os, "O_NOFOLLOW", None)
                if directory_flag is None or nofollow_flag is None:
                    raise _denied()
                descriptor = os.open(directory, os.O_RDONLY | directory_flag | nofollow_flag)
                stack.callback(os.close, descriptor)
                if not Path(f"/proc/self/fd/{descriptor}").resolve(strict=True).is_relative_to(root):
                    raise _denied()
                yield descriptor
    except OSError as exc:
        raise _denied() from exc


def observe_export(root: Path | None) -> dict[str, str]:
    state = "not_configured"
    if root is not None:
        try:
            _plain_path(root)
            state = "ready" if root.is_dir() else "unavailable"
        except (OSError, DomainError):
            state = "unavailable"
    return {"protocol": "local-directory-v1", "status": state}


def read_export(root: Path | None, relative_path: str) -> dict[str, object]:
    if root is None:
        raise DomainError("workflow_export_not_configured", "此渠道尚未配置本地 Workflow 导出目录", 422)
    name = export_path(relative_path)
    try:
        _plain_path(root)
        target = root.joinpath(*name.split("/"))
        _plain_path(target)
        if not target.resolve(strict=True).is_relative_to(root):
            raise _denied()
        # No buffering/read occurs until both type and actual open-handle path
        # have passed. A replacement parent junction cannot redirect the read.
        with target.open("rb", buffering=0) as file:
            info = os.fstat(file.fileno())
            actual = _opened_path(file, target)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not actual.is_relative_to(root):
                raise _denied()
            _plain_path(target)
            if info.st_size > MAX_WORKFLOW_BYTES:
                raise DomainError("workflow_source_too_large", "Workflow JSON 超过 1 MiB 限额", 413)
            raw = file.read(MAX_WORKFLOW_BYTES + 1)
            after = os.fstat(file.fileno())
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(raw) != info.st_size:
                raise DomainError("workflow_export_changed", "导出文件读取期间发生变化，请重试", 409)
        document, kind = parse_source(raw)
        return {"source_document": document, "source_digest": fingerprint(document), "export_format": kind, "source_bytes": len(raw)}
    except OSError as exc:
        raise _denied() from exc
