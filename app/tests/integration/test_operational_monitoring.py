from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.values import public_id, utcnow
from generation_infrastructure.models import (
    Attempt,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    ObjectRecord,
    OutboxEvent,
)
from generation_infrastructure.monitoring import MonitoringService
from generation_infrastructure.monitoring_snapshot import (
    Snapshot,
    SnapshotReader,
    SnapshotUnavailable,
    monitoring_engine,
)
from generation_infrastructure.settings import Settings
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
METRICS = "/internal/monitoring/v1/metrics"
CONFIGURATION = "/internal/monitoring/v1/configuration"


def enabled(settings: Settings) -> tuple[Settings, str]:
    token = "mon_" + uuid4().hex + "." + "a" * 43
    return settings.model_copy(update={"monitoring_token_sha256": [SecretStr(hashlib.sha256(token.encode()).hexdigest())]}), token


def seed(session: Session, catalog: dict[str, Any], **values: Any) -> Job:
    owner, now = catalog["identity"], utcnow()
    defaults = dict(tenant_id=owner.tenant_id, project_id=owner.project_id, key_id=owner.key_id, release_id=catalog["release_id"], model_alias="synthetic-private-alias", task_type="text_to_image", state="queued", request={"input": {"prompt": "synthetic-private-prompt"}}, execution={}, request_fingerprint="a" * 64, execution_fingerprint="b" * 64, eligible_channels=[public_id("ch", i) for i in catalog["channel_ids"]], accepted_at=now - timedelta(minutes=10), created_at=now - timedelta(minutes=10), deadline=now + timedelta(days=1), accepted_period=now.date().isoformat())
    row = Job(**{**defaults, **values})
    session.add(row)
    session.flush()
    return row


def read(settings: Settings, now: Any = None) -> Snapshot:
    engine = monitoring_engine(settings)
    try:
        return SnapshotReader(engine).read(now or utcnow())
    finally:
        engine.dispose()


def samples(payload: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    return {(s.name, tuple(sorted(s.labels.items()))): s.value for f in text_string_to_metric_families(payload) for s in f.samples}


def test_pg_queue_is_deduplicated_and_does_not_export_private_identifiers(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    with pg.begin() as session:
        job = seed(session, catalog)
        identifier = str(job.id)
    snapshot = read(test_settings)
    assert snapshot.values["jobs_active"][("queued", "text_to_image", "customer")] == 1
    assert snapshot.values["queued_candidates"][("with_candidates",)] == 1
    assert snapshot.values["gpu_resources"][("registered",)] == 1
    assert snapshot.values["channels"][("enabled",)] == 2
    assert snapshot.values["jobs_oldest_seconds"][("queued", "customer")] >= 599
    serialized = "\n".join(str(f.samples) for f in snapshot.collect())
    for private in (identifier, str(catalog["gpu_id"]), "synthetic-private", str(catalog["identity"].key_id)):
        assert private not in serialized


def test_expired_disabled_gpu_remains_occupied_and_unknown(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    now = utcnow()
    with pg.begin() as session:
        job = seed(session, catalog, state="reconciling")
        attempt = Attempt(job_id=job.id, channel_id=catalog["channel_ids"][0], channel_snapshot={}, node_id=catalog["node_id"], boot_id=catalog["boot_id"], gpu_id=catalog["gpu_id"], fence=1, lease_expires_at=now - timedelta(hours=2))
        session.add(attempt)
        session.flush()
        job.current_attempt_id = attempt.id
        session.add(GpuOccupancy(gpu_id=catalog["gpu_id"], attempt_id=attempt.id, job_id=job.id, uncertain=True))
        session.get(Gpu, catalog["gpu_id"]).enabled = False
        session.get(Node, catalog["node_id"]).enabled = False
    snapshot = read(test_settings)
    assert snapshot.values["gpu_occupancy"] == {("held",): 1, ("uncertain",): 1}
    assert snapshot.values["gpu_resources"][("enabled",)] == 0
    assert snapshot.values["jobs_active"][("reconciling", "text_to_image", "customer")] == 1
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain
        assert session.get(Attempt, attempt.id).generation_released_at is None


@pytest.mark.parametrize("variant", ["missing", "stale", "future", "boot", "malformed"])
def test_capacity_unknown_is_not_healthy_or_zero_pressure(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, variant: str) -> None:
    now = utcnow()
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        inventory = json.loads(json.dumps(node.inventory))
        if variant == "missing":
            inventory = {}
        elif variant in ("stale", "future"):
            inventory["capacity"]["observed_at_ms"] = int(now.timestamp() * 1000) + (100000 if variant == "future" else -15000)
        elif variant == "boot":
            inventory["capacity"]["boot_id"] = str(uuid4())
        else:
            inventory["capacity"]["sample"]["free_disk_bytes"] = "private-invalid-value"
        node.inventory = inventory
    snapshot = read(test_settings, now)
    assert snapshot.values["worker_capacity_samples"][("unknown",)] == 1
    assert snapshot.values["worker_capacity_samples"][("ready",)] == 0
    assert not snapshot.values["worker_capacity_bytes"] and not snapshot.values["worker_pending_uploads"]
    assert all(value == 0 for value in snapshot.values["worker_capacity_observed"].values())


def test_healthy_capacity_and_outbox_lifecycle_are_aggregated(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    now = utcnow()
    with pg.begin() as session:
        owner = catalog["identity"]
        session.add(OutboxEvent(kind="gateway.settlement", aggregate_id=uuid4(), payload={"secret": "private-payload"}, created_at=now - timedelta(minutes=12)))
        session.add(OutboxEvent(kind="private-kind", aggregate_id=uuid4(), payload={}, created_at=now - timedelta(minutes=2)))
        session.add(OutboxEvent(kind="gateway.settlement", aggregate_id=uuid4(), payload={}, delivered_at=now, created_at=now - timedelta(hours=2)))
        session.add(ObjectRecord(tenant_id=owner.tenant_id, project_id=owner.project_id, object_key="private-object", bytes=123, sha256="a" * 64, media_type="image/png", expires_at=now, delete_requested_at=now - timedelta(seconds=99)))
    snapshot = read(test_settings)
    assert snapshot.values["worker_capacity_samples"][("ready",)] == 1
    assert snapshot.values["worker_capacity_observed"][("free_disk",)] == 1
    assert snapshot.values["worker_capacity_bytes"][("free_disk", "minimum")] == 100 * 1024**3
    assert snapshot.values["outbox_pending"] == {("settlement",): 1, ("other",): 1}
    assert snapshot.values["outbox_oldest_seconds"][("settlement",)] >= 719
    assert snapshot.values["objects_pending_delete"][()] == 1
    assert snapshot.values["objects_pending_delete_bytes"][()] == 123


def test_recent_windows_and_duration_coverage_reject_bad_clocks(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    now = utcnow()
    terminal = now - timedelta(seconds=1)
    with pg.begin() as session:
        for duration in (10, 30):
            seed(session, catalog, state="succeeded", accepted_at=terminal-timedelta(seconds=duration), terminal_at=terminal)
        seed(session, catalog, state="succeeded", accepted_at=None, terminal_at=terminal)
        seed(session, catalog, state="succeeded", accepted_at=now, terminal_at=terminal)
        seed(session, catalog, state="succeeded", terminal_at=now+timedelta(seconds=1))
        seed(session, catalog, state="succeeded", terminal_at=now-timedelta(hours=1))
        seed(session, catalog, state="failed", task_type="text_to_audio", error_code="comfy_oom", terminal_at=terminal)
    snapshot = read(test_settings, now)
    assert snapshot.values["jobs_recent"][("succeeded", "text_to_image", "customer")] == 4
    assert snapshot.values["job_duration_observations"][("text_to_image", "customer")] == 2
    assert snapshot.values["job_duration_seconds"][("text_to_image", "customer", "0.5")] == 20
    assert snapshot.values["job_duration_seconds"][("text_to_image", "customer", "0.95")] == pytest.approx(29)
    assert snapshot.values["job_failures_recent"][("oom", "text_to_audio", "customer")] == 1


def test_future_queue_time_is_unknown_and_does_not_look_fresh(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    now = utcnow()
    with pg.begin() as session:
        seed(session, catalog, accepted_at=now+timedelta(days=1), created_at=now+timedelta(days=1))
    snapshot = read(test_settings, now)
    assert snapshot.values["jobs_active"][("queued", "text_to_image", "customer")] == 1
    assert snapshot.values["jobs_age_unknown"][("queued", "customer")] == 1
    assert ("queued", "customer") not in snapshot.values["jobs_oldest_seconds"]


def test_capacity_node_limit_fails_explicitly_without_truncating(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    with pg.begin() as session:
        for i in range(256):
            session.add(Node(name="Synthetic bounded node", credential_identifier=f"{i+100:032x}", credential_verifier="0"*64))
    with pytest.raises(SnapshotUnavailable, match="capacity_limit"):
        read(test_settings)


def test_reader_uses_separate_read_only_repeatable_read_pool(test_settings: Settings, pg: sessionmaker[Session]) -> None:
    engine = monitoring_engine(test_settings)
    observed = []
    def inspect(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: Any) -> None:
        if statement.startswith("SELECT state,"):
            observed.append(tuple(connection.exec_driver_sql("SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')").one()))
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        SnapshotReader(engine).read(utcnow())
        assert observed and all(value == ("on", "repeatable read") for value in observed)
        assert engine is not pg.kw["bind"]
        with engine.connect() as connection, pytest.raises(DBAPIError) as failure:
            connection.exec_driver_sql("UPDATE nodes SET enabled=false")
        assert failure.value.orig.sqlstate == "25006"
    finally:
        engine.dispose()


def test_ddl_lock_failure_is_bounded_and_does_not_report_zero_queue(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    settings, _ = enabled(test_settings)
    service = MonitoringService(settings)
    try:
        with pg.begin() as session:
            session.execute(text("LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE"))
            start = time.monotonic()
            payload = service.render().decode()
            assert time.monotonic()-start < 2
            assert "render_control_snapshot_success 0.0" in payload and "render_control_jobs_active{" not in payload
        service._refreshed = float("-inf")
        assert "render_control_snapshot_success 1.0" in service.render().decode()
    finally:
        service.close()


def test_one_refresh_in_progress_never_blocks_another_scrape(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, _ = enabled(test_settings)
    service = MonitoringService(settings)
    assert service.reader
    entered, release = threading.Event(), threading.Event()
    results = []
    def slow(now: Any) -> Snapshot:
        entered.set()
        assert release.wait(3)
        return Snapshot()
    monkeypatch.setattr(service.reader, "read", slow)
    thread = threading.Thread(target=lambda: results.append(service.render()))
    try:
        thread.start()
        assert entered.wait(2)
        start = time.monotonic()
        assert "render_control_snapshot_success 0.0" in service.render().decode()
        assert time.monotonic()-start < 0.5
    finally:
        release.set()
        thread.join(3)
        service.close()
    assert not thread.is_alive() and len(results) == 1
    assert b"render_control_snapshot_success 1.0" in results[0]


def test_capacity_projection_is_bounded_before_transfer(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        node.inventory = {"capacity": {"oversized": "x" * 20000}}
    with pytest.raises(SnapshotUnavailable, match="capacity_limit"):
        read(test_settings)


def test_http_monitoring_authentication_and_safe_configuration(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, token = enabled(test_settings)
    app = create_app(settings)
    assert app.state.monitoring.reader
    def forbidden(now: Any) -> Snapshot:
        raise AssertionError("Unauthorized requests must not query the database")
    monkeypatch.setattr(app.state.monitoring.reader, "read", forbidden)
    with TestClient(app, base_url=settings.public_origin, client=("127.0.0.1", 50500)) as client:
        for prefix in ("worker", "gateway", "admin", "sk-gen"):
            wrong = prefix + token.removeprefix("mon")
            assert client.get(METRICS, headers={"Authorization": "Bearer " + wrong}).status_code == 401
        assert client.get(METRICS).status_code == 401
        response = client.get(CONFIGURATION, headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200 and response.json()["credential_slots"] == 1
        assert token not in response.text and hashlib.sha256(token.encode()).hexdigest() not in response.text
        for method, path in (("GET", "/v1/models"), ("GET", "/admin/api/v1/session"), ("GET", "/gateway/v1/catalog"), ("POST", "/internal/v1/claims")):
            assert client.request(method, path, headers={"Authorization": "Bearer " + token}, json={}).status_code == 401
    outside = create_app(settings)
    monkeypatch.setattr(outside.state.monitoring.reader, "read", forbidden)
    with TestClient(outside, base_url=settings.public_origin, client=("192.0.2.19", 50500)) as client:
        assert client.get(METRICS, headers={"Authorization": "Bearer " + token, "X-Forwarded-For": "127.0.0.1"}).status_code == 401
    with TestClient(create_app(test_settings), base_url=settings.public_origin, client=("127.0.0.1", 50500)) as client:
        assert client.get(METRICS, headers={"Authorization": "Bearer " + token}).status_code == 404


def test_real_http_scrape_is_parsable_and_correlates_without_payloads(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, token = enabled(test_settings)
    app = create_app(settings)
    events = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    with TestClient(app, base_url=settings.public_origin, client=("127.0.0.1", 50500)) as client:
        bad = client.get("/v1/models/private-model?secret=private-query", headers={"Authorization": "Bearer private-credential", "X-Request-ID": "private-client-id"})
        scrape = client.get(METRICS, headers={"Authorization": "Bearer " + token})
        assert scrape.status_code == 200 and "version=0.0.4" in scrape.headers["content-type"]
        parsed = samples(scrape.text)
        assert parsed[("render_control_snapshot_success", ())] == 1
        assert parsed[("render_control_gpu_resources", (("status", "registered"),))] == 1
    assert events[0]["request_id"] == bad.headers["x-request-id"]
    serialized = json.dumps(events) + scrape.text
    for private in (token, "private-model", "private-query", "private-credential", "private-client-id", str(catalog["node_id"])):
        assert private not in serialized


def test_unhandled_error_response_is_fully_observed_without_leaking_exception(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(test_settings)
    events = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    @app.get("/health/synthetic-fault")
    async def failure() -> None:
        raise RuntimeError("synthetic-private-exception-payload")
    with TestClient(app, base_url=test_settings.public_origin, client=("127.0.0.1", 50500), raise_server_exceptions=False) as client:
        result = client.get("/health/synthetic-fault?secret=private-query")
    assert result.status_code == 500
    assert len(events) == 1 and events[0]["status"] == 500
    assert events[0]["request_id"] == result.headers["x-request-id"]
    assert events[0]["headers_milliseconds"] is not None
    assert events[0]["response_bytes"] == len(result.content) > 0
    assert events[0]["completion"] == "failed"
    assert "private" not in json.dumps(events) + result.text
