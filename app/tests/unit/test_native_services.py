from __future__ import annotations

import copy
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import venv
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import native_services as services  # noqa: E402 -- repository script under test

CONTRACTS = ROOT.parent / "docs/contracts"
CASES = [case for case in json.loads((CONTRACTS / "fixtures.json").read_bytes())["cases"] if case["schema"] == "service-deployment.schema.json"]
VALIDATOR = Draft202012Validator(json.loads((CONTRACTS / "service-deployment.schema.json").read_bytes()))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_native_service_contract_and_stdlib_boundary_agree(case: dict) -> None:
    assert VALIDATOR.is_valid(case["data"]) == case["valid"]
    if case["valid"]:
        assert services.validate_spec(copy.deepcopy(case["data"])) == case["data"]
    else:
        with pytest.raises(services.ReleaseError):
            services.validate_spec(copy.deepcopy(case["data"]))


@pytest.mark.parametrize("path", ["C:/a/../secret", "C:/a/./secret", "C:/a//secret", "C:/a/secret.", "C:/a/secret ", "C:/a/ con.txt", "C:/a/CON.txt", "C:/a/LPT1", "C:/a/file:ads", "//host/secret", "\\\\?\\C:\\secret", "C:/a/%BASE%", "C:/a/secret\r.txt", "C:/a/*.txt"])
def test_service_paths_reject_windows_aliases_expansion_and_devices(path: str) -> None:
    with pytest.raises(services.ReleaseError, match="service_path_invalid"):
        services.windows_path(path)


@pytest.mark.parametrize("mutator,code", [
    (lambda spec: spec["services"].append(copy.deepcopy(spec["services"][0])), "duplicate_identity"),
    (lambda spec: spec["services"].append({**spec["services"][0], "instance": "second"}), "directories_overlap"),
    (lambda spec: spec["services"].append({**spec["services"][0], "instance": "second", "state_dir": "C:/render/state/runner/nested"}), "directories_overlap"),
])
def test_service_cross_field_identity_and_state_aliases(mutator, code: str) -> None:
    spec = copy.deepcopy(CASES[0]["data"])
    mutator(spec)
    with pytest.raises(services.ReleaseError, match=code):
        services.validate_spec(spec)


@pytest.mark.parametrize("host,allowed", [("0.0.0.0", False), ("192.0.2.1", False), ("::", False), ("localhost", True), ("host.invalid", True)])  # noqa: S104 -- rejection of unapproved network binding
def test_service_bind_is_an_ip_literal_and_network_binding_is_explicit(host: str, allowed: bool) -> None:
    spec = copy.deepcopy(next(case["data"] for case in CASES if case["id"] == "native_services_api"))
    spec["services"][0]["listen"].update(host=host, allow_network_bind=allowed)
    with pytest.raises(services.ReleaseError):
        services.validate_spec(spec)


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Path]:
    installation = tmp_path / "install"
    tools = installation / "payload/service-tools"
    tools.mkdir(parents=True)
    (tools / "WinSW.NET461.exe").write_bytes(b"synthetic-wrapper-unit-fixture-only")
    private = tmp_path / "private"
    private.mkdir()
    config = private / "cloud.env"
    config.write_bytes(b"test-private-config-not-a-production-credential")
    spec = {"protocol": "native-services-v1", "deployment_id": "test", "installation": str(installation), "services": []}
    for role in ("runner", "gc", "worker"):
        state = tmp_path / "state" / role
        state.mkdir(parents=True)
        (state / "journal.keep").write_bytes(b"unknown-submit-must-survive")
        spec["services"].append({"role": role, "instance": "main", "config": str(config), "state_dir": str(state), "startup": "manual"})
    monkeypatch.setattr(services, "installed", lambda value: (installation, "a" * 64))
    return spec, tmp_path / "services"


def test_render_and_validate_are_deterministic_without_scm_or_secret_copy(deployment: tuple[dict, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    spec, output = deployment
    monkeypatch.setattr(services, "ServiceControl", lambda: pytest.fail("render must not query/register services"))
    result = services.render(spec, output)
    assert result["services"] == 3 and not result["services_registered"] and not result["permissions_applied"]
    assert services.load_deployment(output) == spec
    for item in spec["services"]:
        xml = ET.fromstring((output / (services.service_id(spec, item) + ".xml")).read_bytes())  # noqa: S314 -- locally generated fixed XML under this test's private directory
        assert xml.findtext("executable") == str(Path(spec["installation"]) / "venv/Scripts/python-service.exe")
        assert "service-process.py" in xml.findtext("arguments")
        assert xml.findtext("hidewindow") == "true"
        assert xml.findtext("serviceaccount/domain") == "NT SERVICE"
        assert xml.find("serviceaccount/password") is None
        assert xml.findtext("serviceaccount/allowservicelogon") == "false"
        assert [node.attrib["action"] for node in xml.findall("onfailure")] == ["restart", "restart", "restart", "none"]
        assert xml.findtext("stopparentprocessfirst") == "true"
        assert xml.findtext("stoptimeout") == "120 sec"
        assert not xml.findall("download") and not xml.findall("extensions")
        assert b"test-private-config-not-a-production-credential" not in (output / (services.service_id(spec, item) + ".xml")).read_bytes()
        assert (Path(item["state_dir"]) / "journal.keep").read_bytes() == b"unknown-submit-must-survive"
    with pytest.raises(services.ReleaseError, match="must_be_new"):
        services.render(spec, output)


@pytest.mark.parametrize("member", ["spec.json", "acl-plan.json", "rc-test-runner-main.xml", "rc-test-runner-main.exe"])
def test_deployment_tampering_is_rejected_even_with_rewritten_inventory(deployment: tuple[dict, Path], member: str) -> None:
    spec, output = deployment
    services.render(spec, output)
    path = output / member
    if member == "spec.json":
        data = json.loads(path.read_bytes())
        data["services"][0]["instance"] = "changed"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif member == "acl-plan.json":
        data = json.loads(path.read_bytes())
        data["permissions_applied"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b"altered")
    manifest_path = output / "deployment.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][member] = services.digest_file(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((services.ReleaseError, FileNotFoundError)):
        services.load_deployment(output)


def test_deployment_rejects_extra_files_and_installation_digest_change(deployment: tuple[dict, Path]) -> None:
    spec, output = deployment
    services.render(spec, output)
    extra = output / "arbitrary.ps1"
    extra.write_bytes(b"not executed")
    with pytest.raises(services.ReleaseError, match="inventory_mismatch"):
        services.load_deployment(output)
    extra.unlink()
    path = output / "deployment.json"
    manifest = json.loads(path.read_bytes())
    manifest["installation_manifest_sha256"] = "b" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(services.ReleaseError, match="installation_mismatch"):
        services.load_deployment(output)


def test_render_rejects_secrets_inside_writable_state_and_output_inside_installation(deployment: tuple[dict, Path]) -> None:
    spec, output = deployment
    secret = Path(spec["services"][0]["state_dir"]) / "bad.env"
    secret.write_bytes(b"private")
    bad = copy.deepcopy(spec)
    bad["services"][0]["config"] = str(secret)
    with pytest.raises(services.ReleaseError, match="secret_path_not_external"):
        services.render(bad, output)
    with pytest.raises(services.ReleaseError, match="output_inside_installation"):
        services.render(spec, Path(spec["installation"]) / "services")
    assert not output.exists()


def test_reparse_ancestor_is_rejected_before_resolving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.lstat

    def lstat(path: Path, *args, **kwargs):
        if path == tmp_path:
            return SimpleNamespace(st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(services.ReleaseError, match="reparse_point"):
        services.local_path(str(tmp_path / "child"), kind="new")


class FakeSCM:
    def __init__(self, output: Path):
        self.output = output
        self.states: dict[str, services.ServiceState] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_after_create: str | None = None

    def query(self, identifier: str) -> services.ServiceState | None:
        return self.states.get(identifier)

    def invoke(self, output: Path, identifier: str, action: str) -> None:
        assert output == self.output
        self.calls.append((identifier, action))
        state = services.STOPPED if action in ("install", "stopwait") else services.RUNNING
        self.states[identifier] = services.ServiceState(f'"{output / (identifier + ".exe")}"', "NT SERVICE\\" + identifier, state)
        if action == "uninstall":
            del self.states[identifier]
        if action == "install" and identifier == self.fail_after_create:
            raise services.ReleaseError("injected_partial_install")


@pytest.fixture
def scm(deployment: tuple[dict, Path], monkeypatch: pytest.MonkeyPatch) -> FakeSCM:
    value = FakeSCM(deployment[1])
    monkeypatch.setattr(services, "administrator", lambda: True)
    monkeypatch.setattr(services, "wrapper", value.invoke)
    return value


def test_install_start_stop_uninstall_orders_and_retains_journal(deployment: tuple[dict, Path], scm: FakeSCM) -> None:
    spec, output = deployment
    for action in ("install", "start", "stop", "uninstall"):
        result = services.operate(action, output, spec, None, scm)
        assert not result["application_health_verified"] and not result["durable_state_deleted"]
    assert [identifier for identifier, action in scm.calls if action == "install"] == ["rc-test-worker-main", "rc-test-gc-main", "rc-test-runner-main"]
    assert [identifier for identifier, action in scm.calls if action == "stopwait"] == ["rc-test-runner-main", "rc-test-gc-main", "rc-test-worker-main"]
    assert not scm.states
    assert all((Path(item["state_dir"]) / "journal.keep").read_bytes() == b"unknown-submit-must-survive" for item in spec["services"])


def test_combined_deployment_keeps_api_running_until_worker_stops(deployment: tuple[dict, Path], scm: FakeSCM, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, output = deployment
    for role in ("gateway", "api"):
        item = {**spec["services"][0], "role": role, "state_dir": str(output.parent / "state" / role)}
        if role == "api":
            item["listen"] = {"host": "127.0.0.1", "port": 8443, "tls_cert": item["config"], "tls_key": item["config"], "allow_network_bind": False}
        else:
            item["native_config"] = item["config"]
        spec["services"].append(item)
    services.validate_spec(spec)

    def invoke(path, identifier, action):
        if identifier == "rc-test-worker-main" and action in ("start", "stopwait"):
            assert scm.states["rc-test-api-main"].state == services.RUNNING
        scm.invoke(path, identifier, action)

    monkeypatch.setattr(services, "wrapper", invoke)
    for action in ("install", "start", "stop", "uninstall"):
        services.operate(action, output, spec, None, scm)
    assert [identifier for identifier, action in scm.calls if action == "start"] == ["rc-test-api-main", "rc-test-worker-main", "rc-test-gc-main", "rc-test-runner-main", "rc-test-gateway-main"]
    assert [identifier for identifier, action in scm.calls if action == "stopwait"] == ["rc-test-gateway-main", "rc-test-runner-main", "rc-test-gc-main", "rc-test-worker-main", "rc-test-api-main"]
    assert not scm.states


def test_install_failure_after_registration_rolls_back_only_owned_current_batch(deployment: tuple[dict, Path], scm: FakeSCM) -> None:
    spec, output = deployment
    foreign = "foreign-service-outside-deployment"
    scm.states[foreign] = services.ServiceState("foreign.exe", "LocalSystem", services.RUNNING)
    scm.fail_after_create = "rc-test-gc-main"
    with pytest.raises(services.ReleaseError, match="owned_registrations_rolled_back"):
        services.operate("install", output, spec, None, scm)
    assert list(scm.states) == [foreign]
    assert not any(identifier == foreign for identifier, _ in scm.calls)
    assert [(identifier, action) for identifier, action in scm.calls if action == "uninstall"] == [("rc-test-gc-main", "uninstall"), ("rc-test-worker-main", "uninstall")]


def test_existing_name_and_foreign_identity_are_never_adopted(deployment: tuple[dict, Path], scm: FakeSCM) -> None:
    spec, output = deployment
    scm.states["rc-test-gc-main"] = services.ServiceState("foreign.exe", "LocalSystem", services.RUNNING)
    for action in ("install", "start", "stop", "status", "uninstall"):
        with pytest.raises(services.ReleaseError, match="existing_registration|identity_mismatch|not_installed"):
            services.operate(action, output, spec, None, scm)
    assert not scm.calls


@pytest.mark.parametrize("identity", [("foreign.exe", "NT SERVICE\\rc-test-worker-main"), ('"C:/foreign.exe"', "LocalSystem")])
def test_mismatched_registration_during_failed_install_is_retained_for_operator(deployment: tuple[dict, Path], scm: FakeSCM, monkeypatch: pytest.MonkeyPatch, identity: tuple[str, str]) -> None:
    spec, output = deployment

    def conflicting_registration(_output, identifier, _action):
        scm.states[identifier] = services.ServiceState(*identity, services.STOPPED)
        raise services.ReleaseError("injected")

    monkeypatch.setattr(services, "wrapper", conflicting_registration)
    with pytest.raises(services.ReleaseError, match="rollback_incomplete"):
        services.operate("install", output, spec, None, scm)
    assert len(scm.states) == 1 and not scm.calls


@pytest.mark.parametrize("action", ["install", "start", "stop", "uninstall"])
def test_non_administrator_never_launches_wrapper_or_uac(deployment: tuple[dict, Path], scm: FakeSCM, monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    monkeypatch.setattr(services, "administrator", lambda: False)
    with pytest.raises(services.ReleaseError, match="administrator_required_no_elevation"):
        services.operate(action, deployment[1], deployment[0], None, scm)
    assert not scm.calls


def test_selector_is_bounded_and_running_uninstall_is_rejected(deployment: tuple[dict, Path], scm: FakeSCM) -> None:
    spec, output = deployment
    with pytest.raises(services.ReleaseError, match="selector_not_in_deployment"):
        services.operate("stop", output, spec, "foreign", scm)
    services.operate("install", output, spec, "rc-test-worker-main", scm)
    services.operate("start", output, spec, "rc-test-worker-main", scm)
    calls = scm.calls.copy()
    with pytest.raises(services.ReleaseError, match="uninstall_requires_stopped"):
        services.operate("uninstall", output, spec, "rc-test-worker-main", scm)
    assert scm.calls == calls


def test_control_timeout_does_not_force_kill_or_clear_state(deployment: tuple[dict, Path], scm: FakeSCM, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, output = deployment
    services.operate("install", output, spec, "rc-test-worker-main", scm)
    identifier = "rc-test-worker-main"
    scm.states[identifier] = services.ServiceState(scm.states[identifier].image_path, scm.states[identifier].account, services.STOP_PENDING)
    with pytest.raises(services.ReleaseError, match="state_not_confirmed"):
        services.wait_for(scm, output, identifier, services.STOPPED, time.monotonic() - 1)
    assert scm.states[identifier].state == services.STOP_PENDING
    assert all((Path(item["state_dir"]) / "journal.keep").exists() for item in spec["services"])


def test_wrapper_argv_is_fixed_and_output_is_discarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = []
    monkeypatch.setattr(services, "administrator", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: recorded.append((args, kwargs)))
    services.wrapper(tmp_path, "rc-test-gc-main", "stopwait")
    assert recorded[0][0] == [str(tmp_path / "rc-test-gc-main.exe"), "stopwait"]
    assert recorded[0][1]["stdout"] == subprocess.DEVNULL and recorded[0][1]["stderr"] == subprocess.DEVNULL
    assert "shell" not in recorded[0][1] and recorded[0][1]["timeout"] == 150
    with pytest.raises(services.ReleaseError):
        services.wrapper(tmp_path, "rc-test-gc-main", "restart!")
    assert len(recorded) == 1


def test_real_readonly_scm_query_and_native_mutex_have_no_registration_side_effect(tmp_path: Path) -> None:
    identifier = "rc-test-absent-" + tmp_path.name[-16:].replace("_", "-")
    assert services.ServiceControl().query(identifier) is None

    def other_thread() -> str:
        try:
            with services.operation_lock(tmp_path):
                return "unexpected_acquisition"
        except services.ReleaseError as exc:
            return str(exc)

    with services.operation_lock(tmp_path), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(other_thread).result(timeout=5) == "service_operation_in_progress"
    with services.operation_lock(tmp_path):
        pass


def test_service_cli_rejects_mutation_before_loading_any_untrusted_deployment(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    definition = importlib.util.spec_from_file_location("service_cli_under_test", ROOT / "scripts/service-deploy.py")
    assert definition and definition.loader
    module = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(module)
    monkeypatch.setattr(module, "administrator", lambda: False)
    monkeypatch.setattr(module, "load_deployment", lambda *_: pytest.fail("no filesystem/SCM mutation before admin check"))
    monkeypatch.setattr(sys, "argv", ["service-deploy.py", "install", "--deployment", "C:/never-created"])
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 1
    assert json.loads(capsys.readouterr().err) == {"error": "service_administrator_required_no_elevation"}


def test_gateway_supervisor_environment_excludes_inherited_credentials_and_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_PASSWORD", "SERVER_HOST", "GEN_DATABASE_URL", "CONFIG_FILE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SUB2API_RENDER_CONTROL_CONFIG", "PYTHONPATH"):
        monkeypatch.setenv(key, "inherited-private-value-must-not-reach-provider")
    environment = services.gateway_environment(tmp_path, tmp_path / "provider.json", tmp_path / "state", tmp_path / "native.yaml")
    assert "inherited-private-value-must-not-reach-provider" not in environment.values()
    assert environment["CONFIG_FILE"] == str(tmp_path / "native.yaml")
    assert environment["SUB2API_RENDER_CONTROL_CONFIG"] == str(tmp_path / "provider.json")
    assert "DATABASE_PASSWORD" not in environment and "HTTP_PROXY" not in environment


@pytest.mark.parametrize("mode", ["normal", "graceful", "stop_during_spawn", "timeout", "failed"])
def test_gateway_supervisor_signals_deadline_and_owned_child_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    handlers = {}
    clock = [0.0]
    forwarded, killed, started = [], [], []

    class Child:
        returncode = 0 if mode == "normal" else (4 if mode == "failed" else None)

        def poll(self):
            return self.returncode

        def send_signal(self, kind):
            forwarded.append(kind)
            if mode != "timeout":
                self.returncode = 0

        def kill(self):
            killed.append(True)
            self.returncode = 9

        def wait(self, *, timeout):
            assert timeout <= 90
            return self.returncode

    def register(kind, handler):
        old = handlers.get(kind, "prior-handler")
        handlers[kind] = handler
        return old

    def spawn(arguments, **kwargs):
        started.append((arguments, kwargs))
        if mode == "stop_during_spawn":
            handlers[signal.SIGINT](signal.SIGINT, None)
        return Child()

    def tick(_seconds):
        if not forwarded:
            handlers[signal.SIGINT](signal.SIGINT, None)
        clock[0] += 91

    monkeypatch.setattr(services.signal, "signal", register)
    monkeypatch.setattr(services.subprocess, "Popen", spawn)
    monkeypatch.setattr(services.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(services.time, "sleep", tick)
    if mode in ("timeout", "failed"):
        with pytest.raises(services.ReleaseError, match="graceful_shutdown_timeout" if mode == "timeout" else "gateway_process_failed"):
            services.run_gateway(tmp_path, tmp_path / "provider.json", tmp_path / "state", tmp_path / "native.yaml")
    else:
        services.run_gateway(tmp_path, tmp_path / "provider.json", tmp_path / "state", tmp_path / "native.yaml")
    assert all(value == "prior-handler" for value in handlers.values())
    assert bool(killed) == (mode == "timeout")
    assert forwarded == ([] if mode in ("normal", "failed") else [signal.CTRL_BREAK_EVENT])
    assert started[0][0] == [str(tmp_path / "gateway/sub2api-provider.exe")]
    assert started[0][1]["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP


@pytest.mark.parametrize("already_attached", [False, True])
def test_service_process_only_hides_and_frees_its_own_console(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], already_attached: bool) -> None:
    definition = importlib.util.spec_from_file_location("service_process_under_test", ROOT / "scripts/service-process.py")
    assert definition and definition.loader
    module = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(module)
    calls = []
    kernel = SimpleNamespace(GetConsoleProcessList=lambda *_: int(already_attached), AllocConsole=lambda: calls.append("allocate") or True, FreeConsole=lambda: calls.append("free") or True, GetConsoleWindow=lambda: 42)
    user = SimpleNamespace(ShowWindow=lambda window, mode: calls.append((window, mode)))
    monkeypatch.setattr(module.ctypes, "WinDLL", lambda name, **_: kernel if name == "kernel32" else user)
    monkeypatch.setattr(module.runpy, "run_path", lambda path, **kwargs: calls.append((Path(path).name, kwargs["run_name"])))
    if already_attached:
        with pytest.raises(SystemExit):
            module.main()
        assert calls == [] and json.loads(capsys.readouterr().err)["error"] == "service_private_console_required"
    else:
        module.main()
        assert calls == ["allocate", (42, 0), ("release-entrypoint.py", "__main__"), "free"]


def test_service_process_accepts_only_self_attached_console(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = importlib.util.spec_from_file_location("own_console_process_under_test", ROOT / "scripts/service-process.py")
    assert definition and definition.loader
    module = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(module)
    calls = []

    def own_console(buffer, _size):
        buffer[0] = os.getpid()
        return 1

    kernel = SimpleNamespace(GetConsoleProcessList=own_console, AllocConsole=lambda: pytest.fail("own existing console must not be reallocated"), FreeConsole=lambda: calls.append("free") or True, GetConsoleWindow=lambda: 42)
    user = SimpleNamespace(ShowWindow=lambda window, mode: calls.append((window, mode)))
    monkeypatch.setattr(module.ctypes, "WinDLL", lambda name, **_: kernel if name == "kernel32" else user)
    monkeypatch.setattr(module.runpy, "run_path", lambda *_args, **_kwargs: calls.append("entry"))
    module.main()
    assert calls == [(42, 0), "entry", "free"]


def test_real_copied_service_python_preserves_venv_and_tampering_is_rejected(tmp_path: Path) -> None:
    import release_bundle

    installation = tmp_path / "installation"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(installation / "venv")
    receipt = release_bundle.prepare_service_python(installation)
    executable = release_bundle.verify_service_python(installation, receipt)
    result = subprocess.run([str(executable), "-I", "-c", "import json,sqlite3,ssl,sys;print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))"], capture_output=True, check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- newly created native venv and fixed probe, no services or shell
    assert Path(json.loads(result.stdout)["prefix"]) == installation / "venv"
    assert Path(json.loads(result.stdout)["base"]) == Path(sys.base_prefix)
    target = installation / "venv/Scripts/python3.dll"
    target.write_bytes(target.read_bytes() + b"tamper")
    with pytest.raises(release_bundle.ReleaseError, match="runtime_mismatch"):
        release_bundle.verify_service_python(installation, receipt)
    assert (installation / "venv/Scripts/python.exe").is_file()
