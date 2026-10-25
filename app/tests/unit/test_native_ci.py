from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("native_ci_under_test", ROOT / "scripts/native_ci.py")
ci = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ci
spec.loader.exec_module(ci)


def test_ci_lock_is_distinct_from_dispatch_retention_and_migration_locks() -> None:
    from generation_infrastructure.audit_retention import LOCK
    from generation_infrastructure.dispatch import DISPATCH_LOCK

    assert ci.CI_DATABASE_LOCK not in {DISPATCH_LOCK, LOCK, 634720182, 624087029}


def test_child_failure_keeps_output_private_and_does_not_hide_exit(tmp_path: Path) -> None:
    secret = "synthetic-private-output-0123456789"
    gate = ci.Gate("probe", [sys.executable, "-c", f"print({secret!r});raise SystemExit(7)"], tmp_path, 10)
    log = tmp_path / "child.log"
    result = ci.execute(gate, log, dict(os.environ), limit=4096)
    assert result["status"] == "failed" and result["exit_code"] == 7
    assert secret in log.read_text() and secret not in json.dumps(result)
    assert result["owned_process_tree_closed"] and result["log_sha256"] == ci.sha(log)


def test_timeout_kills_owned_descendant_even_when_launcher_exits(tmp_path: Path) -> None:
    pid = tmp_path / "descendant.pid"
    child = "import os,pathlib,time;pathlib.Path(" + repr(str(pid)) + ").write_text(str(os.getpid()));time.sleep(120)"
    launcher = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(120)"
    result = ci.execute(ci.Gate("timeout", [sys.executable, "-c", launcher], tmp_path, 2), tmp_path / "timeout.log", dict(os.environ), limit=4096)
    assert result["status"] == "failed" and result["timed_out"] and result["owned_process_tree_closed"]
    assert pid.exists()
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid.read_text()))
    if handle:
        try:
            exit_code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value != 259
        finally:
            kernel.CloseHandle(handle)


def test_child_log_budget_stops_execution_and_preserves_bounded_evidence(tmp_path: Path) -> None:
    code = "import sys,time;sys.stdout.write('x'*100000);sys.stdout.flush();time.sleep(120)"
    log = tmp_path / "budget.log"
    result = ci.execute(ci.Gate("budget", [sys.executable, "-c", code], tmp_path, 10), log, dict(os.environ), limit=1024)
    assert result["status"] == "failed" and result["log_truncated"]
    assert result["owned_process_tree_closed"] and log.stat().st_size == 1024


def test_checkout_lock_is_exclusive_and_released_after_failure(tmp_path: Path) -> None:
    path = tmp_path / "ci.lock"
    with pytest.raises(RuntimeError), ci.file_lock(path):
        with pytest.raises(ci.CIError, match="ci_checkout_busy"), ci.file_lock(path):
            pass
        raise RuntimeError("synthetic interruption")
    with ci.file_lock(path):
        pass


@pytest.mark.parametrize("value", ["../outside", "abc/defgh", "A1234567", "short", "run;command", "a" * 65])
def test_run_ids_cannot_escape_or_become_shell_input(value: str) -> None:
    with pytest.raises(ci.CIError, match="invalid_run_id"):
        ci.run_id(value)


@pytest.mark.parametrize("xml", [
    "<testsuites/>",
    '<testsuite tests="0"/>',
    '<testsuite tests="3" failures="1"/>',
    '<testsuite tests="3" skipped="1"/>',
    '<testsuite tests="3" errors="1"/>',
    '<testsuite tests="invalid"/>',
    '<!DOCTYPE x [<!ENTITY y "secret">]><testsuite tests="1"/>',
    "<broken>",
])
def test_zero_failed_skipped_and_unsafe_junit_never_become_green(tmp_path: Path, xml: str) -> None:
    path = tmp_path / "junit.xml"
    path.write_text(xml)
    with pytest.raises(ci.CIError):
        ci.junit(path)


def test_successful_junit_reports_counts_without_test_or_error_body(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text('<testsuites><testsuite tests="2"><system-out>private payload</system-out></testsuite><testsuite tests="3"/></testsuites>')
    assert ci.junit(path) == {"tests": 5, "errors": 0, "failures": 0, "skipped": 0}


@pytest.fixture
def workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    policy = json.loads((ROOT / "config/native-ci.json").read_bytes())
    value = yaml.safe_load((ROOT.parent / ".github/workflows/native-ci.yml").read_bytes())
    path = tmp_path / ".github/workflows/native-ci.yml"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    return policy, value, path


@pytest.mark.parametrize("change", ["pr", "write", "hosted_vm", "container", "credential", "raw_logs", "cancel", "script_input", "mutable_action", "wide_branch", "extra_step_env"])
def test_workflow_does_not_widen_execution_or_artifact_authority(workflow, change: str) -> None:
    policy, value, path = workflow
    job = value["jobs"]["verify"]
    if change == "pr":
        value[True]["pull_request"] = None
    elif change == "write":
        value["permissions"]["contents"] = "write"
    elif change == "hosted_vm":
        job["runs-on"] = "windows-latest"
    elif change == "container":
        job["container"] = "postgres"
    elif change == "credential":
        job["steps"][0]["with"]["persist-credentials"] = True
    elif change == "raw_logs":
        job["steps"][3]["with"]["path"] = "app/.local/**"
    elif change == "cancel":
        value["concurrency"]["cancel-in-progress"] = True
    elif change == "script_input":
        job["steps"][1]["run"] = "${{ github.event.pull_request.title }}"
    elif change == "mutable_action":
        job["steps"][0]["uses"] = "actions/checkout@v6"
    elif change == "wide_branch":
        value[True]["push"]["branches"] = ["**"]
    elif change == "extra_step_env":
        job["steps"][1]["env"] = {"PYTHONPATH": "untrusted"}
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(ci.CIError, match="unsafe_workflow_policy"):
        ci.workflow_policy(policy)


def test_current_workflow_keeps_exact_trust_and_artifact_boundaries(workflow) -> None:
    policy, value, path = workflow
    path.write_text(yaml.safe_dump(value))
    ci.workflow_policy(policy)


def test_first_failed_gate_is_durable_and_later_gates_remain_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    app = tmp_path / "app"
    (app / "config").mkdir(parents=True)
    (app / "config/native-ci.json").write_bytes((ROOT / "config/native-ci.json").read_bytes())
    monkeypatch.setattr(ci, "APP", app)
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(ci, "workflow_policy", lambda _: None)
    monkeypatch.setattr(ci, "database_lock", nullcontext)
    monkeypatch.setattr(ci, "media", lambda: 4)
    monkeypatch.setattr(ci, "git", lambda arguments: b"a" * 40 if arguments[0] == "rev-parse" else b"")
    monkeypatch.setattr(ci, "snapshot", lambda: {"app/file.py": "b" * 64})
    monkeypatch.setattr(ci, "execute", lambda gate, *args, **kwargs: {"name": gate.name, "status": "failed", "exit_code": 9, "seconds": 0.01})
    monkeypatch.setattr(sys, "argv", ["native_ci.py", "--run-id", "unit-failure-123"])
    assert ci.main() == 1
    report = json.loads((tmp_path / "docs/evidence/native-ci/unit-failure-123/summary.json").read_bytes())
    assert not report["passed"] and report["state"] == "failed"
    assert report["gates"][0]["status"] == "failed" and all(x["status"] == "not_run" for x in report["gates"][1:])
    assert report["raw_logs_uploaded"] is False and report["failure_code"] == "required_gate_failed"
    assert '"passed": false' in capsys.readouterr().out


def test_path_guard_refuses_parent_and_outside(tmp_path: Path) -> None:
    with pytest.raises(ci.CIError):
        ci.inside(tmp_path, tmp_path)
    with pytest.raises(ci.CIError):
        ci.inside(tmp_path.parent / "outside", tmp_path)


@pytest.mark.parametrize("script", ["sync-contracts.py", "export-openapi.py"])
@pytest.mark.parametrize("drift", [False, True])
def test_generated_check_is_read_only_and_rejects_stale_files(tmp_path: Path, script: str, drift: bool) -> None:
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "config").mkdir()
    target = app / "scripts" / script
    shutil.copyfile(ROOT / "scripts" / script, target)
    if script == "sync-contracts.py":
        shutil.copytree(ROOT.parent / "docs/contracts", tmp_path / "docs/contracts")
        initial = subprocess.run([sys.executable, str(target)], cwd=app, capture_output=True, timeout=30, check=False)  # noqa: S603 -- owned copy of the fixed generator
        assert initial.returncode == 0
        generated = app / "config/contract-hashes.json"
    else:
        for name in ("openapi.json", "openapi.sha256"):
            shutil.copyfile(ROOT / "config" / name, app / "config" / name)
        generated = app / "config/openapi.json"
    if drift:
        generated.write_bytes(generated.read_bytes() + b" ")
    before = {x.relative_to(app).as_posix(): ci.sha(x) for x in app.rglob("*") if x.is_file()}
    result = subprocess.run([sys.executable, str(target), "--check"], cwd=app, capture_output=True, timeout=30, check=False)  # noqa: S603 -- actual fixed --check CLI, no shell
    assert (result.returncode != 0) is drift
    assert before == {x.relative_to(app).as_posix(): ci.sha(x) for x in app.rglob("*") if x.is_file()}


@pytest.mark.parametrize("environment,url", [
    ("production", "postgresql+psycopg://u@127.0.0.1:55438/render_control"),
    ("development", "postgresql+psycopg://u@192.0.2.1:55438/render_control"),
    ("development", "postgresql+psycopg://u@127.0.0.1:5432/render_control"),
    ("development", "postgresql+psycopg://u@127.0.0.1:55438/customer_database"),
    ("development", "mysql://u@127.0.0.1:55438/render_control"),
])
def test_ci_refuses_wrong_database_before_any_connection(monkeypatch: pytest.MonkeyPatch, environment: str, url: str) -> None:
    from types import SimpleNamespace

    from pydantic import SecretStr

    monkeypatch.setattr("generation_infrastructure.settings.Settings", lambda: SimpleNamespace(environment=environment, database_url=SecretStr(url)))
    with pytest.raises(ci.CIError, match="unsafe_ci_database"):
        ci.test_url()


def test_venv_dispatcher_cannot_spawn_outside_owned_job_before_assignment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    survived = tmp_path / "survived-timeout"
    original = ci.ProcessTree.assign

    def delayed_assignment(tree: object, process: subprocess.Popen) -> None:
        time.sleep(1)
        original(tree, process)

    monkeypatch.setattr(ci.ProcessTree, "assign", delayed_assignment)
    code = "import pathlib,time;time.sleep(5);pathlib.Path(" + repr(str(survived)) + ").write_text('escaped');time.sleep(1)"
    result = ci.execute(ci.Gate("dispatcher", [str(ROOT / ".venv/Scripts/python.exe"), "-c", code], tmp_path, 3), tmp_path / "dispatcher.log", dict(os.environ), limit=4096)
    assert result["timed_out"] and result["owned_process_tree_closed"]
    assert not survived.exists(), "The actual interpreter survived its assigned launcher"


def test_small_flushed_log_excess_is_stopped_before_child_continues(tmp_path: Path) -> None:
    continued = tmp_path / "continued-after-log-excess"
    code = "import pathlib,sys,time;sys.stdout.write('x'*1025);sys.stdout.flush();time.sleep(3);pathlib.Path(" + repr(str(continued)) + ").write_text('continued')"
    log = tmp_path / "small-excess.log"
    result = ci.execute(ci.Gate("small-log", [sys.executable, "-c", code], tmp_path, 10), log, dict(os.environ), limit=1024)
    assert result["log_truncated"] and not result["timed_out"] and result["owned_process_tree_closed"]
    assert log.stat().st_size == 1024
    assert not continued.exists(), "Flushed bytes below the pipe buffer still exceed the log budget"


@pytest.mark.parametrize("failure,code", [(subprocess.TimeoutExpired(["PRIVATE-COMMAND"], 1), "ci_command_timeout"), (OSError("PRIVATE-IO"), "ci_io_failed"), (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "PRIVATE-ENCODING"), "ci_encoding_failed")])
def test_preflight_failure_exports_only_owned_phase_and_fixed_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: Exception, code: str) -> None:
    app = tmp_path / "app"
    (app / "config").mkdir(parents=True)
    (app / "config/native-ci.json").write_bytes((ROOT / "config/native-ci.json").read_bytes())
    monkeypatch.setattr(ci, "APP", app)
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(ci, "workflow_policy", lambda _: None)
    monkeypatch.setattr(ci, "database_lock", nullcontext)
    monkeypatch.setattr(ci, "git", lambda _: b"a" * 40)
    def fail_snapshot() -> None:
        raise failure
    monkeypatch.setattr(ci, "snapshot", fail_snapshot)
    monkeypatch.setattr(sys, "argv", ["native_ci.py", "--run-id", "unit-preflight-123"])
    assert ci.main() == 1
    report = json.loads((tmp_path / "docs/evidence/native-ci/unit-preflight-123/summary.json").read_bytes())
    assert report["preflight_phase"] == "source_snapshot" and report["failure_code"] == code
    assert all(gate["status"] == "not_run" for gate in report["gates"])
    assert "PRIVATE" not in json.dumps(report) + capsys.readouterr().out
