"""Check generated rules with a native promtool; start no monitoring services."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from control_api.monitoring_cli import alert_rules
from generation_contracts.monitoring import MonitoringThresholds

APP = Path(__file__).resolve().parents[1]


def series(metric: str, values: str, *, instance: str = "native-a") -> dict[str, str]:
    labels = f'job="render-control",instance="{instance}"'
    if "{" in metric:
        metric = metric[:-1] + "," + labels + "}"
    else:
        metric += "{" + labels + "}"
    return {"series": metric, "values": values}


def scenarios(rules: list[dict[str, Any]]) -> dict[str, Any]:
    base = [series("up", "1x40"), series("render_control_snapshot_success", "1x40"),
            series('render_control_jobs_oldest_seconds{state="queued",kind="customer"}', "0x40"),
            series('render_control_jobs_oldest_seconds{state="admission_pending",kind="customer"}', "0x40"),
            series('render_control_jobs_age_unknown{state="queued",kind="customer"}', "0x40"),
            series('render_control_gpu_occupancy{status="uncertain"}', "0x40"),
            series("render_control_node_heartbeat_oldest_seconds", "1x40"),
            series('render_control_nodes{status="observation_unknown"}', "0x40"),
            series('render_control_worker_capacity_samples{status="unknown"}', "0x40"),
            series('render_control_outbox_oldest_seconds{kind="settlement"}', "0x40"),
            series('render_control_http_requests_total{operation="business_write",method="POST",status_class="2xx",completion="completed"}', "0+10x40"),
            series('render_control_http_requests_total{operation="business_write",method="POST",status_class="5xx",completion="completed"}', "0x40")]

    def case(name: str, inputs: list[dict[str, str]], firing: set[str]) -> dict[str, Any]:
        return {"name": name, "interval": "15s", "input_series": inputs, "alert_rule_test": [{"eval_time": "5m", "alertname": r["alert"], "exp_alerts": [{"exp_labels": {"job": "render-control", "severity": r["labels"]["severity"], **({"instance": "native-a"} if r["alert"] == "RenderControlTargetDown" else {})}, "exp_annotations": r["annotations"]}] if r["alert"] in firing else []} for r in rules]}

    bad_values = ("0x40", "0x40", "301x40", "61x40", "1x40", "1x40", "31x40", "1x40", "1x40", "301x40", "0+10x40", "0+10x40")
    bad = [{**sample, "values": value} for sample, value in zip(base, bad_values, strict=True)]
    duplicate = [dict(sample) for sample in base]
    duplicate[2]["values"] = "200x40"
    duplicate.append(series('render_control_jobs_oldest_seconds{state="queued",kind="customer"}', "200x40", instance="native-b"))
    small = [dict(sample) for sample in base]
    small[-2]["values"], small[-1]["values"] = "0x40", "0x15 1x25"
    unavailable = [series("up", "1x40"), series("render_control_snapshot_success", "0x40")]
    return {"rule_files": ["rules.json"], "evaluation_interval": "15s", "tests": [case("healthy", base, set()), case("all_anomalies", bad, {r["alert"] for r in rules}), case("duplicate_global_snapshot", duplicate, set()), case("low_traffic_errors", small, set()), case("database_unavailable_without_fake_zero", unavailable, {"RenderControlSnapshotUnavailable"})]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promtool", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = args.report.resolve()
    if (APP.parent / "docs/evidence").resolve() not in report.parents or report.exists():
        raise SystemExit("A new report beneath project docs/evidence is required")
    policy = json.loads((APP / "config/monitoring-tools.json").read_text(encoding="utf-8"))
    binary = args.promtool.resolve(strict=True)
    version = subprocess.run([str(binary), "--version"], capture_output=True, timeout=10, check=True).stdout.decode("utf-8")  # noqa: S603 -- explicit native validation tool
    if "version " + policy["prometheus_version"] not in version:
        raise SystemExit("Unexpected promtool version")
    root = APP / ".local/monitoring-rules" / uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=False)
    rules = alert_rules(MonitoringThresholds())
    (root / "rules.json").write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
    (root / "tests.json").write_text(json.dumps(scenarios(rules["groups"][0]["rules"]), indent=2) + "\n", encoding="utf-8")
    checks = []
    for name, arguments in (("syntax", ["check", "rules", "rules.json"]), ("behavior", ["test", "rules", "tests.json"])):
        result = subprocess.run([str(binary), *arguments], cwd=root, capture_output=True, timeout=60)  # noqa: S603 -- fixed syntax/test subcommands, no server
        (root / (name + ".log")).write_bytes(result.stdout + result.stderr)
        checks.append({"name": name, "passed": result.returncode == 0})
    evidence = {"at": datetime.now(UTC).isoformat(), "passed": all(c["passed"] for c in checks), "checks": checks, "version": policy["prometheus_version"], "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "rules_sha256": hashlib.sha256((root / "rules.json").read_bytes()).hexdigest(), "alerts": 10, "scenarios": 5, "alert_expectations": 50, "production_monitoring_deployed": False, "virtualization_used": False, "processes_retained": 0}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence))
    raise SystemExit(0 if evidence["passed"] else 1)


if __name__ == "__main__":
    main()
