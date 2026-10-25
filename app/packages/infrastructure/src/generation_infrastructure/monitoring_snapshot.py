"""Bounded, read-only operational projection; never changes ownership or task state."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from prometheus_client.core import GaugeMetricFamily, Metric
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import RowMapping

from generation_infrastructure.settings import Settings
from generation_infrastructure.worker_capacity import capacity_snapshot

TASKS = ("text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "other")
ACTIVE_STATES = ("admission_pending", "queued", "leased", "preparing", "submitting", "running", "collecting", "uploading", "verifying", "reconciling", "retry_wait", "cancel_requested")
KINDS = ("customer", "validation")
ERROR_CLASSES = ("oom", "output_invalid", "preparation", "storage", "timeout", "other")
DATABASE_GAUGES: dict[str, tuple[str, ...]] = {
    "jobs_active": ("state", "task_type", "kind"), "jobs_oldest_seconds": ("state", "kind"),
    "jobs_age_unknown": ("state", "kind"),
    "queued_candidates": ("coverage",), "jobs_recent": ("outcome", "task_type", "kind"),
    "job_failures_recent": ("error_class", "task_type", "kind"),
    "job_duration_seconds": ("task_type", "kind", "quantile"), "job_duration_observations": ("task_type", "kind"),
    "gpu_resources": ("status",), "gpu_occupancy": ("status",), "nodes": ("status",),
    "node_heartbeat_oldest_seconds": (), "channels": ("status",), "spec_isolations": (),
    "worker_capacity_samples": ("status",), "worker_capacity_observed": ("resource",),
    "worker_capacity_bytes": ("resource", "aggregation"), "worker_pending_uploads": (),
    "outbox_pending": ("kind",), "outbox_oldest_seconds": ("kind",),
    "objects_pending_delete": (), "objects_pending_delete_bytes": (), "objects_pending_delete_oldest_seconds": (),
    "pg_connections": ("state",), "pg_lock_waits": (),
}
type Query = Callable[[str], Sequence[RowMapping]]


class SnapshotUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class Snapshot:
    def __init__(self) -> None:
        self.values: dict[str, dict[tuple[str, ...], float]] = {name: {} for name in DATABASE_GAUGES}

    def set(self, name: str, value: float, *labels: str) -> None:
        if len(labels) != len(DATABASE_GAUGES[name]):
            raise ValueError("Invalid operational label arity")
        self.values[name][labels] = float(value)

    def add(self, name: str, value: float, *labels: str) -> None:
        self.set(name, self.values[name].get(labels, 0) + value, *labels)

    def collect(self) -> Iterable[Metric]:
        for name, labels in DATABASE_GAUGES.items():
            family = GaugeMetricFamily("render_control_" + name, "Read-only PostgreSQL snapshot: " + name, labels=labels)
            for values, value in sorted(self.values[name].items()):
                family.add_metric(list(values), value)
            yield family


def monitoring_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url.get_secret_value(), hide_parameters=True, pool_size=1, max_overflow=0, pool_timeout=0.1, isolation_level="REPEATABLE READ", connect_args={"connect_timeout": 1, "options": "-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=100"})


class SnapshotReader:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def read(self, now: datetime) -> Snapshot:
        result = Snapshot()
        with self.engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            deadline = time.monotonic() + 2

            def query(sql: str) -> Sequence[RowMapping]:
                return self._query(connection, deadline, sql, now)

            self._jobs(result, query)
            self._resources(result, query)
            self._capacity(result, query, now)
            self._backlogs(result, query)
            if time.monotonic() > deadline:
                raise SnapshotUnavailable("timeout")
        return result

    @staticmethod
    def _query(connection: Connection, deadline: float, sql: str, now: datetime) -> Sequence[RowMapping]:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise SnapshotUnavailable("timeout")
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {min(remaining, 1000)}")
        return connection.execute(text(sql), {"now": now}).mappings().all()

    @staticmethod
    def _jobs(result: Snapshot, query: Query) -> None:
        task_case = "CASE WHEN task_type IN ('text_to_image','image_to_image','references_to_image','text_to_video','references_to_video','text_to_audio') THEN task_type ELSE 'other' END"
        for kind in KINDS:
            for state in ACTIVE_STATES:
                result.set("jobs_oldest_seconds", 0, state, kind)
                result.set("jobs_age_unknown", 0, state, kind)
                for task in TASKS:
                    result.set("jobs_active", 0, state, task, kind)
            for task in TASKS:
                result.set("job_duration_observations", 0, task, kind)
                for state in ("succeeded", "failed", "cancelled", "expired"):
                    result.set("jobs_recent", 0, state, task, kind)
                for error in ERROR_CLASSES:
                    result.set("job_failures_recent", 0, error, task, kind)
        for coverage in ("with_candidates", "without_candidates"):
            result.set("queued_candidates", 0, coverage)
        rows = query(f"""SELECT state, {task_case} AS task, is_validation, count(*) AS count,
            extract(epoch FROM (:now-min(coalesce(accepted_at,created_at)) FILTER (WHERE coalesce(accepted_at,created_at) <= :now))) AS age,
            count(*) FILTER (WHERE coalesce(accepted_at,created_at) > :now) AS unknown_age,
            count(*) FILTER (WHERE jsonb_typeof(eligible_channels)='array' AND jsonb_array_length(eligible_channels)>0) AS candidates
            FROM jobs WHERE state NOT IN ('succeeded','failed','cancelled','expired') GROUP BY state,task,is_validation""")  # noqa: S608 -- constant SQL projection; time is bound
        valid_ages: dict[tuple[str, str], list[float]] = {}
        for row in rows:
            state, task, kind = row["state"], row["task"], "validation" if row["is_validation"] else "customer"
            if state not in ACTIVE_STATES or task not in TASKS:
                raise SnapshotUnavailable("invalid_data")
            result.set("jobs_active", row["count"], state, task, kind)
            valid_ages.setdefault((state, kind), [])
            if row["age"] is not None:
                valid_ages[(state, kind)].append(float(row["age"]))
            result.add("jobs_age_unknown", row["unknown_age"], state, kind)
            if state == "queued":
                result.add("queued_candidates", row["candidates"], "with_candidates")
                result.add("queued_candidates", row["count"] - row["candidates"], "without_candidates")
        for labels, ages in valid_ages.items():
            if ages:
                result.set("jobs_oldest_seconds", max(ages), *labels)
            else:
                result.values["jobs_oldest_seconds"].pop(labels)
        rows = query(f"""SELECT state, {task_case} AS task, is_validation,
            CASE WHEN error_code='comfy_oom' THEN 'oom' WHEN error_code='comfy_output_invalid' THEN 'output_invalid'
            WHEN error_code='preparation_transport_failure' THEN 'preparation'
            WHEN error_code IN ('storage_unavailable','output_upload_failed','output_verification_failed') THEN 'storage'
            WHEN error_code IN ('deadline_exceeded','job_expired','execution_timeout') THEN 'timeout' ELSE 'other' END AS error,
            count(*) AS count FROM jobs WHERE terminal_at > :now - interval '1 hour' AND terminal_at <= :now
            GROUP BY state,task,is_validation,error""")  # noqa: S608 -- constant SQL projection; time is bound
        for row in rows:
            state, task, kind = row["state"], row["task"], "validation" if row["is_validation"] else "customer"
            if state not in ("succeeded", "failed", "cancelled", "expired"):
                raise SnapshotUnavailable("invalid_data")
            result.add("jobs_recent", row["count"], state, task, kind)
            if state == "failed":
                result.add("job_failures_recent", row["count"], row["error"], task, kind)
        rows = query(f"""SELECT {task_case} AS task,is_validation,count(*) AS count,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (terminal_at-accepted_at))) AS p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM (terminal_at-accepted_at))) AS p95
            FROM jobs WHERE terminal_at > :now - interval '1 hour' AND terminal_at <= :now
            AND accepted_at IS NOT NULL AND accepted_at <= terminal_at GROUP BY task,is_validation""")  # noqa: S608 -- constant SQL projection; time is bound
        for row in rows:
            task, kind = row["task"], "validation" if row["is_validation"] else "customer"
            result.set("job_duration_observations", row["count"], task, kind)
            for quantile, field in (("0.5", "p50"), ("0.95", "p95")):
                result.set("job_duration_seconds", row[field], task, kind, quantile)

    @staticmethod
    def _resources(result: Snapshot, query: Query) -> None:
        row = query("""SELECT
            (SELECT count(*) FROM gpu_resources) AS registered,
            (SELECT count(*) FROM gpu_resources WHERE enabled) AS enabled,
            (SELECT count(*) FROM gpu_resources WHERE management_hold IS NOT NULL) AS management_hold,
            (SELECT count(*) FROM gpu_occupancies) AS held,
            (SELECT count(*) FROM gpu_occupancies WHERE uncertain) AS uncertain,
            (SELECT count(*) FROM nodes WHERE enabled) AS nodes,
            (SELECT count(*) FROM nodes WHERE enabled AND (observed_at IS NULL OR observed_at <= :now - interval '15 seconds' OR observed_at > :now)) AS stale,
            (SELECT count(*) FROM nodes WHERE enabled AND (observed_at IS NULL OR observed_at > :now)) AS unknown,
            (SELECT GREATEST(0,extract(epoch FROM (:now-min(coalesce(observed_at,created_at))))) FROM nodes WHERE enabled) AS heartbeat_age,
            (SELECT count(*) FROM channels WHERE enabled AND deleted_at IS NULL) AS channels,
            (SELECT count(*) FROM channels WHERE enabled AND deleted_at IS NULL AND (observed_at IS NULL OR observed_at <= :now - interval '15 seconds' OR observed_at > :now)) AS channel_stale,
            (SELECT count(*) FROM channels WHERE enabled AND deleted_at IS NULL AND (applied_revision IS DISTINCT FROM desired_revision OR verified_revision IS DISTINCT FROM desired_revision)) AS unverified,
            (SELECT count(*) FROM spec_isolations WHERE is_active) AS isolated""")[0]
        for status in ("registered", "enabled", "management_hold"):
            result.set("gpu_resources", row[status], status)
        for status in ("held", "uncertain"):
            result.set("gpu_occupancy", row[status], status)
        result.set("nodes", row["nodes"], "enabled")
        result.set("nodes", row["stale"], "stale")
        result.set("nodes", row["unknown"], "observation_unknown")
        if row["heartbeat_age"] is not None:
            result.set("node_heartbeat_oldest_seconds", row["heartbeat_age"])
        result.set("channels", row["channels"], "enabled")
        result.set("channels", row["channel_stale"], "stale")
        result.set("channels", row["unverified"], "unverified")
        result.set("spec_isolations", row["isolated"])

    @staticmethod
    def _capacity(result: Snapshot, query: Query, now: datetime) -> None:
        rows = query("""SELECT current_boot_id,
            pg_column_size(inventory->'capacity') > 8192 AS oversized,
            CASE WHEN pg_column_size(inventory->'capacity') <= 8192 THEN inventory->'capacity' END AS capacity
            FROM nodes WHERE enabled ORDER BY id LIMIT 257""")
        if len(rows) > 256 or any(row["oversized"] for row in rows):
            raise SnapshotUnavailable("capacity_limit")
        for status in ("ready", "blocked", "unavailable", "unknown"):
            result.set("worker_capacity_samples", 0, status)
        resources: dict[str, list[int]] = {name: [] for name in ("free_disk", "available_memory", "spool", "reserved", "pending_uploads")}
        for row in rows:
            sample = capacity_snapshot({"capacity": row["capacity"]}, row["current_boot_id"], now)
            result.add("worker_capacity_samples", 1, sample.status if sample else "unknown")
            if sample:
                for name, field in (("free_disk", "free_disk_bytes"), ("available_memory", "available_memory_bytes"), ("spool", "spool_bytes"), ("reserved", "reserved_bytes"), ("pending_uploads", "pending_uploads")):
                    value = getattr(sample, field)
                    if value is not None:
                        resources[name].append(value)
        for name, values in resources.items():
            result.set("worker_capacity_observed", len(values), name)
            if values:
                if name == "pending_uploads":
                    result.set("worker_pending_uploads", sum(values))
                else:
                    aggregation = "minimum" if name in ("free_disk", "available_memory") else "sum"
                    result.set("worker_capacity_bytes", min(values) if aggregation == "minimum" else sum(values), name, aggregation)

    @staticmethod
    def _backlogs(result: Snapshot, query: Query) -> None:
        for kind in ("settlement", "other"):
            result.set("outbox_pending", 0, kind)
            result.set("outbox_oldest_seconds", 0, kind)
        for row in query("""SELECT CASE WHEN kind='gateway.settlement' THEN 'settlement' ELSE 'other' END AS bucket,
            count(*) AS count,GREATEST(0,extract(epoch FROM (:now-min(created_at)))) AS age
            FROM outbox_events WHERE delivered_at IS NULL GROUP BY bucket"""):
            result.set("outbox_pending", row["count"], row["bucket"])
            result.set("outbox_oldest_seconds", row["age"], row["bucket"])
        row = query("""SELECT count(*) AS count,coalesce(sum(bytes),0) AS bytes,
            coalesce(GREATEST(0,extract(epoch FROM (:now-min(delete_requested_at)))),0) AS age
            FROM object_records WHERE delete_requested_at IS NOT NULL AND deleted_at IS NULL""")[0]
        for name, field in (("objects_pending_delete", "count"), ("objects_pending_delete_bytes", "bytes"), ("objects_pending_delete_oldest_seconds", "age")):
            result.set(name, row[field])
        for state in ("active", "idle", "other"):
            result.set("pg_connections", 0, state)
        result.set("pg_lock_waits", 0)
        for row in query("""SELECT CASE WHEN state IN ('active','idle') THEN state ELSE 'other' END AS bucket,
            count(*) AS count,count(*) FILTER (WHERE wait_event_type='Lock') AS waits
            FROM pg_stat_activity WHERE datname=current_database() GROUP BY bucket"""):
            result.set("pg_connections", row["count"], row["bucket"])
            result.add("pg_lock_waits", row["waits"])
