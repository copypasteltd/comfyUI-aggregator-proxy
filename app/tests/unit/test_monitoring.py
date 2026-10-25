from __future__ import annotations

import asyncio
import hashlib
import json
import queue
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from control_api.monitoring import MonitoringMiddleware, RequestLog
from control_api.monitoring_cli import alert_rules, main
from generation_contracts.monitoring import MonitoringConfiguration, MonitoringThresholds
from generation_infrastructure.monitoring import (
    HTTP_BUCKETS,
    HTTP_GROUPS,
    MonitoringService,
    ProcessMetrics,
    monitoring_authorized,
)
from generation_infrastructure.monitoring_snapshot import (
    DATABASE_GAUGES,
    Snapshot,
    SnapshotUnavailable,
)
from generation_infrastructure.settings import Settings
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr, ValidationError
from starlette.requests import ClientDisconnect

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads((ROOT / "docs/contracts/fixtures.json").read_text(encoding="utf-8"))["cases"]


def synthetic_token(prefix: str = "mon", char: str = "a") -> str:
    return prefix + "_" + "1" * 32 + "." + char * 43


def enabled(settings: Settings, *tokens: str) -> Settings:
    return settings.model_copy(update={"monitoring_token_sha256": [SecretStr(hashlib.sha256(t.encode()).hexdigest()) for t in tokens or (synthetic_token(),)]})


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "monitoring-configuration.schema.json"], ids=lambda c: c["id"])
def test_configuration_matches_frozen_schema(case: dict[str, Any]) -> None:
    if case["valid"]:
        assert MonitoringConfiguration.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            MonitoringConfiguration.model_validate(case["data"])


def test_metric_manifest_matches_emitted_dimensions() -> None:
    contract = json.loads((ROOT / "docs/contracts/monitoring-metrics.json").read_text(encoding="utf-8"))
    assert {k: list(v) for k, v in DATABASE_GAUGES.items()} == contract["database_gauges"]
    assert list(HTTP_BUCKETS) == contract["http_histogram_buckets_seconds"]
    assert list(HTTP_GROUPS) == contract["http_operation_groups"]


@pytest.mark.parametrize(("prefix", "peer", "allowed"), [("mon", "127.0.0.1", True), ("mon", "::1", True), ("mon", "::ffff:127.0.0.1", True), ("mon", "192.0.2.1", False), ("mon", "invalid", False), ("mon", None, False), ("worker", "127.0.0.1", False), ("admin", "127.0.0.1", False), ("sk-gen", "127.0.0.1", False), ("gateway", "127.0.0.1", False)])
def test_auth_requires_monitoring_realm_and_actual_peer(test_settings: Settings, prefix: str, peer: str | None, allowed: bool) -> None:
    token = synthetic_token(prefix)
    assert monitoring_authorized(enabled(test_settings, token), "Bearer " + token, peer) is allowed


def test_rotation_disabled_and_bad_bearer(test_settings: Settings) -> None:
    a, b = synthetic_token(), synthetic_token(char="b")
    settings = enabled(test_settings, a, b)
    assert monitoring_authorized(settings, "Bearer " + a, "127.0.0.1")
    assert monitoring_authorized(settings, "bearer " + b, "127.0.0.1")
    assert not monitoring_authorized(enabled(test_settings, b), "Bearer " + a, "127.0.0.1")
    assert not monitoring_authorized(test_settings, "Bearer " + a, "127.0.0.1")
    for value in (a, "Basic " + a, "Bearer " + a + " ", "Bearer " + a[:-1], "Bearer " + a + "\n"):
        assert not monitoring_authorized(settings, value, "127.0.0.1")


@pytest.mark.parametrize("values", [{"monitoring_token_sha256": ["plain-token"]}, {"monitoring_token_sha256": ["0" * 64] * 2}, {"monitoring_token_sha256": ["0" * 64, "1" * 64, "2" * 64]}, {"monitoring_allowed_networks": []}, {"monitoring_allowed_networks": ["https://127.0.0.1"]}, {"monitoring_thresholds": {"queue_age_seconds": True}}, {"monitoring_thresholds": {"unknown": 1}}, {"monitoring_thresholds": {"http_error_ratio": float("nan")}}])
def test_deployment_settings_reject_ambiguous_or_sensitive_values(test_settings: Settings, values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**test_settings.model_dump(), **values})


def test_cli_writes_token_once_without_printing_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "credential"
    monkeypatch.setattr("sys.argv", ["gen-monitoring", "issue-token", "--output", str(target)])
    main()
    token = target.read_text().strip()
    output = capsys.readouterr().out
    assert token.startswith("mon_") and token not in output
    assert json.loads(output) == {"sha256": hashlib.sha256(token.encode()).hexdigest(), "credential_written": True}
    with pytest.raises(SystemExit, match="monitoring_output_exists"):
        main()
    assert target.read_text().strip() == token


def test_alert_rules_use_configured_thresholds_and_deduplicate_global_facts() -> None:
    t = MonitoringThresholds(queue_age_seconds=901, pending_age_seconds=41, settlement_age_seconds=501, node_stale_seconds=61, http_error_ratio=0.09, minimum_requests=101, alert_for_seconds=17)
    rules = alert_rules(t)["groups"][0]["rules"]
    assert len(rules) == len({r["alert"] for r in rules}) == 10
    assert all(r["for"] == "17s" for r in rules)
    expressions = "\n".join(r["expr"] for r in rules)
    for value in ("> 901", "> 41", "> 501", "> 61", "> 0.09", ">= 101"):
        assert value in expressions
    assert "sum by (job) (render_control_jobs" not in expressions
    assert 'status="uncertain"' in expressions and "clamp_min" in expressions


def test_failed_refresh_does_not_export_stale_queue_as_current(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MonitoringService(enabled(test_settings))
    assert service.reader
    try:
        snapshot = Snapshot()
        snapshot.set("jobs_active", 7, "queued", "text_to_image", "customer")
        monkeypatch.setattr(service.reader, "read", lambda now: snapshot)
        first = service.render().decode()
        assert 'render_control_jobs_active{kind="customer",state="queued",task_type="text_to_image"} 7.0' in first
        last_success = service.last_success_at
        def unavailable(now: Any) -> Snapshot:
            raise SnapshotUnavailable("timeout")
        monkeypatch.setattr(service.reader, "read", unavailable)
        service._refreshed = float("-inf")
        failed = service.render().decode()
        assert "render_control_snapshot_success 0.0" in failed
        assert "render_control_jobs_active{" not in failed and service.last_success_at == last_success
        assert service.process.registry.get_sample_value("render_control_snapshot_failures_total", {"reason": "timeout"}) == 1
        assert service.render().decode().count("render_control_snapshot_success 0.0") == 1
    finally:
        service.close()


def test_output_budget_is_enforced(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MonitoringService(test_settings)
    monkeypatch.setattr("generation_infrastructure.monitoring.MAX_EXPOSITION_BYTES", 10)
    with pytest.raises(SnapshotUnavailable, match="exposition_limit"):
        service.render()


def test_streams_remain_incremental_and_observe_headers_separately() -> None:
    async def scenario() -> None:
        metrics = ProcessMetrics()
        log = RequestLog(metrics)
        gate, first_body = asyncio.Event(), asyncio.Event()
        incoming, outgoing = [], []
        async def receive() -> Any:
            incoming.append(len(incoming))
            return {"type": "http.request", "body": b"ab", "more_body": len(incoming) == 1}
        async def send(message: Any) -> None:
            outgoing.append(message)
            if message["type"] == "http.response.body" and message.get("more_body"):
                first_body.set()
                await gate.wait()
        async def app(scope: Any, receive: Any, send: Any) -> None:
            assert (await receive())["body"] == b"ab"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            assert (await receive())["body"] == b"ab"
            await send({"type": "http.response.body", "body": b"last", "more_body": False})
        scope = {"type": "http", "method": "POST", "path": "/v1/jobs/private-id", "query_string": b"token=private-query", "route": SimpleNamespace(path="/v1/jobs/{identifier}")}
        middleware = MonitoringMiddleware(app, metrics, log, frozenset(("/v1/jobs/{identifier}",)))
        task = asyncio.create_task(middleware(scope, receive, send))
        await asyncio.wait_for(first_body.wait(), 2)
        assert len(incoming) == 1 and not task.done()
        assert metrics.registry.get_sample_value("render_control_http_inflight", {"operation": "business_write"}) == 1
        assert metrics.registry.get_sample_value("render_control_http_headers_seconds_count", {"operation": "business_write"}) == 1
        assert metrics.registry.get_sample_value("render_control_http_response_seconds_count", {"operation": "business_write"}) is None
        gate.set()
        await asyncio.wait_for(task, 2)
        assert len(incoming) == 2 and metrics.registry.get_sample_value("render_control_http_inflight", {"operation": "business_write"}) == 0
        event = json.loads(log.queue.get_nowait())
        assert event["route"] == "/v1/jobs/{identifier}" and event["request_bytes"] == 4 and event["response_bytes"] == 9
        assert str(UUID(event["request_id"])) == event["request_id"]
        assert "private" not in json.dumps(event)
        assert metrics.registry.get_sample_value("render_control_http_response_seconds_count", {"operation": "business_write"}) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize(("exception", "completion"), [(asyncio.CancelledError, "disconnected"), (ClientDisconnect, "disconnected"), (RuntimeError, "failed")])
def test_disconnect_and_exception_are_not_swallowed(exception: type[BaseException], completion: str) -> None:
    async def scenario() -> None:
        metrics = ProcessMetrics()
        log = RequestLog(metrics)
        async def app(scope: Any, receive: Any, send: Any) -> None:
            raise exception()
        async def unused(*args: Any) -> Any:
            raise AssertionError("Must not buffer or synthesize traffic")
        middleware = MonitoringMiddleware(app, metrics, log, frozenset())
        with pytest.raises(exception):
            await middleware({"type": "http", "method": "GET", "path": "/v1/jobs/private"}, unused, unused)
        event = json.loads(log.queue.get_nowait())
        assert event["completion"] == completion and event["route"] == "<unmatched>"
        assert metrics.registry.get_sample_value("render_control_http_inflight", {"operation": "business_read"}) == 0
    asyncio.run(scenario())


def test_log_queue_is_bounded_and_drops_are_observable() -> None:
    metrics = ProcessMetrics()
    log = RequestLog(metrics)
    for _ in range(1030):
        log.emit({"event": "synthetic"})
    assert log.queue.qsize() == 1024
    assert metrics.registry.get_sample_value("render_control_http_log_dropped_total") == 6
    assert isinstance(log.queue, queue.Queue)


def test_unrecognized_routes_cannot_create_metric_series_from_client_data() -> None:
    async def scenario() -> None:
        metrics = ProcessMetrics()
        log = RequestLog(metrics)
        async def app(scope: Any, receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        async def send(message: Any) -> None:
            pass
        async def receive() -> Any:
            raise AssertionError("Do not consume unused bodies")
        middleware = MonitoringMiddleware(app, metrics, log, frozenset())
        for i in range(150):
            await middleware({"type": "http", "method": f"PRIVATE-{i}", "path": f"/v1/private-{i}", "query_string": f"secret={i}".encode()}, receive, send)
        from prometheus_client import generate_latest
        payload = generate_latest(metrics.registry).decode()
        families = list(text_string_to_metric_families(payload))
        counters = [s for f in families for s in f.samples if s.name == "render_control_http_requests_total"]
        assert len(counters) == 1 and counters[0].value == 150
        assert "PRIVATE" not in payload and "private-" not in payload and "secret=" not in payload
        assert all(json.loads(log.queue.get_nowait())["route"] == "<unmatched>" for _ in range(150))
    asyncio.run(scenario())


def test_cli_explicit_configuration_and_rules_need_no_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from cryptography.fernet import Fernet
    config = tmp_path / "external.env"
    config.write_text("\n".join(("GEN_ENVIRONMENT=test", "GEN_DATABASE_URL=postgresql+psycopg://unavailable.invalid/render", "GEN_CREDENTIAL_PEPPER=" + "p"*43, "GEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode(), "GEN_PUBLIC_ORIGIN=https://127.0.0.1:8443", "GEN_STORAGE_BACKEND=local_development", 'GEN_MONITORING_THRESHOLDS={"queue_age_seconds":901}')), encoding="utf-8")
    def no_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Offline configuration must not connect")
    monkeypatch.setattr("psycopg.connect", no_connection)
    monkeypatch.setattr("sys.argv", ["gen-monitoring", "configuration", "--env-file", str(config)])
    main()
    assert json.loads(capsys.readouterr().out)["thresholds"]["queue_age_seconds"] == 901
    target = tmp_path / "rules.json"
    monkeypatch.setattr("sys.argv", ["gen-monitoring", "rules", "--env-file", str(config), "--output", str(target)])
    main()
    assert json.loads(capsys.readouterr().out)["alerts"] == 10
    assert "> 901" in target.read_text()


def test_cli_invalid_configuration_never_formats_private_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "invalid.env"
    config.write_text("GEN_DATABASE_URL=synthetic-private-configuration-value\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["gen-monitoring", "configuration", "--env-file", str(config)])
    with pytest.raises(SystemExit) as failure:
        main()
    assert str(failure.value) == "monitoring_configuration_invalid"
    assert "synthetic-private" not in capsys.readouterr().out
