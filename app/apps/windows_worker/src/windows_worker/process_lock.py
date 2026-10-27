from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import sys
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path

from generation_domain.errors import DomainError

from windows_worker.configuration import canonical_origin

_registry: set[str] = set()
_registry_lock = threading.Lock()


class NodeProcessLock:
    """Machine-wide node identity lock, in addition to the journal directory lock."""

    def __init__(self, origin: str, node_id: str, *, allow_http: bool = False) -> None:
        self._posix_fd: int | None = None
        self._handle: int | None = None
        if sys.platform == "linux":
            import fcntl
            directory = Path(tempfile.gettempdir()) / ("render-control-worker-locks-" + str(os.getuid()))
            directory.mkdir(mode=0o700, exist_ok=True)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise DomainError("worker_node_lock_unavailable", "节点运行锁目录权限无效")
            digest = hashlib.sha256((canonical_origin(origin, allow_http=allow_http) + "\n" + node_id).encode()).hexdigest()
            descriptor = os.open(directory / digest, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                file = os.fstat(descriptor)
                if not stat.S_ISREG(file.st_mode) or file.st_nlink != 1 or file.st_uid != os.getuid() or file.st_mode & 0o077:
                    raise DomainError("worker_node_lock_unavailable", "节点运行锁文件无效")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException as exc:
                os.close(descriptor)
                if isinstance(exc, OSError):
                    raise DomainError("worker_node_in_use", "本机该节点已有 Worker 进程") from None
                raise
            self._posix_fd = descriptor
            self.abandoned = False
            return
        if sys.platform != "win32":
            raise DomainError("worker_platform_unsupported", "Worker 需要原生 Windows 或 Linux")
        self.name = "Global\\RenderControl.Worker." + hashlib.sha256((canonical_origin(origin, allow_http=allow_http) + "\n" + node_id).encode()).hexdigest()
        self._handle = None
        self._thread = threading.get_ident()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        with _registry_lock:
            if self.name in _registry:
                raise DomainError("worker_node_in_use", "本机该节点已有 Worker 进程")
            _registry.add(self.name)
        handle = None
        try:
            handle = kernel.CreateMutexW(None, False, self.name)
            if not handle:
                raise DomainError("worker_node_lock_unavailable", "无法取得本机节点运行锁")
            result = kernel.WaitForSingleObject(handle, 0)
            # An abandoned owner never proves any ComfyUI prompt has stopped.
            # Journal reconciliation still runs before new work can be claimed.
            if result not in (0, 0x80):
                code = "worker_node_in_use" if result == 0x102 else "worker_node_lock_unavailable"
                raise DomainError(code, "本机该节点运行锁不可用")
            self._handle = handle
            self.abandoned = result == 0x80
        except BaseException:
            if handle:
                kernel.CloseHandle(handle)
            with _registry_lock:
                _registry.discard(self.name)
            raise

    def close(self) -> None:
        if self._posix_fd is not None:
            descriptor, self._posix_fd = self._posix_fd, None
            os.close(descriptor)
            return
        if self._handle is None:
            return
        if threading.get_ident() != self._thread:
            raise DomainError("worker_node_lock_thread", "节点运行锁必须由原线程关闭")
        handle, self._handle = self._handle, None
        try:
            if not self._kernel.ReleaseMutex(handle):
                raise DomainError("worker_node_lock_release_failed", "无法释放本机节点运行锁")
        finally:
            self._kernel.CloseHandle(handle)
            with _registry_lock:
                _registry.discard(self.name)
