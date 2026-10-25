"""Offline, exclusive-file monitoring provisioning; no business DB connections."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from generation_contracts.monitoring import MonitoringThresholds
from generation_infrastructure.monitoring import configuration
from generation_infrastructure.settings import Settings


def write_exclusive(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(value + "\n")
    path.chmod(0o600)


def alert_rules(thresholds: MonitoringThresholds) -> dict[str, Any]:
    t = thresholds
    denominator = 'sum by (job) (increase(render_control_http_requests_total{operation!~"health|monitoring|admin_static|other"}[5m]))'
    numerator = 'sum by (job) (increase(render_control_http_requests_total{operation!~"health|monitoring|admin_static|other",status_class="5xx"}[5m]))'
    conditions = (
        ("RenderControlTargetDown", 'up{job="render-control"} == 0', "critical"),
        ("RenderControlSnapshotUnavailable", "min by (job) (render_control_snapshot_success) == 0", "warning"),
        ("RenderControlQueueAging", f'max by (job) (render_control_jobs_oldest_seconds{{state="queued",kind="customer"}}) > {t.queue_age_seconds}', "warning"),
        ("RenderControlAdmissionAging", f'max by (job) (render_control_jobs_oldest_seconds{{state="admission_pending",kind="customer"}}) > {t.pending_age_seconds}', "warning"),
        ("RenderControlJobClockUnknown", "max by (job) (render_control_jobs_age_unknown) > 0", "warning"),
        ("RenderControlUnknownGpu", 'max by (job) (render_control_gpu_occupancy{status="uncertain"}) > 0', "critical"),
        ("RenderControlWorkerStale", f'max by (job) (render_control_node_heartbeat_oldest_seconds) > {t.node_stale_seconds} or max by (job) (render_control_nodes{{status="observation_unknown"}}) > 0', "warning"),
        ("RenderControlWorkerCapacity", 'max by (job) (render_control_worker_capacity_samples{status=~"blocked|unavailable|unknown"}) > 0', "warning"),
        ("RenderControlSettlementAging", f'max by (job) (render_control_outbox_oldest_seconds{{kind="settlement"}}) > {t.settlement_age_seconds}', "critical"),
        ("RenderControlHttpErrors", f"({numerator} / clamp_min({denominator}, 1) > {t.http_error_ratio}) and ({denominator} >= {t.minimum_requests})", "warning"),
    )
    return {"groups": [{"name": "render-control", "interval": "15s", "rules": [{"alert": name, "expr": expression, "for": f"{t.alert_for_seconds}s", "labels": {"severity": severity}, "annotations": {"summary": name, "runbook": "docs/84-监控指标与脱敏请求关联.md"}} for name, expression, severity in conditions]}]}


def main() -> None:
    parser = argparse.ArgumentParser(prog="gen-monitoring", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue-token")
    issue.add_argument("--output", type=Path, required=True)
    config = sub.add_parser("configuration")
    config.add_argument("--env-file", type=Path)
    rules = sub.add_parser("rules")
    rules.add_argument("--output", type=Path, required=True)
    rules.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "issue-token":
            token = "mon_" + secrets.token_hex(16) + "." + secrets.token_urlsafe(32)
            write_exclusive(args.output, token)
            print(json.dumps({"sha256": hashlib.sha256(token.encode("ascii")).hexdigest(), "credential_written": True}))
            return
        settings = Settings(_env_file=args.env_file.resolve(strict=True)) if args.env_file else Settings()
        if args.command == "configuration":
            print(configuration(settings).model_dump_json())
        else:
            write_exclusive(args.output, json.dumps(alert_rules(settings.monitoring_thresholds), ensure_ascii=True, indent=2))
            print(json.dumps({"rules_written": True, "alerts": 10}))
    except FileExistsError:
        raise SystemExit("monitoring_output_exists") from None
    except OSError:
        raise SystemExit("monitoring_io_failed") from None
    except ValueError:
        raise SystemExit("monitoring_configuration_invalid") from None


if __name__ == "__main__":
    main()
