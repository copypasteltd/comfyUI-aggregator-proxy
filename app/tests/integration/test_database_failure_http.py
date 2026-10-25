from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.validation import validate
from generation_domain.values import utcnow
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.models import AdminUser, GatewayService, RateState
from generation_infrastructure.security import issue_secret, password_hash, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def observe_blocker(pg: sessionmaker[Session], blocker: int, request: Future[Any]) -> None:
    deadline = time.monotonic() + 3
    with pg.kw["bind"].connect().execution_options(isolation_level="AUTOCOMMIT") as monitor:
        while time.monotonic() < deadline:
            row = monitor.execute(text("SELECT a.pid,a.wait_event_type,b.state FROM pg_stat_activity a JOIN pg_stat_activity b ON b.pid=:blocker WHERE a.datname=current_database() AND :blocker=ANY(pg_blocking_pids(a.pid))"), {"blocker": blocker}).first()
            if row:
                assert row.pid != blocker and row.wait_event_type == "Lock" and row.state == "idle in transaction"
                return
            assert not request.done(), "The injected wait must be observed before its timeout"
            time.sleep(0.02)
    raise AssertionError("Owned blocker chain was not observed")


def diagnostic(events: list[dict[str, Any]], response: Any, operation: str, state: str) -> None:
    event = next(value for value in events if value["request_id"] == response.headers["x-request-id"])
    validate("database-failure", event["database_failure"])
    assert event["database_failure"]["operation"] == operation
    assert event["database_failure"]["sqlstate"] == state
    assert event["completion"] == "completed"
    assert "database_failure" not in response.text
    assert "PRIVATE" not in json.dumps(events) + response.text


def test_gateway_row_wait_is_observed_sanitized_and_recovers_without_retry(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier, token = issue_secret("gateway")
    with pg.begin() as session:
        service = GatewayService(name="Synthetic diagnostics", issuer="diagnostics", allowed_cidrs=["127.0.0.1/32"], credential_identifier=identifier, credential_verifier=verifier(token, test_settings.credential_pepper.get_secret_value()))
        session.add(service)
        session.flush()
        rate_id = "gateway:" + str(service.id)
        session.add(RateState(identity=rate_id, tokens=100, updated_at=utcnow(), recent=[]))
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    headers = {"Authorization": "Bearer " + token}
    with TestClient(app, base_url=test_settings.public_origin, client=("127.0.0.1", 50101)) as client, ThreadPoolExecutor(max_workers=1) as pool:
        with pg.begin() as holder:
            holder.execute(select(RateState).where(RateState.identity == rate_id).with_for_update())
            blocker = holder.scalar(select(func.pg_backend_pid()))
            assert blocker
            future = pool.submit(client.get, "/gateway/v1/settlements", headers=headers)
            observe_blocker(pg, blocker, future)
            response = future.result(timeout=10)
            assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
            diagnostic(events, response, "gateway_auth", "55P03")
            assert holder.get(RateState, rate_id).tokens == 100
        recovered = client.get("/gateway/v1/settlements", headers=headers)
        assert recovered.status_code == 200
        assert "database_failure" not in events[-1]
    with pg() as session:
        assert session.get(RateState, rate_id).tokens == 99
    assert token not in json.dumps(events)


def test_audit_relation_wait_uses_existing_budget_and_recovers(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    password = "Synthetic-diagnostics-password"
    with pg.begin() as session:
        session.add(AdminUser(username="diagnostics-admin", password_hash=password_hash(password), role="administrator"))
    login = AuthRepository(pg, test_settings).login("diagnostics-admin", password, "127.0.0.1", "diagnostics-login")
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    with TestClient(app, base_url=test_settings.public_origin, client=("127.0.0.1", 50102)) as client, ThreadPoolExecutor(max_workers=1) as pool:
        client.cookies.set("gen_admin", login["token"])
        with pg.begin() as holder:
            holder.execute(text("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE"))
            blocker = holder.scalar(select(func.pg_backend_pid()))
            assert blocker
            future = pool.submit(client.get, "/admin/api/v1/audit-events")
            observe_blocker(pg, blocker, future)
            response = future.result(timeout=5)
            assert response.status_code == 503 and response.json()["error"]["code"] == "audit_query_unavailable"
            diagnostic(events, response, "audit_snapshot", "55P03")
        recovered = client.get("/admin/api/v1/audit-events")
        assert recovered.status_code == 200, events[-1].get("database_failure")
        assert "database_failure" not in events[-1]


def test_real_cancelled_query_is_classified_without_escaping_framework(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    @app.get("/health/synthetic-query-cancel")
    def failure() -> None:
        with app.state.sessions.begin() as session:
            session.execute(text("SET LOCAL statement_timeout='50ms'"))
            session.execute(text("SELECT pg_sleep(0.2)"))
    with TestClient(app, base_url=test_settings.public_origin) as client:
        response = client.get("/health/synthetic-query-cancel")
        assert response.status_code == 500
        diagnostic(events, response, "http_request", "57014")
        assert client.get("/health/ready").status_code == 200
        assert "database_failure" not in events[-1]


@pytest.mark.parametrize("state", ["55P03", "57014", "PRIVATE-STATE", None])
def test_driver_sql_parameters_and_text_never_escape(test_settings: Settings, monkeypatch: pytest.MonkeyPatch, state: str | None) -> None:
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    class DriverError(Exception):
        sqlstate = state
    @app.get("/health/synthetic-private-driver")
    def failure() -> None:
        raise DBAPIError("PRIVATE-SQL", {"token": "PRIVATE-PARAMETER"}, DriverError("PRIVATE-DRIVER"))
    with TestClient(app, base_url=test_settings.public_origin) as client:
        response = client.get("/health/synthetic-private-driver?token=PRIVATE-QUERY")
        assert response.status_code == 500
    assert "PRIVATE" not in response.text + json.dumps(events)
    assert events[0]["database_failure"]["sqlstate"] == (state if state in ("55P03", "57014") else None)


def test_integrity_conflict_keeps_its_original_handler(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    @app.get("/health/synthetic-integrity")
    def failure() -> None:
        raise IntegrityError("PRIVATE-SQL", {}, Exception("PRIVATE-DRIVER"))
    with TestClient(app, base_url=test_settings.public_origin) as client:
        response = client.get("/health/synthetic-integrity")
        assert response.status_code == 409 and response.json()["error"]["code"] == "resource_conflict"
    assert "database_failure" not in events[0]
