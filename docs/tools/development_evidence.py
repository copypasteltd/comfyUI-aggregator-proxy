"""Audit requirement evidence offline; this does not execute or accept runtime gates."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

REGISTRY = "docs/contracts/development-evidence.json"
SPEC = "docs/01-需求规格与约束.md"
ACCEPTANCE = "docs/05-实施路线与验收.md"
QUALITY = "docs/14-测试策略与质量门禁.md"
BACKLOG = "docs/78-完整待办与首版收口清单.md"
TRACE = "docs/contracts/development-traceability.json"
MATRIX = "docs/88-需求实现与分层证据追踪.md"
BEGIN = "<!-- development-evidence:begin -->"
END = "<!-- development-evidence:end -->"
MAX_FILE_BYTES = 5 * 1024 * 1024
PRIVATE_PARTS = {".git", ".local", ".venv", "node_modules", "__pycache__"}
REQ_PATTERN = r"^\| ((?:CAP|API|JOB|NODE|DATA|BILL|ADMIN|KEY)-\d{2})\b"
GATE_PATTERN = r"^\| ((?:FUNC|COMPAT|SCALE|FAIL|DATA-TEST|ADMIN-TEST|KEY-TEST|AUDIO|QC)-\d{2})\b"


class EvidenceError(ValueError):
    """A bounded, repository-local evidence input is invalid."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str) or len(relative) > 500
        or re.search(r"[\\:\x00-\x1f]", relative)
        or any(part in {"", ".", ".."} for part in relative.split("/"))
        or PurePosixPath(relative).is_absolute()
    ):
        raise EvidenceError("noncanonical repository path")
    parts = PurePosixPath(relative).parts
    if PRIVATE_PARTS.intersection(parts) or parts[-1] == ".env":
        raise EvidenceError("private evidence input")
    root = root.resolve()
    path = root
    for part in relative.split("/"):
        path /= part
        if path.is_symlink() or path.is_junction():
            raise EvidenceError(f"linked evidence input: {relative}")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise EvidenceError(f"missing repository file: {relative}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise EvidenceError(f"evidence file exceeds budget: {relative}")
    return path


def read_raw(root: Path, relative: str) -> bytes:
    with safe_file(root, relative).open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise EvidenceError(f"evidence file exceeds budget: {relative}")
    return raw


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate JSON key")
        result[key] = value
    return result


def parse_json(raw: bytes) -> dict:
    def reject_constant(_value: str) -> None:
        raise EvidenceError("nonfinite JSON constant")

    value = json.loads(
        raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise EvidenceError("evidence document must be an object")
    return value


def read_json(root: Path, relative: str) -> dict:
    return parse_json(read_raw(root, relative))


def _unique(rows: list, label: str) -> dict:
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise EvidenceError(f"invalid {label} collection")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise EvidenceError(f"invalid {label} row")
        if row["id"] in result:
            raise EvidenceError(f"duplicate {label} ID: {row['id']}")
        result[row["id"]] = row
    return result


def matrix(registry: dict) -> str:
    lines = [
        BEGIN, "", "## 需求逐项关联", "",
        "下表的本地状态只描述实现与测试覆盖。每条需求的完整出口仍需右列待办及下表验收项；文件名链接指向实现，测试名称与各次实测报告在机器清单中完整保存。", "",
        "| 需求 | 本地状态 | 实现入口 | 已执行测试引用 | 本地报告 | 剩余待办 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in registry["requirements"]:
        implementations = "<br>".join(
            f"[{PurePosixPath(ref['path']).name}](../{ref['path']})"
            for ref in row["implementation"]
        )
        tests = "<br>".join(f"`{ref['symbol']}`" for ref in row["tests"])
        status = "框架；真实产物待定" if row["local_status"] == "framework" else "已实现；完整出口待验收"
        lines.append(
            f"| {row['id']} | {status} | {implementations} | {tests} | "
            f"{', '.join(row['evidence'])} | {', '.join(row['remaining_backlog'])} |"
        )
    lines += [
        "", "## 验收场景逐项关联", "",
        "98 项均保留完整验收出口。局部自动测试或历史协议负载通过，并不表示该场景的真实环境、全部断言和版本组合已签署；这里的“待完整验收”也不表示关联模块从未测试。", "",
        "| 场景 | 需求关联 | 局部证据 | 剩余待办 | 完整验收还需 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in registry["gates"]:
        lines.append(
            f"| {row['id']} | {', '.join(row['requirement_ids'])} | "
            f"{', '.join(row['local_evidence'])} | {', '.join(row['remaining_backlog'])} | "
            f"{row['remaining_evidence']} |"
        )
    return "\n".join([*lines, "", END])


def audit(root: Path, *, check_matrix: bool = True) -> dict:
    """Validate published provenance and selected current inputs, without private files."""
    issues: list[str] = []
    counts: dict[str, object] = {}
    checked_files: set[str] = set()

    def require(condition: object, message: str) -> None:
        if not condition:
            issues.append(message)

    def bound_json(ref: dict) -> dict:
        relative = ref["path"]
        if not relative.startswith("docs/evidence/"):
            raise EvidenceError("report must be under docs/evidence")
        raw = read_raw(root, relative)
        require(sha256(raw) == ref["sha256"], f"report hash drift: {relative}")
        checked_files.add(relative)
        return parse_json(raw)

    try:
        registry = read_json(root, REGISTRY)
        require(registry.get("format_version") == 1, "unknown evidence registry format")
        require(registry.get("full_acceptance") == "pending", "offline map cannot promote full acceptance")
        requirements = _unique(registry["requirements"], "requirement")
        gates = _unique(registry["gates"], "gate")
        spec_ids = set(re.findall(REQ_PATTERN, read_raw(root, SPEC).decode("utf-8-sig"), re.MULTILINE))
        gate_ids = set(re.findall(GATE_PATTERN, read_raw(root, ACCEPTANCE).decode("utf-8-sig"), re.MULTILINE))
        gate_ids.update(re.findall(GATE_PATTERN, read_raw(root, QUALITY).decode("utf-8-sig"), re.MULTILINE))
        require(set(requirements) == spec_ids, "requirement registry differs from specification")
        require(set(gates) == gate_ids, "acceptance registry differs from design gates")
        backlog_ids = set(re.findall(r"^\| (\d{2}) \|", read_raw(root, BACKLOG).decode("utf-8-sig"), re.MULTILINE))
        trace = read_json(root, TRACE)
        trace_requirements = _unique(trace["requirements"], "design requirement")
        require(set(trace_requirements) == spec_ids, "design trace requirement drift")

        baseline = bound_json(registry["baseline_index"])
        reports = {}
        for key, ref in baseline["reports"].items():
            report = bound_json(ref)
            require(report.get("passed") is True, f"report is not passed: {key}")
            require(ref.get("layer") in {"local_ci", "local_protocol", "local_browser", "local_component", "historical_protocol_load"}, f"unreviewed evidence layer: {key}")
            reports[key] = report
        ci = reports["ci86"]
        python = ci["python"]
        require(ci.get("real_resource_acceptance") is False, "CI scope must remain local")
        require(python["errors"] == python["failures"] == python["skipped"] == 0, "Python baseline has failed or skipped cases")
        require(ci.get("sources_unchanged") is True, "CI inputs changed during historical run")
        require(all(g["status"] == "passed" for g in ci["gates"]), "historical CI contains failed gates")
        inventory = baseline["python_tests"]
        require(inventory["source_report"] == "ci86", "test inventory has unrelated provenance")
        python_gates = [gate for gate in ci["gates"] if gate["name"] == "python"]
        require(len(python_gates) == 1 and inventory["python_log_sha256"] == python_gates[0]["log_sha256"], "test inventory does not match historical Python log")
        require(re.fullmatch(r"[0-9a-f]{64}", inventory["junit_sha256"]) is not None, "missing private JUnit provenance digest")
        test_index = {}
        for case in inventory["items"]:
            key = (case["path"], case["symbol"])
            require(key not in test_index, "duplicate Python inventory symbol")
            require(type(case["passed_cases"]) is int and case["passed_cases"] > 0, "test inventory contains non-passing cases")
            test_index[key] = case
        require(sum(case["passed_cases"] for case in test_index.values()) == python["tests"] == inventory["case_count"], "Python inventory count differs from executed baseline")

        source_indexes = {"ci86": ci["source_sha256"]}
        admin = baseline["admin_sources"]
        require(admin["archive_sha256"] == reports["visual87"]["candidate"]["archive_sha256"], "Admin source snapshot has unrelated candidate")
        require(reports["installed87"]["browser_closed"] is True, "installed browser evidence left resources open")
        require(reports["visual87"]["candidate"]["production_accepted"] is False, "visual evidence cannot accept production")
        source_indexes["admin87"] = admin["source_sha256"]
        source_indexes["go80"] = {
            "app/integrations/sub2api/overlay/backend/" + key: value
            for key, value in reports["go80"]["overlay_files_sha256"].items()
        }
        require(reports["go80"]["runtime_integration_verified"] is False, "Go component evidence cannot accept full runtime")

        def verify_source(relative: str, provenance: str) -> bytes:
            if not relative.startswith("app/") or "/.local/" in relative:
                raise EvidenceError("implementation/test must be a maintained app input")
            raw = read_raw(root, relative)
            require(sha256(raw) == source_indexes.get(provenance, {}).get(relative), f"source evidence drift: {relative} ({provenance})")
            checked_files.add(relative)
            return raw

        def check_refs(values: list, allowed: set, label: str) -> None:
            require(isinstance(values, list) and bool(values) and len(values) == len(set(values)) and set(values) <= allowed, f"missing/unknown/duplicate {label}")

        referenced_tests = set()
        for key, row in requirements.items():
            require(row.get("local_status") in {"framework", "implemented"}, f"unknown local status: {key}")
            require(bool(row.get("limitation")), f"missing acceptance limitation: {key}")
            check_refs(row["remaining_backlog"], backlog_ids, f"backlog references: {key}")
            check_refs(row["evidence"], set(reports), f"report references: {key}")
            require(bool(row["implementation"]), f"missing implementation: {key}")
            for ref in row["implementation"]:
                verify_source(ref["path"], ref["baseline"])
            require(bool(row["tests"]), f"missing named tests: {key}")
            for ref in row["tests"]:
                relative, symbol = ref["path"], ref["symbol"]
                language = ref["language"]
                raw = verify_source(relative, "ci86" if language == "python" else "go80")
                require(language in {"python", "go"}, f"unknown test language: {key}")
                if language == "python":
                    names = {node.name for node in ast.walk(ast.parse(raw.decode("utf-8-sig"))) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                    require(symbol in names, f"Python test symbol missing: {relative}::{symbol}")
                    require((relative, symbol) in test_index, f"Python test was not in executed baseline: {relative}::{symbol}")
                    require("ci86" in row["evidence"], f"Python test missing CI evidence: {key}")
                else:
                    require(re.fullmatch(r"Test[A-Za-z0-9_]+", symbol) is not None and re.search(r"^func " + re.escape(symbol) + r"\(t \*testing\.T\)", raw.decode("utf-8"), re.MULTILINE), f"Go test symbol missing: {relative}::{symbol}")
                    require(reports["go80"]["tests"].get(symbol, {}).get("result") == "pass", f"Go test not passed in baseline: {symbol}")
                    require("go80" in row["evidence"], f"Go test missing component evidence: {key}")
                referenced_tests.add((relative, symbol))
            expected_gates = set(trace_requirements.get(key, {}).get("acceptance", []))
            actual_gates = {gate for gate, item in gates.items() if key in item["requirement_ids"]}
            require(expected_gates <= actual_gates, f"design acceptance link lost: {key}")
        for key, row in gates.items():
            require(row.get("status") == "pending_full_acceptance", f"offline gate cannot promote acceptance: {key}")
            check_refs(row["requirement_ids"], set(requirements), f"gate requirement references: {key}")
            check_refs(row["remaining_backlog"], backlog_ids, f"gate backlog references: {key}")
            check_refs(row["local_evidence"], set(reports), f"gate report references: {key}")
            require(bool(row.get("remaining_evidence")), f"gate has no remaining acceptance condition: {key}")
        if check_matrix:
            source = read_raw(root, MATRIX).decode("utf-8-sig").replace("\r\n", "\n")
            require(source.count(BEGIN) == source.count(END) == 1, "matrix markers missing/duplicated")
            if BEGIN in source and END in source:
                require(source[source.index(BEGIN):source.index(END) + len(END)] == matrix(registry), "readable matrix drift; review registry then --refresh-matrix")
        counts = {
            "requirements": len(requirements), "acceptance_gates": len(gates),
            "local_statuses": dict(Counter(row["local_status"] for row in requirements.values())),
            "named_test_references": len(referenced_tests), "baseline_python_cases": python["tests"],
            "reports": len(reports), "current_source_files": len([p for p in checked_files if p.startswith("app/")]),
        }
    except (EvidenceError, KeyError, TypeError, ValueError, OSError, SyntaxError, AttributeError) as exc:
        issues.append(f"invalid evidence input ({type(exc).__name__}): {exc}")
    return {
        "format_version": 1, "passed": not issues, "scope": "Offline traceability and selected source provenance only; historical tests are not rerun; complete acceptance remains pending.",
        "real_resource_acceptance": False, "full_acceptance": "pending", "virtualization_used": False,
        "counts": counts, "errors": issues, "checked_files": sorted(checked_files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--refresh-matrix", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.refresh_matrix:
        result = audit(root, check_matrix=False)
        if not result["passed"]:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        path = safe_file(root, MATRIX)
        source = path.read_text(encoding="utf-8")
        if source.count(BEGIN) != 1 or source.count(END) != 1 or source.index(END) < source.index(BEGIN):
            parser.error("matrix markers must exist exactly once and in order")
        source = source[:source.index(BEGIN)] + matrix(read_json(root, REGISTRY)) + source[source.index(END) + len(END):]
        path.write_text(source, encoding="utf-8", newline="\n")
    result = audit(root)
    if args.write_report:
        result["checked_at"] = datetime.now(UTC).isoformat()
        (root / "docs/需求证据校验记录.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
