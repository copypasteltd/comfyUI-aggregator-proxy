from __future__ import annotations

from datetime import timedelta
from typing import Any

from generation_contracts.alert_queries import AlertKind, AlertQuery
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import (
    BigInteger,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    not_,
    or_,
    select,
    union_all,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, timestamp
from generation_infrastructure.admin_channels import begin_channel_read
from generation_infrastructure.models import (
    Attempt,
    Channel,
    GpuOccupancy,
    Job,
    ManagementOperation,
    Node,
    OutboxEvent,
    SpecIsolation,
)
from generation_infrastructure.worker_capacity import capacity_snapshot


def signal(kind: str, prefix: str, identifier: Any, recorded_at: Any, *conditions: Any) -> Any:
    return select(identifier.label("resource_id"), literal(kind).label("kind"), literal(prefix).label("prefix"), literal(0 if kind == "gpu_occupancy_uncertain" else 1).label("severity_rank"), recorded_at.label("recorded_at")).where(*conditions)


class AlertQueries(AdminBase):
    def snapshot(self, page: int = 1, kind: AlertKind | None = None, *, query: AlertQuery | None = None) -> dict[str, Any]:
        selected = query or AlertQuery(page=page, kind=kind)
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                return self._snapshot(session, selected)
        except DBAPIError:
            raise DomainError("alert_query_unavailable", "告警读取暂不可用，请稍后刷新", 503) from None

    def _snapshot(self, session: Session, query: AlertQuery) -> dict[str, Any]:
        now = utcnow()
        heartbeat_cutoff = now - timedelta(seconds=15)
        backlog_cutoff = now - timedelta(seconds=300)
        node_observed = func.coalesce(Node.observed_at, Node.created_at)
        channel_observed = func.coalesce(Channel.observed_at, Channel.created_at)
        live_channel = [Channel.enabled.is_(True), Channel.deleted_at.is_(None)]
        capacity = Node.inventory["capacity"]
        sample = capacity["sample"]
        raw_observed = capacity["observed_at_ms"]
        observed_ms = case((and_(func.jsonb_typeof(raw_observed) == "number", raw_observed.astext.op("~")(r"^[0-9]{1,16}$")), cast(raw_observed.astext, BigInteger)), else_=None)
        known = func.coalesce(and_(capacity["boot_id"].astext == cast(Node.current_boot_id, String), observed_ms > int(now.timestamp() * 1000) - 15000, observed_ms <= int(now.timestamp() * 1000), sample["status"].astext.in_(("ready", "blocked", "unavailable"))), False)
        live_node = [Node.enabled.is_(True), node_observed > heartbeat_cutoff]
        alerts = union_all(
            signal("spec_isolation", "iso", SpecIsolation.id, SpecIsolation.isolated_at, SpecIsolation.is_active.is_(True)),
            signal("gpu_occupancy_uncertain", "gpu", GpuOccupancy.gpu_id, GpuOccupancy.acquired_at, GpuOccupancy.uncertain.is_(True)),
            signal("job_reconciling", "job", Job.id, Job.accepted_at, Job.state == "reconciling"),
            signal("node_heartbeat_stale", "node", Node.id, node_observed, Node.enabled.is_(True), node_observed <= heartbeat_cutoff),
            signal("channel_observation_stale", "ch", Channel.id, channel_observed, *live_channel, channel_observed <= heartbeat_cutoff),
            signal("channel_config_unverified", "ch", Channel.id, Channel.created_at, *live_channel, or_(Channel.applied_revision.is_(None), Channel.applied_revision != Channel.desired_revision, Channel.verified_revision.is_(None), Channel.verified_revision != Channel.desired_revision)),
            signal("job_waiting_over_5m", "job", Job.id, Job.accepted_at, Job.state == "queued", Job.accepted_at <= backlog_cutoff),
            signal("operation_failed", "op", ManagementOperation.id, ManagementOperation.created_at, ManagementOperation.state.in_(("failed", "partial_failed")), ManagementOperation.created_at >= now - timedelta(days=1), ManagementOperation.created_at <= now),
            signal("gateway_settlement_unacked", "evt", OutboxEvent.id, OutboxEvent.created_at, OutboxEvent.kind == "gateway.settlement", OutboxEvent.delivered_at.is_(None), OutboxEvent.created_at <= backlog_cutoff),
            signal("worker_storage_pressure", "node", Node.id, node_observed, *live_node, known, or_(sample["reasons"].contains(["disk_space"]), sample["reasons"].contains(["spool_limit"]))),
            signal("worker_memory_pressure", "node", Node.id, node_observed, *live_node, known, sample["reasons"].contains(["memory"])),
            signal("worker_upload_backlog", "node", Node.id, node_observed, *live_node, known, sample["reasons"].contains(["upload_backlog"])),
            signal("worker_capacity_unavailable", "node", Node.id, node_observed, *live_node, or_(not_(known), sample["status"].astext == "unavailable")),
            *[signal(name, "job", Job.id, Job.terminal_at, Job.state == "failed", Job.error_code == code, Job.terminal_at >= now - timedelta(days=1), Job.terminal_at <= now, Attempt.id == Job.current_attempt_id, Attempt.job_id == Job.id, Attempt.closed_at.is_not(None), Attempt.generation_released_at.is_not(None), Attempt.generation_released_at <= Attempt.closed_at, Attempt.closed_at <= Job.terminal_at, Job.accepted_at <= Job.terminal_at) for name, code in (("job_oom_recent", "comfy_oom"), ("job_output_invalid_recent", "comfy_output_invalid"))],
        ).subquery("active_alerts")
        conditions = []
        if query.kind is not None:
            conditions.append(alerts.c.kind == query.kind)
        if query.severity is not None:
            conditions.append(alerts.c.severity_rank == (0 if query.severity == "critical" else 1))
        if query.resource_id is not None:
            prefix, _ = query.resource_id.split("_", 1)
            conditions.extend((alerts.c.prefix == prefix, alerts.c.resource_id == parse_id(prefix, query.resource_id)))
        filtered = and_(True, *conditions)
        total = session.scalar(select(func.count()).select_from(alerts).where(filtered)) or 0
        rows = list(session.execute(select(alerts).where(filtered).order_by(alerts.c.severity_rank, alerts.c.recorded_at, alerts.c.kind, alerts.c.resource_id).offset((query.page - 1) * 50).limit(51)))
        data = []
        node_ids = [row.resource_id for row in rows[:50] if row.kind.startswith("worker_")]
        nodes = {node.id: node for node in session.scalars(select(Node).where(Node.id.in_(node_ids)))} if node_ids else {}
        failed_ids = [row.resource_id for row in rows[:50] if row.kind in ("job_oom_recent", "job_output_invalid_recent")]
        failures = {row.id: {"code": row.error_code, "task_type": row.task_type, "model": row.model_alias, "release_id": public_id("wfr", row.release_id), "attempt_id": public_id("att", row.attempt_id), "channel_id": public_id("ch", row.channel_id)} for row in session.execute(select(Job.id, Job.error_code, Job.task_type, Job.model_alias, Job.release_id, Attempt.id.label("attempt_id"), Attempt.channel_id).join(Attempt, and_(Attempt.id == Job.current_attempt_id, Attempt.job_id == Job.id)).where(Job.id.in_(failed_ids)))} if failed_ids else {}
        for row in rows[:50]:
            resource = public_id(row.prefix, row.resource_id)
            item: dict[str, Any] = {"id": "alert:" + row.kind + ":" + resource, "kind": row.kind, "severity": "critical" if row.severity_rank == 0 else "warning", "resource_id": resource, "recorded_at": timestamp(row.recorded_at)}
            if row.kind in ("job_oom_recent", "job_output_invalid_recent"):
                item["failure"] = failures[row.resource_id]
            if node := nodes.get(row.resource_id):
                value = capacity_snapshot(node.inventory, node.current_boot_id, now)
                item["capacity"] = value.model_dump() if value else None
                item["capacity_observed_at"] = node.inventory["capacity"]["observed_at_ms"] // 1000 if value else None
            data.append(item)
        return {"snapshot_at": int(now.timestamp()), "data": data, "total": total, "page": query.page, "has_more": len(rows) > 50}
