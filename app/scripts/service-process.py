"""Provide an owned hidden Windows console for cooperative service shutdown."""

from __future__ import annotations

import ctypes
import json
import os
import runpy
import sys
from ctypes import wintypes
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_bundle import ReleaseError  # noqa: E402 -- authenticated package module


def main() -> None:
    allocated = False
    kernel = None
    try:
        if sys.platform != "win32":
            raise ReleaseError("native_windows_required")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        kernel.GetConsoleProcessList.restype = wintypes.DWORD
        kernel.AllocConsole.argtypes = []
        kernel.AllocConsole.restype = wintypes.BOOL
        kernel.FreeConsole.argtypes = []
        kernel.FreeConsole.restype = wintypes.BOOL
        kernel.GetConsoleWindow.argtypes = []
        kernel.GetConsoleWindow.restype = wintypes.HWND
        processes = (wintypes.DWORD * 2)()
        attached = kernel.GetConsoleProcessList(processes, len(processes))
        if attached and (attached != 1 or processes[0] != os.getpid()):
            raise ReleaseError("service_private_console_required")
        if not attached and not kernel.AllocConsole():
            raise ReleaseError("service_private_console_unavailable")
        allocated = True
        window = kernel.GetConsoleWindow()
        if window:
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user.ShowWindow.restype = wintypes.BOOL
            user.ShowWindow(window, 0)
        runpy.run_path(str(Path(__file__).with_name("release-entrypoint.py")), run_name="__main__")
    except Exception as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, ReleaseError) else "service_process_failed"}), file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if allocated and kernel is not None:
            kernel.FreeConsole()


if __name__ == "__main__":
    main()
