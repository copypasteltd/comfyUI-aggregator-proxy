"""Owned native service-entry probes; never register SCM services or elevate."""

from __future__ import annotations

import ctypes
import json
import subprocess
from ctypes import wintypes
from pathlib import Path

from native_ci import ProcessTree

STOP_CONTROLLER = r'''
import ctypes,json,os,sys,time
from ctypes import wintypes as w
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetConsoleProcessList.argtypes=[ctypes.POINTER(w.DWORD),w.DWORD];k.GetConsoleProcessList.restype=w.DWORD
k.FreeConsole.argtypes=[];k.FreeConsole.restype=w.BOOL
k.AttachConsole.argtypes=[w.DWORD];k.AttachConsole.restype=w.BOOL
k.SetConsoleCtrlHandler.argtypes=[ctypes.c_void_p,w.BOOL];k.SetConsoleCtrlHandler.restype=w.BOOL
k.GenerateConsoleCtrlEvent.argtypes=[w.DWORD,w.DWORD];k.GenerateConsoleCtrlEvent.restype=w.BOOL
p=(w.DWORD*2)();n=k.GetConsoleProcessList(p,2)
if n and (n!=1 or p[0]!=os.getpid()): raise SystemExit('shared_controller_console_refused')
if n and not k.FreeConsole(): raise SystemExit('own_console_detach_failed')
target_pid=int(sys.argv[1])
if not k.AttachConsole(target_pid): raise SystemExit('owned_target_console_attach_failed')
try:
 attached=(w.DWORD*3)();count=k.GetConsoleProcessList(attached,3)
 if count!=2 or set(attached[:count])!={os.getpid(),target_pid}: raise SystemExit('shared_target_console_refused')
 if not k.SetConsoleCtrlHandler(None,True): raise SystemExit('controller_handler_failed')
 if not k.GenerateConsoleCtrlEvent(0,0): raise SystemExit('owned_console_signal_failed')
 time.sleep(.1)
finally: k.FreeConsole()
print(json.dumps({'signal_sent':True,'scm_used':False,'elevation_used':False}))
'''


class ServiceEntryProbe:
    def __init__(self, process: subprocess.Popen, python: Path) -> None:
        self.process = process
        self.python = python
        self.tree = ProcessTree()
        self.tree.assign(process)
        self.stopped = False

    def stop(self) -> dict:
        if self.process.poll() is not None:
            raise AssertionError("native_service_entry_exited_before_stop")
        result = subprocess.run([str(self.python), "-I", "-c", STOP_CONTROLLER, str(self.process.pid)], stdin=subprocess.DEVNULL, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed controller, retained owned PID, dedicated console only
        if result.returncode or json.loads(result.stdout) != {"signal_sent": True, "scm_used": False, "elevation_used": False}:
            raise AssertionError("native_service_entry_stop_signal_failed")
        self.process.wait(timeout=110)
        if self.process.returncode != 0:
            raise AssertionError("native_service_entry_did_not_exit_cleanly")
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [(name, wintypes.DWORD) for name in ("page_faults", "total", "active", "terminated")]

        query = self.tree.kernel.QueryInformationJobObject
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        query.restype = wintypes.BOOL
        accounting = Accounting()
        if not query(self.tree.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None) or accounting.active:
            raise AssertionError("native_service_entry_descendants_remain")
        self.stopped = True
        return {"exit_code": 0, "signal": "CTRL_C", "owned_job_active_processes_before_cleanup": 0, "scm_registered": False, "wrapper_lifecycle_verified": False}

    def close(self) -> None:
        self.tree.close()
        self.process.wait(timeout=15)
