"""Offline checks for the development documentation; never starts runtime services."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


DOCS = Path(__file__).resolve().parents[1]
PROJECT = DOCS.parent
CONTRACTS = DOCS / "contracts"
BASELINE = "v0.4"


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> object:
    return json.loads(
        path.read_text(encoding="utf-8-sig"),
        object_pairs_hook=no_duplicate_keys,
        parse_float=Decimal,
    )


def local_refs(value: object, schema: dict, location: str = "$") -> list[str]:
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref":
                if not isinstance(item, str) or not item.startswith("#/"):
                    errors.append(f"{location}: only local JSON Schema refs are allowed")
                    continue
                target = schema
                try:
                    for segment in item[2:].split("/"):
                        target = target[segment.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    errors.append(f"{location}: unresolved ref {item}")
            errors.extend(local_refs(item, schema, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(local_refs(item, schema, f"{location}[{index}]"))
    return errors


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        import yaml
    except ImportError as exc:
        print(f"Missing native Python documentation dependency: {exc.name}; no installation attempted.", file=sys.stderr)
        return 2

    errors: list[str] = []
    checks: dict[str, object] = {}

    def require(condition: object, message: str) -> None:
        if not condition:
            errors.append(message)

    def unique(items: list, field: str, label: str) -> set:
        values = [item[field] for item in items]
        require(len(values) == len(set(values)), f"{label}: duplicate {field}")
        return set(values)

    markdown_paths = sorted(DOCS.rglob("*.md"))
    text_by_name = {p.relative_to(DOCS).as_posix(): p.read_text(encoding="utf-8-sig") for p in markdown_paths}
    instruction = PROJECT / "AGENTS.md"
    require(instruction.exists(), "Missing project AGENTS.md")
    if instruction.exists():
        text_by_name["../AGENTS.md"] = instruction.read_text(encoding="utf-8-sig")
    examples = Counter()
    links_checked = 0
    fenced_blocks = 0
    link_pattern = re.compile(r"(?<!!)\[[^\]\n]+\]\((<[^>]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)")
    for name, source in text_by_name.items():
        fence = None
        body: list[str] = []
        prose: list[str] = []
        opened_at = 0
        for line_no, line in enumerate(source.splitlines(), 1):
            marker = re.match(r"^\s*(`{3,}|~{3,})([^\s]*)\s*$", line)
            if marker and fence is None:
                fence = (marker[1][0], len(marker[1]), marker[2].lower())
                opened_at = line_no
                body = []
                fenced_blocks += 1
            elif fence is not None and re.match(r"^\s*" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*$", line):
                block = "\n".join(body)
                try:
                    if fence[2] == "json":
                        json.loads(block, object_pairs_hook=no_duplicate_keys, parse_float=Decimal)
                        examples["json"] += 1
                    elif fence[2] in {"yaml", "yml"}:
                        yaml.safe_load(block)
                        examples["yaml"] += 1
                except (ValueError, yaml.YAMLError) as exc:
                    errors.append(f"{name}:{opened_at}: invalid fenced {fence[2]}: {exc}")
                fence = None
            elif fence is not None:
                body.append(line)
            else:
                prose.append(line)
        require(fence is None, f"{name}:{opened_at}: unclosed code fence")
        for match in link_pattern.finditer("\n".join(prose)):
            target = match[1].strip("<>")
            if target.startswith("#"):
                continue
            parsed = urlsplit(target)
            if parsed.scheme in {"http", "https", "mailto", "app", "codex"}:
                continue
            if parsed.scheme and not re.match(r"^[A-Za-z]:[/\\]", target):
                errors.append(f"{name}: unsupported link scheme {target}")
                continue
            file_part = unquote(target.split("#", 1)[0])
            resolved = ((DOCS / name).parent / file_part).resolve()
            require(resolved.is_relative_to(PROJECT), f"{name}: local link leaves project: {target}")
            require(resolved.exists(), f"{name}: broken local link: {target}")
            links_checked += 1
    checks["markdown"] = {"documents": len(markdown_paths), "instruction_files": int(instruction.exists()), "fenced_blocks": fenced_blocks, "json_examples": examples["json"], "yaml_examples": examples["yaml"], "local_links_checked": links_checked}

    data = {}
    json_paths = sorted(CONTRACTS.glob("*.json"))
    for path in json_paths:
        try:
            data[path.name] = read_json(path)
        except (ValueError, OSError) as exc:
            errors.append(f"{path.name}: {exc}")
    schemas = {}
    for name, value in data.items():
        if not name.endswith(".schema.json"):
            expected_version = "0.5.0" if name in {"fixtures.json", "monitoring-metrics.json"} else "0.4.0"
            require(value.get("contract_version") == expected_version, f"{name}: inconsistent contract version")
            continue
        try:
            errors.extend(f"{name}: {issue}" for issue in local_refs(value, value))
            Draft202012Validator.check_schema(value)
            schemas[name] = Draft202012Validator(value, format_checker=FormatChecker())
        except Exception as exc:
            errors.append(f"{name}: schema invalid: {exc}")

    fixtures = data.get("fixtures.json", {}).get("cases", [])
    unique(fixtures, "id", "fixtures")
    fixture_counts = Counter()
    positive_types = set()
    for item in fixtures:
        name = item["schema"]
        if name not in schemas:
            errors.append(f"fixture {item['id']}: missing schema {name}")
            continue
        actual = not list(schemas[name].iter_errors(item["data"]))
        require(actual == item["valid"], f"fixture {item['id']}: expected valid={item['valid']}, got {actual}")
        fixture_counts["positive" if item["valid"] else "negative"] += 1
        if name == "job-create.schema.json" and item["valid"]:
            positive_types.add(item["data"]["task_type"])
    expected_types = set(data.get("job-create.schema.json", {}).get("properties", {}).get("task_type", {}).get("enum", []))
    require(expected_types == {"text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"} and positive_types == expected_types, "Native image, video and audio capability fixtures are not covered")
    worker_positive_types = {item["data"]["type"] for item in fixtures if item["valid"] and item["schema"] == "worker-event.schema.json"}
    declared_worker_types = set(data.get("worker-event.schema.json", {}).get("properties", {}).get("type", {}).get("enum", []))
    require(worker_positive_types == declared_worker_types, "Worker event type fixtures incomplete")
    job_shape = data.get("job-create.schema.json", {}).get("properties", {})
    require(job_shape.get("input", {}).get("properties", {}).get("prompt", {}).get("maxLength") == 8000, "Prompt ceiling drifted from requirement baseline")
    require(job_shape.get("input", {}).get("properties", {}).get("references", {}).get("maxItems") == 4, "Reference ceiling drifted from first-release baseline")
    require(job_shape.get("parameters", {}).get("properties", {}).get("n", {}).get("maximum") == 1, "First-release output count drifted")
    checks["contracts"] = {"json_files": len(json_paths), "schemas": len(schemas), "fixtures": len(fixtures), "positive_fixtures": fixture_counts["positive"], "negative_fixtures": fixture_counts["negative"], "capabilities_covered": sorted(positive_types)}

    machine = data.get("job-state-machine.json", {})
    states = set(machine.get("states", []))
    terminals = set(machine.get("terminal_states", []))
    transitions = machine.get("transitions", [])
    guards = set(machine.get("guards", {}))
    require(len(states) == len(machine.get("states", [])) == 16, "State registry must contain the 16 unique baseline states")
    require(terminals == {"succeeded", "failed", "cancelled", "expired"}, "Terminal state set drifted")
    edges = set()
    adjacency = {state: set() for state in states}
    for transition in transitions:
        src, dst = transition["from"], transition["to"]
        require(src in states and dst in states, f"Unknown transition {src}->{dst}")
        require(src not in terminals, f"Terminal state can leave: {src}->{dst}")
        require(src != dst, f"Progress events must not be state self-loops: {src}")
        pair = (src, dst, transition["trigger"])
        require(pair not in edges, f"Duplicate transition {pair}")
        edges.add(pair)
        required = set(transition["guards"])
        require(required and required <= guards, f"Missing/unknown guards for {src}->{dst}")
        if dst in terminals:
            require("terminal_commit_once" in required, f"Terminal commit guard missing: {src}->{dst}")
            require("resources_accounted" in required, f"Terminal resource accounting missing: {src}->{dst}")
        if dst == "succeeded":
            require({"current_lease", "cloud_manifest_verified"} <= required, "Success lacks cloud/lease guards")
        if dst == "retry_wait":
            require({"execution_stopped", "old_attempt_closed", "retry_budget_available"} <= required, f"Unsafe retry: {src}->{dst}")
        if src in adjacency:
            adjacency[src].add(dst)
        require(f"`{src}`" in text_by_name.get("02-系统架构与任务机制.md", ""), f"State missing from architecture: {src}")

    def walk(start: set, graph: dict) -> set:
        seen = set(start)
        pending = list(start)
        while pending:
            for child in graph.get(pending.pop(), set()):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
        return seen

    require(walk(set(machine.get("initial_states", [])), adjacency) == states, "Some states are not reachable")
    reverse = {state: set() for state in states}
    for src, children in adjacency.items():
        for child in children:
            reverse.setdefault(child, set()).add(src)
    require(walk(terminals, reverse) == states, "Some states have no guarded terminal path")
    checks["state_machine"] = {"states": len(states), "terminal_states": len(terminals), "transitions": len(transitions), "guards": len(guards)}

    catalog = data.get("endpoint-catalog.json", {})
    endpoints = catalog.get("endpoints", [])
    unique(endpoints, "operation_id", "endpoints")
    route_keys = [(item["method"], item["path"]) for item in endpoints]
    require(len(route_keys) == len(set(route_keys)), "Duplicate method/path in endpoint catalog")
    identity_prefix = {"service": "/v1/", "admin": "/admin/api/v1/", "admin_login": "/admin/api/v1/session", "worker": "/internal/v1/", "gateway": "/gateway/v1/", "monitoring": "/internal/monitoring/v1/"}
    allowed_scopes = set(data.get("key-policy.schema.json", {}).get("properties", {}).get("scopes", {}).get("items", {}).get("enum", []))
    permissions = set(catalog.get("permissions", []))
    require(permissions, "Missing admin permissions")
    for role, role_permissions in catalog.get("role_presets", {}).items():
        require(set(role_permissions) <= permissions, f"Unknown permissions in role {role}")
    for item in endpoints:
        label = f"{item['method']} {item['path']}"
        identity = item["identity"]
        require(identity in identity_prefix and item["path"].startswith(identity_prefix.get(identity, "!")), f"Identity namespace mismatch: {label}")
        require(bool(item.get("resource_guard")), f"Resource guard missing: {label}")
        require(set(item["scopes_all"]) <= allowed_scopes, f"Unknown business scope: {label}")
        require(set(item["permissions_all"]) <= permissions, f"Unknown admin permission: {label}")
        if identity == "service":
            require(item["scopes_all"] or item["path"] == "/v1/access-policy", f"Business endpoint lacks scope: {label}")
        if identity == "admin":
            require(not item["scopes_all"], f"Admin endpoint accepts business scope: {label}")
            own_session_activity = (item["method"], item["path"]) == ("POST", "/admin/api/v1/session/activity")
            require(item["permissions_all"] or item["path"] in {"/admin/api/v1/session", "/admin/api/v1/events"} or own_session_activity, f"Admin permission missing: {label}")
            if item["method"] not in {"GET", "HEAD"}:
                require(item["csrf_required"], f"Admin non-GET lacks CSRF: {label}")
                readonly_directory_query = (item["method"], item["path"]) in {
                    ("POST", "/admin/api/v1/key-policy/options/query"),
                    ("POST", "/admin/api/v1/key-generation-options/query"),
                }
                if readonly_directory_query:
                    require(item["permissions_all"] == ["keys:manage"] and item["resource_guard"] == "admin_visibility", f"Unbounded Admin directory query: {label}")
                    require(item["idempotency"] == "none" and not item["if_match_required"], f"Read-only directory query must not declare mutation controls: {label}")
                elif own_session_activity:
                    require(not item["permissions_all"] and item["resource_guard"] == "current_session_only", f"Session activity may only touch the authenticated session: {label}")
                    require(item["idempotency"] == "none" and not item["if_match_required"], f"Session activity must recheck current authentication: {label}")
                elif item["path"] != "/admin/api/v1/session":
                    require(item["idempotency"] == "required", f"Admin write lacks idempotency: {label}")
        if identity == "admin_login":
            require(item["path"] == "/admin/api/v1/session" and item["method"] == "POST", "Anonymous realm allowed beyond login")
        if identity == "monitoring":
            require(item["method"] == "GET" and item["path"] in {"/internal/monitoring/v1/metrics", "/internal/monitoring/v1/configuration"}, f"Monitoring must remain read-only: {label}")
            require(item["resource_guard"] == "monitoring_token_and_source_network" and not item["scopes_all"] and not item["permissions_all"], f"Monitoring identity isolation missing: {label}")
        if identity == "gateway":
            if item.get("configuration_service_only"):
                configuration_endpoints = {("GET", "/gateway/v1/catalog"), ("POST", "/gateway/v1/catalog/receipts")}
                require((item["method"], item["path"]) in configuration_endpoints, f"Configuration exception beyond fixed catalog endpoints: {label}")
                require(item.get("resource_guard") == "gateway_catalog_binding" and item.get("policy_evaluation") == "service_configuration" and not item.get("delegated_business_policy_required") and not item.get("delegated_scopes_all") and not item.get("reconciliation_service_only"), f"Unbounded configuration exception: {label}")
            elif item.get("reconciliation_service_only"):
                reconciliation_guards = {("GET", "/gateway/v1/settlements"): "gateway_settlement_binding", ("POST", "/gateway/v1/settlements/{id}/ack"): "gateway_settlement_binding", ("POST", "/gateway/v1/admission-requests/lookup"): "gateway_request_binding", ("POST", "/gateway/v1/admission-requests/close"): "gateway_request_binding"}
                expected_guard = reconciliation_guards.get((item["method"], item["path"]))
                require(expected_guard is not None, f"Reconciliation exception beyond fixed recovery endpoints: {label}")
                require(item.get("resource_guard") == expected_guard and item.get("policy_evaluation") == "service_reconciliation" and not item.get("delegated_business_policy_required") and not item.get("delegated_scopes_all"), f"Unbounded reconciliation exception: {label}")
            else:
                require(item.get("delegated_business_policy_required"), f"Gateway bypasses business policy: {label}")
                require(item.get("policy_evaluation") in {"admission_snapshot", "current_business_policy"}, f"Gateway policy timing missing: {label}")
            require(set(item.get("delegated_scopes_all", [])) <= allowed_scopes, f"Unknown delegated scope: {label}")
        for schema in item.get("request_schemas", []):
            require(schema in schemas, f"Unknown request schema {schema}: {label}")
    required_routes = {("POST", "/v1/jobs"), ("POST", "/v1/images/generations"), ("POST", "/v1/videos"), ("POST", "/admin/api/v1/api-keys"), ("POST", "/admin/api/v1/workflow-syncs"), ("GET", "/admin/api/v1/queues/snapshot"), ("POST", "/internal/v1/reconciliations")}
    require(required_routes <= set(route_keys), "Core first-release endpoint missing")
    native_create = next((item for item in endpoints if item["method"] == "POST" and item["path"] == "/v1/jobs"), {})
    require("jobs:create" in native_create.get("scopes_all", []), "Native job bypasses jobs:create")
    require(native_create.get("capability_scopes") == {"image": ["images:create"], "video": ["videos:create"]}, "Native job bypasses capability scopes")
    checks["endpoints"] = {"total": len(endpoints), "by_identity": dict(Counter(item["identity"] for item in endpoints)), "admin_permissions": len(permissions)}

    trace = data.get("development-traceability.json", {})
    rules = trace.get("rules", [])
    scenarios = trace.get("quality_scenarios", [])
    packages = trace.get("work_packages", [])
    requirements = trace.get("requirements", [])
    rule_ids = unique(rules, "id", "rules")
    scenario_ids = unique(scenarios, "id", "quality scenarios")
    package_ids = unique(packages, "id", "work packages")
    requirement_ids = unique(requirements, "id", "requirements")
    require(rule_ids == {f"DEV-{n:02}" for n in range(1, 33)}, "DEV rule registry incomplete")
    require(scenario_ids == {f"QC-{n:02}" for n in range(1, 19)}, "QC scenario registry incomplete")
    require(package_ids == {f"WP-{n:02}" for n in range(15)}, "Work package registry incomplete")
    spec = text_by_name.get("01-需求规格与约束.md", "")
    expected_requirements = set(re.findall(r"^\| ((?:CAP|API|JOB|NODE|DATA|BILL|ADMIN|KEY)-\d{2})\b", spec, re.MULTILINE))
    require(requirement_ids == expected_requirements and len(expected_requirements) >= 72, "Full specification requirement registry incomplete")
    acceptance = text_by_name.get("05-实施路线与验收.md", "")
    declared_acceptance = set(re.findall(r"^\| ([A-Z]+(?:-TEST)?-\d{2})\b", acceptance, re.MULTILINE))
    for item in rules + scenarios + packages:
        doc = item["document"]
        require(doc in text_by_name and item["id"] in text_by_name.get(doc, ""), f"Trace target missing: {item['id']}")
    for item in rules:
        require(item["evidence_refs"] and set(item["evidence_refs"]) <= scenario_ids | package_ids, f"Rule evidence unresolved: {item['id']}")
    for item in scenarios:
        require(set(item["rules"]) <= rule_ids, f"Unknown rule in {item['id']}")
        require(item["status"] in {"not_run", "passed", "failed", "skipped"}, f"Unknown test status: {item['id']}")
        if item["status"] == "passed":
            require(item.get("evidence"), f"Passed test without evidence: {item['id']}")
    graph = {item["id"]: set(item["depends_on"]) for item in packages}
    for item in packages:
        require(set(item["depends_on"]) <= package_ids, f"Unknown dependency in {item['id']}")
        require(item["id"] not in walk(set(item["depends_on"]), graph), f"Work package dependency cycle: {item['id']}")
        require(item["status"] in {"not_started", "in_progress", "blocked", "complete"}, f"Unknown work status: {item['id']}")
        if item["status"] == "complete":
            require(item.get("evidence"), f"Complete work package without evidence: {item['id']}")
    acceptance_refs = set()
    for item in requirements:
        require(item["id"] in spec, f"Requirement not in specification: {item['id']}")
        require(item["work_packages"] and set(item["work_packages"]) <= package_ids, f"Requirement work mapping invalid: {item['id']}")
        for doc in item["design_docs"]:
            require(doc in text_by_name, f"Missing design document {doc}")
        for ref in item["acceptance"]:
            require(ref in scenario_ids or ref in declared_acceptance, f"Acceptance not found: {item['id']} -> {ref}")
            acceptance_refs.add(ref)
    require({f"ADMIN-TEST-{n:02}" for n in range(1, 17)} <= acceptance_refs, "Admin acceptance trace incomplete")
    require({f"KEY-TEST-{n:02}" for n in range(1, 13)} <= acceptance_refs, "Key acceptance trace incomplete")
    for item in endpoints:
        require(item["work_package"] in package_ids, f"Endpoint has unknown work package: {item['operation_id']}")
    checks["traceability"] = {"rules": len(rules), "quality_scenarios": len(scenarios), "work_packages": len(packages), "requirements": len(requirements), "work_package_statuses": dict(Counter(item["status"] for item in packages)), "admin_acceptance_mapped": 16, "key_acceptance_mapped": 12}

    topics = {
        "native_only": ("09-开发总纲与强制约束.md", ["DEV-02", "Docker", "WSL"]),
        "browser_cleanup": ("13-Admin前端开发规范.md", ["finally", "context", "隐藏页"]),
        "separate_gpu_result_ownership": ("11-数据字典与数据库事务.md", ["gpu_occupancies", "结果租约", "不能建立"]),
        "unknown_submit_reconciliation": ("12-接口契约与Worker协议.md", ["journal", "submit_intent", "响应丢失不重发"]),
        "atomic_quota": ("11-数据字典与数据库事务.md", ["锁顺序", "原子", "Asia/Taipei"]),
        "dual_fingerprints": ("11-数据字典与数据库事务.md", ["request_fingerprint", "execution_fingerprint"]),
        "business_key_admin_isolation": ("08-APIKey鉴权与访问控制.md", ["业务访问 Key", "管理会话", "独立 Key"]),
        "private_delivery": ("15-原生部署配置与运维.md", ["桶私有", "authenticated_proxy", "Range/HEAD"]),
        "outbox_and_snapshot": ("11-数据字典与数据库事务.md", ["Outbox", "提交顺序", "失效通知后重取一致快照"]),
        "native_deployment_and_recovery": ("15-原生部署配置与运维.md", ["systemd", "reconciling", "RPO", "回退"]),
        "five_capabilities": ("04-工作流产品化规范.md", ["文生图", "图生图", "多参考文图生图", "文生视频", "多参考文图生视频"]),
        "real_scale_evidence": ("16-开发任务分解与交付清单.md", ["20协议", "10/20真实", "未开始"]),
        "single_customer_ledger": ("09-开发总纲与强制约束.md", ["DEV-24", "唯一客户货币账本"]),
        "sub2api_first": ("README.md", ["Sub2API", "优先", "异步"]),
    }
    for topic, (name, words) in topics.items():
        missing = [word for word in words if word not in text_by_name.get(name, "")]
        require(not missing, f"Topic {topic}: missing {missing} in {name}")
    checks["topic_presence"] = {"checks": len(topics), "scope": "Text presence only, not proof of correctness"}

    source_files = markdown_paths + ([instruction] if instruction.exists() else []) + json_paths + [Path(__file__).resolve()]
    fingerprints = []
    for path in sorted(source_files):
        raw = path.read_bytes()
        fingerprints.append({"file": path.relative_to(PROJECT).as_posix(), "bytes": len(raw), "lines": len(raw.splitlines()), "sha256": hashlib.sha256(raw).hexdigest()})
        if path.suffix == ".py":
            try:
                ast.parse(raw.decode("utf-8-sig"))
            except SyntaxError as exc:
                errors.append(f"Invalid Python tool syntax: {exc}")

    report = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "baseline": BASELINE,
        "scope": "Offline documentation syntax, links, schemas/fixtures, state graph, endpoint metadata and traceability only; no service, auth, database, browser, GPU, gateway or real node runtime tested.",
        "project": str(PROJECT),
        "tool": {"python": sys.version.split()[0], "jsonschema": version("jsonschema"), "PyYAML": version("PyYAML")},
        "checks": checks,
        "files": fingerprints,
        "errors": errors,
        "passed": not errors,
    }
    if args.write_report:
        (DOCS / "文档校验记录.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": BASELINE, "passed": not errors, "checks": checks, "errors": errors, "report_written": args.write_report}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(run())
