from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("development_evidence_under_test", ROOT / "docs/tools/development_evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def write_json(root: Path, name: str, value: dict) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(root: Path, name: str, value: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def binding(root: Path, path: str, **extra) -> dict:
    return {"path": path, "sha256": evidence.sha256((root / path).read_bytes()), **extra}


def rebind(root: Path, *, matrix: bool = False) -> None:
    baseline = evidence.read_json(root, "docs/evidence/baseline.json")
    for ref in baseline["reports"].values():
        ref["sha256"] = binding(root, ref["path"])["sha256"]
    write_json(root, "docs/evidence/baseline.json", baseline)
    registry = evidence.read_json(root, evidence.REGISTRY)
    registry["baseline_index"] = binding(root, "docs/evidence/baseline.json")
    write_json(root, evidence.REGISTRY, registry)
    if matrix:
        write_text(root, evidence.MATRIX, "Introduction\n" + evidence.matrix(registry) + "\nHistory\n")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    write_text(tmp_path, evidence.SPEC, "| CAP-01 | Generate |\n")
    write_text(tmp_path, evidence.ACCEPTANCE, "| FUNC-01 | Real result |\n")
    write_text(tmp_path, evidence.QUALITY, "| QC-01 | Race |\n")
    write_text(tmp_path, evidence.BACKLOG, "| 18 | product | Real workflow |\n")
    write_json(tmp_path, evidence.TRACE, {"requirements": [{"id": "CAP-01", "acceptance": ["FUNC-01", "QC-01"]}]})
    write_text(tmp_path, "app/runtime.py", "value = 1\n")
    write_text(tmp_path, "app/tests/test_runtime.py", "def test_protocol():\n    assert 1\n")
    write_text(tmp_path, "app/ui.vue", "<template>Local UI</template>\n")
    go_dir = "app/integrations/sub2api/overlay/backend/internal/rendercontrol/"
    write_text(tmp_path, go_dir + "probe.go", "package rendercontrol\n")
    write_text(tmp_path, go_dir + "probe_test.go", "package rendercontrol\nfunc TestProbe(t *testing.T) {}\n")
    reports = {
        "ci86": {
            "passed": True, "real_resource_acceptance": False, "sources_unchanged": True,
            "python": {"tests": 2, "errors": 0, "failures": 0, "skipped": 0},
            "gates": [{"name": "python", "status": "passed", "log_sha256": "c" * 64}],
            "source_sha256": {path: binding(tmp_path, path)["sha256"] for path in ["app/runtime.py", "app/tests/test_runtime.py"]},
        },
        "go80": {"passed": True, "runtime_integration_verified": False, "tests": {"TestProbe": {"result": "pass"}}, "overlay_files_sha256": {"internal/rendercontrol/" + name: binding(tmp_path, go_dir + name)["sha256"] for name in ["probe.go", "probe_test.go"]}},
        "installed87": {"passed": True, "browser_closed": True},
        "visual87": {"passed": True, "candidate": {"archive_sha256": "a" * 64, "production_accepted": False}},
    }
    for key, value in reports.items():
        write_json(tmp_path, f"docs/evidence/{key}.json", value)
    baseline = {
        "reports": {key: binding(tmp_path, f"docs/evidence/{key}.json", layer="local_ci" if key == "ci86" else "local_component" if key == "go80" else "local_browser") for key in reports},
        "python_tests": {"source_report": "ci86", "junit_sha256": "b" * 64, "python_log_sha256": "c" * 64, "case_count": 2, "items": [{"path": "app/tests/test_runtime.py", "symbol": "test_protocol", "passed_cases": 2}]},
        "admin_sources": {"archive_sha256": "a" * 64, "source_sha256": {"app/ui.vue": binding(tmp_path, "app/ui.vue")["sha256"]}},
    }
    write_json(tmp_path, "docs/evidence/baseline.json", baseline)
    registry = {
        "format_version": 1, "full_acceptance": "pending", "baseline_index": binding(tmp_path, "docs/evidence/baseline.json"),
        "requirements": [{
            "id": "CAP-01", "local_status": "framework", "limitation": "Needs actual output",
            "implementation": [{"path": "app/runtime.py", "baseline": "ci86"}, {"path": "app/ui.vue", "baseline": "admin87"}, {"path": go_dir + "probe.go", "baseline": "go80"}],
            "tests": [{"path": "app/tests/test_runtime.py", "symbol": "test_protocol", "language": "python"}, {"path": go_dir + "probe_test.go", "symbol": "TestProbe", "language": "go"}],
            "evidence": list(reports), "remaining_backlog": ["18"],
        }],
        "gates": [{"id": key, "status": "pending_full_acceptance", "requirement_ids": ["CAP-01"], "local_evidence": list(reports), "remaining_backlog": ["18"], "remaining_evidence": "Actual controlled run"} for key in ["FUNC-01", "QC-01"]],
    }
    write_json(tmp_path, evidence.REGISTRY, registry)
    rebind(tmp_path, matrix=True)
    return tmp_path


def test_repository_mapping_checks_actual_current_inputs_without_private_artifacts() -> None:
    result = evidence.audit(ROOT)
    assert result["passed"], result["errors"]
    assert result["counts"]["requirements"] == 73
    assert result["counts"]["acceptance_gates"] == 98
    assert result["full_acceptance"] == "pending" and result["real_resource_acceptance"] is False


def test_valid_map_is_read_only_and_does_not_execute_test_sources(repository: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Offline audit spawned a process"))
    before = {path.relative_to(repository): path.read_bytes() for path in repository.rglob("*") if path.is_file()}
    result = evidence.audit(repository)
    assert result["passed"], result["errors"]
    assert result["counts"]["named_test_references"] == 2
    assert before == {path.relative_to(repository): path.read_bytes() for path in repository.rglob("*") if path.is_file()}


@pytest.mark.parametrize("path", ["app/runtime.py", "app/tests/test_runtime.py", "app/ui.vue", "app/integrations/sub2api/overlay/backend/internal/rendercontrol/probe.go", "app/integrations/sub2api/overlay/backend/internal/rendercontrol/probe_test.go"])
def test_source_drift_invalidates_historical_evidence(repository: Path, path: str) -> None:
    with (repository / path).open("a", encoding="utf-8") as target:
        target.write("\n# changed\n" if path.endswith(".py") else "\n// changed\n")
    result = evidence.audit(repository)
    assert not result["passed"] and any("source evidence drift" in message for message in result["errors"])


def test_missing_python_function_cannot_pass_by_refreshing_only_its_hash(repository: Path) -> None:
    write_text(repository, "app/tests/test_runtime.py", "def test_other():\n    assert 1\n")
    ci = evidence.read_json(repository, "docs/evidence/ci86.json")
    ci["source_sha256"]["app/tests/test_runtime.py"] = binding(repository, "app/tests/test_runtime.py")["sha256"]
    write_json(repository, "docs/evidence/ci86.json", ci)
    rebind(repository)
    result = evidence.audit(repository)
    assert not result["passed"] and any("Python test symbol missing" in error for error in result["errors"])


def test_existing_python_function_requires_an_executed_baseline_entry(repository: Path) -> None:
    baseline = evidence.read_json(repository, "docs/evidence/baseline.json")
    baseline["python_tests"]["items"][0]["symbol"] = "test_unrelated"
    write_json(repository, "docs/evidence/baseline.json", baseline)
    rebind(repository)
    assert any("was not in executed baseline" in error for error in evidence.audit(repository)["errors"])


def fresh_python_run(repository: Path) -> None:
    """A new execution binds changed inputs without modifying old reports."""
    write_text(repository, "app/runtime.py", "value = 2\n")
    report = {
        "format_version": 1, "passed": True, "sources_unchanged": True,
        "real_resource_acceptance": False, "full_acceptance": "pending",
        "python": {"tests": 2, "failures": 0, "errors": 0, "skipped": 0},
        "junit_sha256": "d" * 64, "python_log_sha256": "e" * 64,
        "python_tests": [{"path": "app/tests/test_runtime.py", "symbol": "test_protocol", "passed_cases": 2}],
        "source_sha256": {path: binding(repository, path)["sha256"] for path in ("app/runtime.py", "app/tests/test_runtime.py")},
    }
    write_json(repository, "docs/evidence/python89.json", report)
    baseline = evidence.read_json(repository, "docs/evidence/baseline.json")
    baseline["python_runs"] = ["python89"]
    baseline["reports"]["python89"] = binding(repository, "docs/evidence/python89.json", layer="local_component")
    write_json(repository, "docs/evidence/baseline.json", baseline)
    registry = evidence.read_json(repository, evidence.REGISTRY)
    row = registry["requirements"][0]
    row["implementation"][0]["baseline"] = "python89"
    row["tests"][0]["baseline"] = "python89"
    row["evidence"].append("python89")
    write_json(repository, evidence.REGISTRY, registry)
    rebind(repository, matrix=True)


def fresh_browser_run(repository: Path) -> None:
    write_text(repository, "app/ui.vue", "<template>Updated UI</template>\n")
    write_text(repository, "docs/evidence/view.png", "unit fixture: artifact digest only")
    report = {
        "format_version": 1, "passed": True, "sources_unchanged": True,
        "real_resource_acceptance": False, "full_acceptance": "pending",
        "browser_closed": True, "closed_contexts": 3, "cleanup": {"processes": True, "database": True},
        "browser_process": {"status": "passed", "owned_process_tree_closed": True},
        "checks": ["native_https_layout"],
        "screenshots": [{"path": "view.png", "sha256": binding(repository, "docs/evidence/view.png")["sha256"]}],
        "source_sha256": {"app/ui.vue": binding(repository, "app/ui.vue")["sha256"]},
    }
    write_json(repository, "docs/evidence/browser94.json", report)
    baseline = evidence.read_json(repository, "docs/evidence/baseline.json")
    baseline["browser_runs"] = ["browser94"]
    baseline["reports"]["browser94"] = binding(repository, "docs/evidence/browser94.json", layer="local_browser")
    write_json(repository, "docs/evidence/baseline.json", baseline)
    registry = evidence.read_json(repository, evidence.REGISTRY)
    registry["requirements"][0]["implementation"][1]["baseline"] = "browser94"
    registry["requirements"][0]["evidence"].append("browser94")
    write_json(repository, evidence.REGISTRY, registry)
    rebind(repository, matrix=True)


def test_fresh_browser_binding_keeps_old_sources_and_reports_immutable(repository: Path) -> None:
    old = (repository / "docs/evidence/visual87.json").read_bytes()
    fresh_browser_run(repository)
    result = evidence.audit(repository)
    assert result["passed"], result["errors"]
    assert (repository / "docs/evidence/visual87.json").read_bytes() == old


@pytest.mark.parametrize("mutation", ["open", "cleanup", "changed", "production", "process", "checks", "image", "path", "layer", "source"])
def test_browser_binding_rejects_unverified_or_unclosed_execution(repository: Path, mutation: str) -> None:
    fresh_browser_run(repository)
    report = evidence.read_json(repository, "docs/evidence/browser94.json")
    if mutation == "open":
        report["browser_closed"] = False
    elif mutation == "cleanup":
        report["cleanup"]["processes"] = False
    elif mutation == "changed":
        report["sources_unchanged"] = False
    elif mutation == "production":
        report["real_resource_acceptance"] = True
    elif mutation == "process":
        report["browser_process"]["owned_process_tree_closed"] = False
    elif mutation == "checks":
        report["checks"] = []
    elif mutation == "image":
        report["screenshots"][0]["sha256"] = "0" * 64
    elif mutation == "path":
        report["screenshots"][0]["path"] = "../../app/ui.vue"
    elif mutation == "source":
        report["source_sha256"]["app/ui.vue"] = "0" * 64
    elif mutation == "layer":
        baseline = evidence.read_json(repository, "docs/evidence/baseline.json")
        baseline["reports"]["browser94"]["layer"] = "local_component"
        write_json(repository, "docs/evidence/baseline.json", baseline)
    write_json(repository, "docs/evidence/browser94.json", report)
    rebind(repository)
    assert not evidence.audit(repository)["passed"]


def test_explicit_new_python_run_preserves_historical_reports(repository: Path) -> None:
    old = (repository / "docs/evidence/ci86.json").read_bytes()
    fresh_python_run(repository)
    result = evidence.audit(repository)
    assert result["passed"], result["errors"]
    assert (repository / "docs/evidence/ci86.json").read_bytes() == old


@pytest.mark.parametrize("mutation", [
    "failed", "skipped", "changed", "scope", "full_acceptance", "missing_symbol",
    "wrong_count", "duplicate_case", "bad_digest", "source_drift", "missing_report",
    "unbound_test", "unexecuted_baseline", "duplicate_run", "reserved_run", "wrong_layer",
])
def test_fresh_python_provenance_cannot_hide_missing_execution(repository: Path, mutation: str) -> None:
    fresh_python_run(repository)
    report = evidence.read_json(repository, "docs/evidence/python89.json")
    if mutation == "failed":
        report["python"]["failures"] = 1
    elif mutation == "skipped":
        report["python"]["skipped"] = 1
    elif mutation == "changed":
        report["sources_unchanged"] = False
    elif mutation == "scope":
        report["real_resource_acceptance"] = True
    elif mutation == "full_acceptance":
        report["full_acceptance"] = "passed"
    elif mutation == "missing_symbol":
        report["python_tests"][0]["symbol"] = "test_unrelated"
    elif mutation == "wrong_count":
        report["python_tests"][0]["passed_cases"] = 3
    elif mutation == "duplicate_case":
        report["python_tests"] *= 2
    elif mutation == "bad_digest":
        report["junit_sha256"] = "unknown"
    elif mutation == "source_drift":
        report["source_sha256"]["app/runtime.py"] = "f" * 64
    elif mutation in {"missing_report", "duplicate_run", "reserved_run", "wrong_layer"}:
        baseline = evidence.read_json(repository, "docs/evidence/baseline.json")
        if mutation == "missing_report":
            del baseline["reports"]["python89"]
        elif mutation == "duplicate_run":
            baseline["python_runs"] *= 2
        elif mutation == "reserved_run":
            baseline["python_runs"] = ["ci86"]
        else:
            baseline["reports"]["python89"]["layer"] = "local_browser"
        write_json(repository, "docs/evidence/baseline.json", baseline)
    else:
        registry = evidence.read_json(repository, evidence.REGISTRY)
        if mutation == "unbound_test":
            registry["requirements"][0]["evidence"].remove("python89")
        else:
            registry["requirements"][0]["tests"][0]["baseline"] = "visual87"
        write_json(repository, evidence.REGISTRY, registry)
    write_json(repository, "docs/evidence/python89.json", report)
    rebind(repository, matrix=True)
    assert not evidence.audit(repository)["passed"]


@pytest.mark.parametrize("result", ["fail", "skip", "run", None])
def test_go_reference_requires_actual_pass_even_if_symbol_exists(repository: Path, result: str | None) -> None:
    report = evidence.read_json(repository, "docs/evidence/go80.json")
    report["tests"]["TestProbe"] = {"result": result}
    write_json(repository, "docs/evidence/go80.json", report)
    rebind(repository)
    assert any("Go test not passed" in error for error in evidence.audit(repository)["errors"])


@pytest.mark.parametrize("key,value", [("passed", False), ("passed", "true"), ("sources_unchanged", False), ("real_resource_acceptance", True)])
def test_baseline_failure_and_scope_cannot_be_hidden_by_hash_update(repository: Path, key: str, value: object) -> None:
    report = evidence.read_json(repository, "docs/evidence/ci86.json")
    report[key] = value
    write_json(repository, "docs/evidence/ci86.json", report)
    rebind(repository)
    assert not evidence.audit(repository)["passed"]


@pytest.mark.parametrize("mutation", ["inventory_count", "inventory_duplicate", "negative_cases", "python_log", "failed_gate", "skipped_python", "unknown_layer", "candidate", "browser_leak", "production", "go_runtime"])
def test_invalid_provenance_is_rejected(repository: Path, mutation: str) -> None:
    baseline = evidence.read_json(repository, "docs/evidence/baseline.json")
    if mutation in {"inventory_count", "negative_cases"}:
        baseline["python_tests"]["items"][0]["passed_cases"] = 3 if mutation == "inventory_count" else -2
    elif mutation == "inventory_duplicate":
        baseline["python_tests"]["items"] *= 2
    elif mutation == "python_log":
        baseline["python_tests"]["python_log_sha256"] = "d" * 64
    elif mutation == "unknown_layer":
        baseline["reports"]["ci86"]["layer"] = "real_gpu"
    elif mutation == "candidate":
        baseline["admin_sources"]["archive_sha256"] = "c" * 64
    else:
        name = "ci86" if mutation in {"failed_gate", "skipped_python"} else "installed87" if mutation == "browser_leak" else "visual87" if mutation == "production" else "go80"
        report = evidence.read_json(repository, f"docs/evidence/{name}.json")
        if mutation == "failed_gate":
            report["gates"][0]["status"] = "failed"
        elif mutation == "skipped_python":
            report["python"]["skipped"] = 1
        elif mutation == "browser_leak":
            report["browser_closed"] = False
        elif mutation == "production":
            report["candidate"]["production_accepted"] = True
        else:
            report["runtime_integration_verified"] = True
        write_json(repository, f"docs/evidence/{name}.json", report)
    write_json(repository, "docs/evidence/baseline.json", baseline)
    rebind(repository)
    assert not evidence.audit(repository)["passed"]


@pytest.mark.parametrize("mutation", ["requirement_missing", "requirement_duplicate", "gate_missing", "gate_duplicate", "unknown_requirement", "unknown_backlog", "missing_condition", "full_acceptance", "gate_acceptance", "unknown_report", "local_status", "lost_design_link"])
def test_missing_links_and_false_acceptance_are_rejected(repository: Path, mutation: str) -> None:
    registry = evidence.read_json(repository, evidence.REGISTRY)
    if mutation == "requirement_missing":
        registry["requirements"] = []
    elif mutation == "requirement_duplicate":
        registry["requirements"] *= 2
    elif mutation == "gate_missing":
        registry["gates"].pop()
    elif mutation == "gate_duplicate":
        registry["gates"] *= 2
    elif mutation == "unknown_requirement":
        registry["gates"][0]["requirement_ids"] = ["CAP-99"]
    elif mutation == "unknown_backlog":
        registry["requirements"][0]["remaining_backlog"] = ["99"]
    elif mutation == "missing_condition":
        registry["gates"][0]["remaining_evidence"] = ""
    elif mutation == "full_acceptance":
        registry["full_acceptance"] = "passed"
    elif mutation == "gate_acceptance":
        registry["gates"][0]["status"] = "passed"
    elif mutation == "unknown_report":
        registry["requirements"][0]["evidence"] = ["invented"]
    elif mutation == "local_status":
        registry["requirements"][0]["local_status"] = "complete"
    else:
        registry["gates"][0]["requirement_ids"] = []
    write_json(repository, evidence.REGISTRY, registry)
    rebind(repository, matrix=True)
    assert not evidence.audit(repository)["passed"]


def test_report_hash_and_readable_matrix_drift_are_independent_failures(repository: Path) -> None:
    with (repository / "docs/evidence/ci86.json").open("a", encoding="utf-8") as target:
        target.write("\n")
    assert any("report hash drift" in error for error in evidence.audit(repository)["errors"])
    rebind(repository)
    with (repository / evidence.MATRIX).open("a", encoding="utf-8") as target:
        target.write("\nUnrelated history\n")
    assert evidence.audit(repository)["passed"]
    source = (repository / evidence.MATRIX).read_text(encoding="utf-8").replace("pending", "passed").replace("test_protocol", "test_invented")
    write_text(repository, evidence.MATRIX, source)
    assert any("matrix drift" in error for error in evidence.audit(repository)["errors"])


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "docs\\evidence\\ci.json", "docs/../secret", "docs//file", "./docs/file", "docs/file\x00"])
def test_unsafe_input_paths_are_rejected_before_read(repository: Path, path: str) -> None:
    with pytest.raises(evidence.EvidenceError, match="noncanonical"):
        evidence.read_raw(repository, path)


@pytest.mark.parametrize("part", [".git", ".local", ".venv", "node_modules", "__pycache__", ".env"])
def test_private_inputs_are_rejected_before_open(repository: Path, part: str) -> None:
    relative = "app/" + part if part == ".env" else "app/" + part + "/private.json"
    write_text(repository, relative, '{"credential":"synthetic-private"}')
    with pytest.raises(evidence.EvidenceError, match="private evidence input"):
        evidence.read_raw(repository, relative)


def test_windows_junction_cannot_import_evidence_from_another_directory(repository: Path, tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows directory junction boundary")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "private.json").write_text('{"credential":"synthetic-private"}', encoding="utf-8")
    link = repository / "docs/evidence/linked"
    try:
        cmd = str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe")
        # Both paths belong to this pytest fixture; create only its fixed junction.
        created = subprocess.run([cmd, "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False, timeout=10)  # noqa: S603
        assert created.returncode == 0
        with pytest.raises(evidence.EvidenceError, match="linked evidence input"):
            evidence.read_raw(repository, "docs/evidence/linked/private.json")
    finally:
        if link.is_junction():
            os.rmdir(link)
        (outside / "private.json").unlink(missing_ok=True)
        outside.rmdir()


def test_json_duplicates_and_oversized_files_do_not_pass(repository: Path) -> None:
    write_text(repository, "docs/evidence/invalid.json", '{"passed":false,"passed":true}')
    with pytest.raises(evidence.EvidenceError, match="duplicate"):
        evidence.read_json(repository, "docs/evidence/invalid.json")
    with (repository / "docs/evidence/large.json").open("wb") as target:
        target.truncate(evidence.MAX_FILE_BYTES + 1)
    with pytest.raises(evidence.EvidenceError, match="exceeds budget"):
        evidence.read_raw(repository, "docs/evidence/large.json")
