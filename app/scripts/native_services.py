"""Fixed-role Windows service plans; no shell, elevation, or implicit registration."""

from __future__ import annotations

import ctypes
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from release_bundle import (
    ReleaseError,
    digest_file,
    strict_json,
    verify_candidate,
    verify_payload,
    verify_service_python,
    verify_service_runtime,
)

ROLES = ("api", "worker", "gc", "runner", "gateway")
NAME = r"[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?"
STOPPED, START_PENDING, STOP_PENDING, RUNNING = 1, 2, 3, 4
CONTROL_TIMEOUT = 150
MAX_DOCUMENT = 256 * 1024


def fields(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ReleaseError("service_configuration_fields")
    return value


def windows_path(value: Any) -> str:
    if type(value) is not str or not 4 <= len(value) <= 240 or not re.fullmatch(r'[A-Za-z]:[/\\][^%"<>|?*\x00-\x1f\x7f:]+', value):
        raise ReleaseError("service_path_invalid")
    for part in re.split(r"[/\\]", value[3:]):
        if not part or part in (".", "..") or part != part.strip() or part.endswith(".") or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I):
            raise ReleaseError("service_path_invalid")
    return str(PureWindowsPath(value))


def validate_spec(value: Any) -> dict:
    """Structural/semantic validation is separate from native filesystem checks."""
    value = fields(value, {"protocol", "deployment_id", "installation", "services"})
    if value["protocol"] != "native-services-v1" or type(value["deployment_id"]) is not str or not re.fullmatch(NAME, value["deployment_id"]):
        raise ReleaseError("service_configuration_version_or_name")
    windows_path(value["installation"])
    if type(value["services"]) is not list or not 1 <= len(value["services"]) <= 24:
        raise ReleaseError("service_configuration_count")
    identifiers, states = set(), []
    for item in value["services"]:
        item = fields(item, {"role", "instance", "config", "state_dir", "startup"}, {"listen", "native_config"})
        if item["role"] not in ROLES or type(item["instance"]) is not str or not re.fullmatch(NAME, item["instance"]) or item["startup"] not in ("manual", "automatic-delayed"):
            raise ReleaseError("service_configuration_role_or_startup")
        identifier = service_id(value, item)
        if identifier in identifiers:
            raise ReleaseError("service_duplicate_identity")
        identifiers.add(identifier)
        windows_path(item["config"])
        states.append(PureWindowsPath(windows_path(item["state_dir"])))
        if (item["role"] == "api") != ("listen" in item) or (item["role"] == "gateway") != ("native_config" in item):
            raise ReleaseError("service_role_configuration_mismatch")
        if "native_config" in item:
            windows_path(item["native_config"])
        if "listen" in item:
            listen = fields(item["listen"], {"host", "port", "tls_cert", "tls_key", "allow_network_bind"})
            if type(listen["host"]) is not str or type(listen["allow_network_bind"]) is not bool or type(listen["port"]) is not int or not 1 <= listen["port"] <= 65535:
                raise ReleaseError("service_listen_invalid")
            try:
                address = ipaddress.ip_address(listen["host"])
            except ValueError as exc:
                raise ReleaseError("service_listen_invalid") from exc
            if not address.is_loopback and not listen["allow_network_bind"]:
                raise ReleaseError("service_network_bind_requires_explicit_flag")
            windows_path(listen["tls_cert"])
            windows_path(listen["tls_key"])
    for index, state in enumerate(states):
        for other in states[index + 1:]:
            if overlaps(state, other):
                raise ReleaseError("service_state_directories_overlap")
    return value


def service_id(spec: dict, item: dict) -> str:
    return f"rc-{spec['deployment_id']}-{item['role']}-{item['instance']}"


def overlaps(left: Any, right: Any) -> bool:
    return left == right or left in right.parents or right in left.parents


def local_path(value: str, *, kind: str) -> Path:
    path = Path(windows_path(value))
    # Inspect before resolve(), which would otherwise erase evidence of a junction.
    for parent in [*reversed(path.parents), path]:
        try:
            attributes = parent.lstat()
        except FileNotFoundError:
            continue
        if attributes.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ReleaseError("service_path_reparse_point")
    if kind == "file" and not path.is_file() or kind == "directory" and not path.is_dir():
        raise ReleaseError("service_required_path_missing")
    return path.resolve()


def read_document(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > MAX_DOCUMENT:
        raise ReleaseError("service_document_invalid")
    return strict_json(path.read_bytes())


def installed(spec: dict) -> tuple[Path, str]:
    installation = local_path(spec["installation"], kind="directory")
    receipt_path = local_path(str(installation / "install.json"), kind="file")
    receipt = read_document(receipt_path)
    if type(receipt) is not dict or type(receipt.get("manifest_sha256")) is not str:
        raise ReleaseError("invalid_install_record")
    manifest = verify_payload(installation / "payload", receipt["manifest_sha256"])
    verify_candidate(installation / "payload", manifest)
    verify_service_runtime(installation / "payload")
    verify_service_python(installation, receipt.get("service_python"))
    if Path(sys.prefix).resolve() != installation / "venv":
        raise ReleaseError("installed_interpreter_required")
    return installation, receipt["manifest_sha256"]


def check_layout(spec: dict, output: Path) -> None:
    installation = local_path(spec["installation"], kind="directory")
    if overlaps(installation, output):
        raise ReleaseError("service_output_inside_installation")
    states = [local_path(item["state_dir"], kind="directory") for item in spec["services"]]
    files = []
    for item in spec["services"]:
        files.append(local_path(item["config"], kind="file"))
        if item["role"] == "gateway":
            files.append(local_path(item["native_config"], kind="file"))
        if item["role"] == "api":
            files.extend(local_path(item["listen"][key], kind="file") for key in ("tls_cert", "tls_key"))
    for state in states:
        if overlaps(state, installation) or overlaps(state, output):
            raise ReleaseError("service_state_directory_not_external")
    for path in files:
        if overlaps(path, installation) or overlaps(path, output) or any(overlaps(path, state) for state in states):
            raise ReleaseError("service_secret_path_not_external")


def windows_directory() -> Path:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetWindowsDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(260)
    length = kernel.GetWindowsDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise ReleaseError("service_windows_directory_unavailable")
    return local_path(buffer.value, kind="directory")


def gateway_environment(payload: Path, config: Path, state: Path, native_config: Path) -> dict[str, str]:
    windows = windows_directory()
    allowed = {"PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"}
    environment = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    environment.update({"SystemRoot": str(windows), "WINDIR": str(windows), "PATH": str(payload / "media/bin") + os.pathsep + str(windows / "System32"), "DATA_DIR": str(state), "CONFIG_FILE": str(native_config), "SUB2API_RENDER_CONTROL_CONFIG": str(config)})
    return environment


def run_gateway(payload: Path, config: Path, state: Path, native_config: Path) -> None:
    gateway = None
    stopping: float | None = None

    def forward_stop() -> None:
        if gateway is not None and gateway.poll() is None:
            try:
                gateway.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                # The retained process handle, deadline and poll remain authoritative.
                pass

    def stop_gateway(_signal, _frame) -> None:
        nonlocal stopping
        if stopping is None:
            stopping = time.monotonic()
            forward_stop()

    previous = {kind: signal.signal(kind, stop_gateway) for kind in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)}
    try:
        if stopping is not None:
            return
        gateway = subprocess.Popen([str(payload / "gateway/sub2api-provider.exe")], cwd=state, env=gateway_environment(payload, config, state, native_config), stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)  # noqa: S603 -- authenticated immutable Provider, minimal environment, owned process group
        if stopping is not None:
            forward_stop()
        while gateway.poll() is None:
            if stopping is not None and time.monotonic() - stopping >= 90:
                gateway.kill()
                gateway.wait(timeout=10)
                raise ReleaseError("gateway_graceful_shutdown_timeout")
            time.sleep(0.1)
        if gateway.returncode:
            raise ReleaseError("gateway_process_failed")
    finally:
        try:
            if gateway is not None and gateway.poll() is None:
                stop_gateway(signal.SIGINT, None)
                try:
                    assert stopping is not None
                    remaining = max(0.0, 90 - (time.monotonic() - stopping))
                    gateway.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=10)
        finally:
            for kind, handler in previous.items():
                signal.signal(kind, handler)


def build_xml(spec: dict, item: dict) -> bytes:
    installation = Path(spec["installation"])
    payload = installation / "payload"
    state = Path(item["state_dir"])
    root = ET.Element("service")

    def element(name: str, value: Any, parent: Any = root) -> Any:
        node = ET.SubElement(parent, name)
        node.text = str(value)
        return node

    identifier = service_id(spec, item)
    element("id", identifier)
    element("name", identifier)
    element("description", "Render Control fixed-role native service; readiness requires application verification")
    executable = installation / "venv/Scripts/python-service.exe"
    arguments = ["-I", str(payload / "scripts/service-process.py"), item["role"], "--config", item["config"], "--state-dir", item["state_dir"]]
    variables = {}
    if item["role"] == "gateway":
        arguments.extend(["--native-config", item["native_config"]])
    if item["role"] == "api":
        listen = item["listen"]
        arguments.extend(["--host", listen["host"], "--port", str(listen["port"]), "--tls-cert", listen["tls_cert"], "--tls-key", listen["tls_key"]])
        if listen["allow_network_bind"]:
            arguments.append("--allow-network-bind")
    element("executable", executable)
    element("arguments", subprocess.list2cmdline(arguments))
    element("workingdirectory", state)
    element("hidewindow", "true")
    element("startmode", "Manual" if item["startup"] == "manual" else "Automatic")
    element("delayedAutoStart", "true" if item["startup"] == "automatic-delayed" else "false")
    account = ET.SubElement(root, "serviceaccount")
    element("domain", "NT SERVICE", account)
    element("user", identifier, account)
    element("allowservicelogon", "false", account)
    element("stoptimeout", "120 sec")
    element("stopparentprocessfirst", "true")
    for delay in (10, 30, 60):
        ET.SubElement(root, "onfailure", action="restart", delay=f"{delay} sec")
    ET.SubElement(root, "onfailure", action="none")
    element("resetfailure", "1 hour")
    element("logpath", state / "service-logs")
    log = ET.SubElement(root, "log", mode="roll-by-size")
    element("sizeThreshold", "10240", log)
    element("keepFiles", "8", log)
    variables.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": str(payload / "media/bin") + ";" + str(windows_directory() / "System32"), "TEMP": str(state / "service-tmp"), "TMP": str(state / "service-tmp")})
    for name, value in sorted(variables.items()):
        ET.SubElement(root, "env", name=name, value=value)
    ET.indent(root, "  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def acl_plan(spec: dict, output: Path) -> dict:
    entries = []
    for item in spec["services"]:
        reads = [spec["installation"], str(Path(sys.base_prefix).resolve()), str(output), item["config"]]
        if item["role"] == "api":
            reads.extend(item["listen"][key] for key in ("tls_cert", "tls_key"))
        if item["role"] == "gateway":
            reads.append(item["native_config"])
        entries.append({"service_id": service_id(spec, item), "account": "NT SERVICE\\" + service_id(spec, item), "read_execute": sorted(set(reads)), "modify": [item["state_dir"]], "worker_credential_acl_requires_configuration_review": item["role"] == "worker"})
    return {"protocol": "native-service-acl-plan-v1", "permissions_applied": False, "operator_must_protect_service_xml_and_executables": True, "services": entries}


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()


def render(spec: dict, output: Path) -> dict:
    spec = validate_spec(spec)
    output = local_path(str(output), kind="new")
    check_layout(spec, output)
    installation, manifest = installed(spec)
    if output.exists() or not output.parent.is_dir():
        raise ReleaseError("service_output_must_be_new")
    output.mkdir()
    try:
        (output / "spec.json").write_bytes(json_bytes(spec))
        (output / "acl-plan.json").write_bytes(json_bytes(acl_plan(spec, output)))
        for item in spec["services"]:
            identifier = service_id(spec, item)
            shutil.copyfile(installation / "payload/service-tools/WinSW.NET461.exe", output / f"{identifier}.exe")
            (output / f"{identifier}.xml").write_bytes(build_xml(spec, item))
            for folder in ("service-logs", "service-tmp"):
                local_path(str(Path(item["state_dir"]) / folder), kind="new").mkdir(exist_ok=True)
        inventory = {path.name: digest_file(path) for path in sorted(output.iterdir())}
        document = {"protocol": "native-service-plan-v1", "installation_manifest_sha256": manifest, "files": inventory}
        (output / "deployment.json").write_bytes(json_bytes(document))
        return {"rendered": True, "services": len(spec["services"]), "services_registered": False, "permissions_applied": False}
    except BaseException:
        # Only this fresh, exact directory is removed; state and journal survive.
        for path in output.iterdir():
            if path.is_file() and not path.is_symlink():
                path.unlink()
        output.rmdir()
        raise


def load_deployment(output: Path) -> dict:
    output = local_path(str(output), kind="directory")
    document = fields(read_document(output / "deployment.json"), {"protocol", "installation_manifest_sha256", "files"})
    spec = validate_spec(read_document(output / "spec.json"))
    check_layout(spec, output)
    installation, manifest = installed(spec)
    if document["protocol"] != "native-service-plan-v1" or document["installation_manifest_sha256"] != manifest:
        raise ReleaseError("service_plan_installation_mismatch")
    expected = {"spec.json", "acl-plan.json"}
    for item in spec["services"]:
        identifier = service_id(spec, item)
        expected.update((f"{identifier}.exe", f"{identifier}.xml"))
        xml = local_path(str(output / f"{identifier}.xml"), kind="file")
        executable = local_path(str(output / f"{identifier}.exe"), kind="file")
        if xml.stat().st_size > MAX_DOCUMENT or xml.read_bytes() != build_xml(spec, item) or digest_file(executable) != digest_file(installation / "payload/service-tools/WinSW.NET461.exe"):
            raise ReleaseError("service_plan_tampered")
    if read_document(output / "acl-plan.json") != acl_plan(spec, output):
        raise ReleaseError("service_acl_plan_tampered")
    if type(document["files"]) is not dict or set(document["files"]) != expected or {path.name for path in output.iterdir()} != expected | {"deployment.json"}:
        raise ReleaseError("service_plan_inventory_mismatch")
    for name in expected:
        path = local_path(str(output / name), kind="file")
        if digest_file(path) != document["files"][name]:
            raise ReleaseError("service_plan_tampered")
    return spec


@dataclass(frozen=True)
class ServiceState:
    image_path: str
    account: str
    state: int
    pid: int = 0
    startup: int = 3


class ServiceControl:
    """Read-only SCM identity/status queries; mutation uses authenticated WinSW."""

    def __init__(self) -> None:
        self.api = ctypes.WinDLL("advapi32", use_last_error=True)
        api = self.api
        api.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        api.OpenSCManagerW.restype = wintypes.HANDLE
        api.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
        api.OpenServiceW.restype = wintypes.HANDLE
        api.QueryServiceConfigW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        api.QueryServiceConfigW.restype = wintypes.BOOL
        api.QueryServiceStatusEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        api.QueryServiceStatusEx.restype = wintypes.BOOL
        api.CloseServiceHandle.argtypes = [wintypes.HANDLE]
        api.CloseServiceHandle.restype = wintypes.BOOL

    def query(self, identifier: str) -> ServiceState | None:
        class Config(ctypes.Structure):
            _fields_ = [("kind", wintypes.DWORD), ("startup", wintypes.DWORD), ("error", wintypes.DWORD), ("image", wintypes.LPWSTR), ("group", wintypes.LPWSTR), ("tag", wintypes.DWORD), ("dependencies", wintypes.LPWSTR), ("account", wintypes.LPWSTR), ("display", wintypes.LPWSTR)]

        class Status(ctypes.Structure):
            _fields_ = [(name, wintypes.DWORD) for name in ("kind", "state", "controls", "win32_exit", "service_exit", "checkpoint", "wait_hint", "pid", "flags")]

        manager = self.api.OpenSCManagerW(None, None, 0x0001)
        if not manager:
            raise ReleaseError("service_scm_access_denied")
        service = None
        try:
            service = self.api.OpenServiceW(manager, identifier, 0x0001 | 0x0004)
            if not service:
                if ctypes.get_last_error() == 1060:
                    return None
                raise ReleaseError("service_scm_query_failed")
            needed = wintypes.DWORD()
            self.api.QueryServiceConfigW(service, None, 0, ctypes.byref(needed))
            if ctypes.get_last_error() != 122 or not 0 < needed.value <= 65536:
                raise ReleaseError("service_scm_query_failed")
            buffer = ctypes.create_string_buffer(needed.value)
            if not self.api.QueryServiceConfigW(service, buffer, len(buffer), ctypes.byref(needed)):
                raise ReleaseError("service_scm_query_failed")
            config = ctypes.cast(buffer, ctypes.POINTER(Config)).contents
            status = Status()
            if not self.api.QueryServiceStatusEx(service, 0, ctypes.byref(status), ctypes.sizeof(status), ctypes.byref(needed)):
                raise ReleaseError("service_scm_query_failed")
            return ServiceState(config.image or "", config.account or "", status.state, status.pid, config.startup)
        finally:
            if service:
                self.api.CloseServiceHandle(service)
            self.api.CloseServiceHandle(manager)


def administrator() -> bool:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.IsUserAnAdmin.argtypes = []
    shell.IsUserAnAdmin.restype = wintypes.BOOL
    return bool(shell.IsUserAnAdmin())


@contextmanager
def operation_lock(output: Path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    identity = hashlib.sha256(str(output.resolve()).casefold().encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Global\\RenderControlServices-" + identity)
    if not handle:
        raise ReleaseError("service_operation_lock_unavailable")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
        if not acquired:
            raise ReleaseError("service_operation_in_progress")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def owned(state: ServiceState | None, output: Path, identifier: str) -> ServiceState:
    if state is None:
        raise ReleaseError("service_not_installed")
    expected = f'"{output / (identifier + ".exe")}"'
    if state.image_path.casefold() != expected.casefold() or state.account.casefold() != ("NT SERVICE\\" + identifier).casefold():
        raise ReleaseError("service_identity_mismatch")
    return state


def wrapper(output: Path, identifier: str, action: str) -> None:
    if action not in ("install", "start", "stopwait", "uninstall") or not administrator():
        raise ReleaseError("service_administrator_required_no_elevation")
    try:
        subprocess.run([str(output / f"{identifier}.exe"), action], cwd=output, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=CONTROL_TIMEOUT, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- pinned wrapper, fixed allowlisted operation, no shell or automatic UAC
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError("service_control_timeout_state_must_be_queried") from exc
    except subprocess.CalledProcessError as exc:
        raise ReleaseError("service_control_failed_state_must_be_queried") from exc


def wait_for(scm: Any, output: Path, identifier: str, expected: int, deadline: float) -> ServiceState:
    while True:
        current = owned(scm.query(identifier), output, identifier)
        if current.state == expected:
            return current
        if time.monotonic() >= deadline or current.state not in (START_PENDING, STOP_PENDING):
            raise ReleaseError("service_state_not_confirmed")
        time.sleep(0.1)


def operate(action: str, output: Path, spec: dict, selector: str | None, scm: Any) -> dict:
    items = [item for item in spec["services"] if selector is None or service_id(spec, item) == selector]
    if not items:
        raise ReleaseError("service_selector_not_in_deployment")
    if action not in ("status", "install", "start", "stop", "uninstall"):
        raise ReleaseError("service_operation_not_allowed")
    if action != "status" and not administrator():
        raise ReleaseError("service_administrator_required_no_elevation")
    items.sort(key=lambda item: ROLES.index(item["role"]), reverse=action in ("stop", "uninstall"))
    identifiers = [service_id(spec, item) for item in items]
    current = {identifier: scm.query(identifier) for identifier in identifiers}
    if action == "install":
        if any(state is not None for state in current.values()):
            raise ReleaseError("service_existing_registration_refused")
    else:
        for identifier, state in current.items():
            if action != "status" or state is not None:
                owned(state, output, identifier)
    if action == "uninstall" and any(state.state != STOPPED for state in current.values()):
        raise ReleaseError("service_uninstall_requires_stopped")
    if action == "start" and any(state.state not in (STOPPED, RUNNING) for state in current.values()):
        raise ReleaseError("service_start_requires_stable_state")
    if action == "stop" and any(state.state not in (STOPPED, RUNNING, STOP_PENDING) for state in current.values()):
        raise ReleaseError("service_stop_requires_stable_state")
    attempted = []
    results = []
    try:
        for identifier in identifiers:
            deadline = time.monotonic() + CONTROL_TIMEOUT
            state = current[identifier]
            if action == "install":
                attempted.append(identifier)
                wrapper(output, identifier, "install")
                actual = owned(scm.query(identifier), output, identifier)
                item = next(item for item in items if service_id(spec, item) == identifier)
                if actual.state != STOPPED or actual.startup != (3 if item["startup"] == "manual" else 2):
                    raise ReleaseError("service_install_configuration_mismatch")
            elif action == "start":
                owned(scm.query(identifier), output, identifier)
                if state.state == STOPPED:
                    wrapper(output, identifier, "start")
                wait_for(scm, output, identifier, RUNNING, deadline)
            elif action == "stop":
                owned(scm.query(identifier), output, identifier)
                if state.state == RUNNING:
                    wrapper(output, identifier, "stopwait")
                wait_for(scm, output, identifier, STOPPED, deadline)
            elif action == "uninstall":
                if owned(scm.query(identifier), output, identifier).state != STOPPED:
                    raise ReleaseError("service_uninstall_requires_stopped")
                wrapper(output, identifier, "uninstall")
                if scm.query(identifier) is not None:
                    raise ReleaseError("service_uninstall_not_confirmed")
            actual = scm.query(identifier)
            if actual is not None:
                owned(actual, output, identifier)
            results.append({"service_id": identifier, "state": actual.state if actual else None, "pid": actual.pid if actual else None})
    except Exception:
        if action == "install":
            incomplete = False
            for identifier in reversed(attempted):
                try:
                    actual = scm.query(identifier)
                    if actual is not None:
                        if owned(actual, output, identifier).state != STOPPED:
                            raise ReleaseError("service_rollback_requires_stopped")
                        wrapper(output, identifier, "uninstall")
                        if scm.query(identifier) is not None:
                            raise ReleaseError("service_rollback_not_confirmed")
                except Exception:
                    incomplete = True
            raise ReleaseError("service_install_failed_rollback_incomplete" if incomplete else "service_install_failed_owned_registrations_rolled_back") from None
        raise
    return {"action": action, "services": results, "application_health_verified": False, "durable_state_deleted": False}
