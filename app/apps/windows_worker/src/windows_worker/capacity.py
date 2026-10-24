from __future__ import annotations

import ctypes
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any

from generation_contracts.capacity import WorkerCapacity

from windows_worker.configuration import WorkerSettings


def is_pointer(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def available_memory() -> int:
    if sys.platform != "win32":
        raise OSError("native Windows memory observation required")

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended", ctypes.c_ulonglong)]

    value = MemoryStatus()
    value.length = ctypes.sizeof(value)
    function = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    function.argtypes = [ctypes.POINTER(MemoryStatus)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(value)):
        raise OSError("native memory observation failed")
    return int(value.available_physical)


def spool_size(root: Path, *, maximum_entries: int = 20000, maximum_depth: int = 8, seconds: float = 1.0) -> int:
    deadline, count, total = time.monotonic() + seconds, 0, 0
    if is_pointer(root.lstat()):
        raise OSError("spool root cannot be a pointer")
    stack = [(root / name, 0) for name in ("inputs", "outputs")]
    while stack:
        directory, depth = stack.pop()
        if depth > maximum_depth or time.monotonic() > deadline:
            raise OSError("spool observation budget exhausted")
        try:
            info = directory.lstat()
        except FileNotFoundError:
            continue
        if is_pointer(info) or not stat.S_ISDIR(info.st_mode):
            raise OSError("unsafe spool directory")
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > maximum_entries or time.monotonic() > deadline:
                    raise OSError("spool observation budget exhausted")
                info = entry.stat(follow_symlinks=False)
                if is_pointer(info):
                    raise OSError("unsafe spool entry")
                if stat.S_ISDIR(info.st_mode):
                    stack.append((Path(entry.path), depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > 2**60:
                        raise OSError("spool size out of range")
                else:
                    raise OSError("unsupported spool entry")
    return total


def observe_capacity(settings: WorkerSettings, sequence: int, reserved: int, uploads: int) -> WorkerCapacity:
    started = time.monotonic()
    disk = memory = spool = None
    try:
        roots = {settings.state_root, *(c.comfy_root for c in settings.channels)}
        for channel in settings.channels:
            for name in ("input", "output", "temp"):
                path = channel.comfy_root / name
                if path.exists():
                    roots.add(path)
        disk = min(shutil.disk_usage(root).free for root in roots)
        memory = available_memory()
        spool = spool_size(settings.state_root)
    except (OSError, ValueError):
        # Raw OS errors and paths never leave the Worker.
        disk = memory = spool = None
    value: dict[str, Any] = {"protocol": "native-capacity-v1", "sequence": sequence, "observed_ago_ms": min(60000, int((time.monotonic() - started) * 1000)),
        "status": "unavailable", "reasons": [], "free_disk_bytes": disk, "available_memory_bytes": memory,
        "spool_bytes": spool, "reserved_bytes": reserved, "pending_uploads": uploads,
        **{name: getattr(settings, name) for name in ("minimum_free_disk_bytes", "minimum_available_memory_bytes", "maximum_spool_bytes", "generation_reserve_bytes", "maximum_uploads")}}
    unchecked = WorkerCapacity.model_construct(**value)
    value["reasons"] = unchecked.derived_reasons()
    value["status"] = "unavailable" if value["reasons"] == ["observation_unavailable"] else "blocked" if value["reasons"] else "ready"
    return WorkerCapacity.model_validate(value)
