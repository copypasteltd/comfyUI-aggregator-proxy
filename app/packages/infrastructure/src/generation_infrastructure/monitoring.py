"""Dedicated metrics registry and fail-closed snapshot cache."""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import threading
import time
from collections.abc import Iterable
from ipaddress import IPv6Address, ip_address, ip_network

from generation_contracts.monitoring import MonitoringConfiguration
from generation_domain.values import utcnow
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, Metric
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from generation_infrastructure.monitoring_snapshot import (
    Snapshot,
    SnapshotReader,
    SnapshotUnavailable,
    monitoring_engine,
)
from generation_infrastructure.settings import Settings

HTTP_GROUPS = ("health", "admin_read", "admin_write", "business_read", "business_write", "gateway_read", "gateway_write", "worker_read", "worker_write", "monitoring", "admin_static", "other")
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15, 60, 300, 3600)
SNAPSHOT_FAILURES = frozenset(("busy", "disabled", "timeout", "database", "capacity_limit", "invalid_data", "other"))
MAX_EXPOSITION_BYTES = 2 * 1024**2


class ProcessCpu:
    def collect(self) -> Iterable[Metric]:
        yield CounterMetricFamily("render_control_process_cpu_seconds", "CPU seconds used by this API process.", value=time.process_time())


def configuration(settings: Settings) -> MonitoringConfiguration:
    return MonitoringConfiguration(enabled=bool(settings.monitoring_token_sha256), credential_slots=len(settings.monitoring_token_sha256), source_network_count=len(settings.monitoring_allowed_networks), thresholds=settings.monitoring_thresholds)


def monitoring_authorized(settings: Settings, authorization: str, peer: str | None) -> bool:
    if not settings.monitoring_token_sha256 or peer is None:
        return False
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not re.fullmatch(r"mon_[0-9a-f]{32}\.[A-Za-z0-9_-]{43}", token):
        return False
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    matches = [hmac.compare_digest(digest, value.get_secret_value()) for value in settings.monitoring_token_sha256]
    try:
        address = ip_address(peer)
        if isinstance(address, IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        allowed = any(address in ip_network(network) for network in settings.monitoring_allowed_networks)
    except ValueError:
        return False
    return any(matches) and allowed


class ProcessMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=False)
        self.requests = Counter("render_control_http_requests", "Finished HTTP requests; fixed operation groups.", ("operation", "method", "status_class", "completion"), registry=self.registry)
        self.rejections = Counter("render_control_http_rejections", "Rejected requests by bounded reason.", ("operation", "reason"), registry=self.registry)
        self.inflight = Gauge("render_control_http_inflight", "Current HTTP requests including active streams.", ("operation",), registry=self.registry)
        self.header_time = Histogram("render_control_http_headers_seconds", "Time until response headers.", ("operation",), buckets=HTTP_BUCKETS, registry=self.registry)
        self.response_time = Histogram("render_control_http_response_seconds", "Full request duration including streaming.", ("operation",), buckets=HTTP_BUCKETS, registry=self.registry)
        self.request_bytes = Counter("render_control_http_request_bytes", "Observed incoming payload bytes, without pre-reading.", ("operation",), registry=self.registry)
        self.response_bytes = Counter("render_control_http_response_bytes", "Delivered outgoing payload bytes.", ("operation",), registry=self.registry)
        self.log_dropped = Counter("render_control_http_log_dropped", "Operational log events dropped by the bounded sink.", registry=self.registry)
        self.snapshot_failures = Counter("render_control_snapshot_failures", "Unavailable database snapshots by bounded reason.", ("reason",), registry=self.registry)
        self.snapshot_time = Histogram("render_control_snapshot_refresh_seconds", "Database snapshot refresh duration.", buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 3), registry=self.registry)
        Gauge("render_control_registry_start_timestamp_seconds", "API metrics registry initialization time.", registry=self.registry).set(time.time())
        self.registry.register(ProcessCpu())


class MonitoringService:
    def __init__(self, settings: Settings) -> None:
        self.process = ProcessMetrics()
        self.reader = SnapshotReader(monitoring_engine(settings)) if settings.monitoring_token_sha256 else None
        self._lock = threading.Lock()
        self._cached: Snapshot | None = None
        self._refreshed = -math.inf
        self.last_success_at = math.nan

    def snapshot(self) -> Snapshot | None:
        if time.monotonic() - self._refreshed < 5:
            return self._cached
        if not self._lock.acquire(blocking=False):
            self.process.snapshot_failures.labels("busy").inc()
            return None
        started = time.monotonic()
        try:
            if started - self._refreshed < 5:
                return self._cached
            try:
                if self.reader is None:
                    raise SnapshotUnavailable("disabled")
                value = self.reader.read(utcnow())
            except (SQLAlchemyError, SnapshotUnavailable) as exc:
                if isinstance(exc, SnapshotUnavailable):
                    reason = exc.reason
                elif isinstance(exc, PoolTimeout):
                    reason = "busy"
                elif isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "57014":
                    reason = "timeout"
                else:
                    reason = "database"
                self.process.snapshot_failures.labels(reason if reason in SNAPSHOT_FAILURES else "other").inc()
                value = None
            self._cached = value
            self._refreshed = time.monotonic()
            if value is not None:
                self.last_success_at = time.time()
            return value
        finally:
            self.process.snapshot_time.observe(time.monotonic() - started)
            self._lock.release()

    def render(self) -> bytes:
        snapshot = self.snapshot()
        registry = CollectorRegistry(auto_describe=False)
        Gauge("render_control_snapshot_success", "Current database snapshot is available and within its five second cache.", registry=registry).set(int(snapshot is not None))
        Gauge("render_control_snapshot_last_success_timestamp_seconds", "Last successful refresh; NaN before the first success.", registry=registry).set(self.last_success_at)
        Gauge("render_control_snapshot_age_seconds", "Age of the most recent successful refresh, even when unavailable.", registry=registry).set(max(0, time.time() - self.last_success_at) if math.isfinite(self.last_success_at) else math.nan)
        if snapshot is not None:
            registry.register(snapshot)
        payload = generate_latest(self.process.registry) + generate_latest(registry)
        if len(payload) > MAX_EXPOSITION_BYTES:
            raise SnapshotUnavailable("exposition_limit")
        return payload

    def close(self) -> None:
        if self.reader is not None:
            self.reader.engine.dispose()
