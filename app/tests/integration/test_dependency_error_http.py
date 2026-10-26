from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.values import utcnow
from generation_infrastructure.database_diagnostics import REASONS
from generation_infrastructure.models import RateState
from generation_infrastructure.settings import Settings
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
POLICY = json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/dependency-error-policy.json").read_text(encoding="utf-8"))
CASES = [
    *[(state, False, 503) for state in POLICY["unavailable_sqlstates"]],
    *[(state, invalidated, 500) for state in POLICY["non_dependency_sqlstates"] for invalidated in (False, True)],
    (None, True, 503), ("PRIVATE-STATE", True, 503), ("broken_property", True, 503),
    (None, False, 500), ("PRIVATE-STATE", False, 500), ("broken_property", False, 500),
    (None, 1, 500), (None, "true", 500), (["55P03"], False, 500), ({"sqlstate": "55P03"}, False, 500),
    ("55p03", False, 500), ("57014\nPRIVATE", False, 500),
]


def driver_failure(state: Any, invalidated: Any = False) -> DBAPIError:
    class DriverError(Exception):
        @property
        def sqlstate(self) -> Any:
            if state == "broken_property":
                raise ValueError("PRIVATE-METADATA")
            return state

        def __str__(self) -> str:
            raise AssertionError("Driver error text must never be formatted")

    return DBAPIError("PRIVATE-SQL", {"secret": "PRIVATE-PARAMETER"}, DriverError(), connection_invalidated=invalidated)


def assert_unavailable(response: Any) -> None:
    assert response.status_code == POLICY["control_api"]["status"]
    assert response.json() == {"error": {"code": POLICY["control_api"]["code"], "message": POLICY["control_api"]["message"], "request_id": response.headers["x-request-id"]}}
    for name, value in POLICY["headers"].items():
        assert response.headers[name] == value


@pytest.mark.parametrize("state,invalidated,status", CASES)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_dependency_errors_are_safe_and_do_not_retry_the_request(test_settings: Settings, monkeypatch: pytest.MonkeyPatch, state: Any, invalidated: Any, status: int, method: str) -> None:
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    calls = 0

    @app.api_route("/health/synthetic-dependency", methods=["GET", "POST"])
    def failure() -> None:
        nonlocal calls
        calls += 1
        raise driver_failure(state, invalidated)

    with TestClient(app, base_url=test_settings.public_origin) as client:
        response = client.request(method, "/health/synthetic-dependency")
    assert response.status_code == status and calls == 1
    if status == 503:
        assert_unavailable(response)
    else:
        assert response.json()["error"]["code"] == "internal_error"
        assert "retry-after" not in response.headers
    assert "PRIVATE" not in response.text + json.dumps(events)
    assert events[0]["request_id"] == response.headers["x-request-id"]
    assert events[0]["status"] == response.status_code


def test_dependency_policy_matches_runtime_and_provider_fixture() -> None:
    root = Path(__file__).resolve().parents[3]
    expected = (root / "docs/contracts/dependency-error-policy.json").read_bytes()
    assert files("generation_contracts").joinpath("schemas", "dependency-error-policy.json").read_bytes() == expected
    assert (root / "app/integrations/sub2api/overlay/backend/internal/rendercontrol/testdata/dependency-error-policy.json").read_bytes() == expected
    unavailable, permanent = set(POLICY["unavailable_sqlstates"]), set(POLICY["non_dependency_sqlstates"])
    assert unavailable.isdisjoint(permanent) and unavailable | permanent == set(REASONS)


def test_real_connection_pool_exhaustion_returns_503_and_recovers(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    engine = create_engine(test_settings.database_url.get_secret_value(), pool_size=1, max_overflow=0, pool_timeout=0.05)

    @app.get("/health/synthetic-pool")
    def query() -> dict[str, bool]:
        with engine.connect() as connection:
            return {"ready": connection.scalar(text("SELECT 1")) == 1}

    try:
        with TestClient(app, base_url=test_settings.public_origin) as client:
            with engine.connect() as held:
                assert held.scalar(text("SELECT current_database()")) == "render_control_test"
                response = client.get("/health/synthetic-pool")
                assert_unavailable(response)
                assert "database_failure" not in events[-1]
            recovered = client.get("/health/synthetic-pool")
            assert recovered.status_code == 200 and recovered.json() == {"ready": True}
    finally:
        engine.dispose()
    assert "PRIVATE" not in json.dumps(events)


def test_503_after_actual_commit_does_not_promise_rollback_or_retry(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(test_settings)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(app.state.request_log, "emit", events.append)
    calls = 0
    identity = "synthetic:dependency-commit"

    @app.post("/health/synthetic-committed-write")
    def write_then_lose_receipt() -> None:
        nonlocal calls
        calls += 1
        with pg.begin() as session:
            session.add(RateState(identity=identity, tokens=100, updated_at=utcnow(), recent=[]))
        raise driver_failure("08007", True)

    with TestClient(app, base_url=test_settings.public_origin) as client:
        response = client.post("/health/synthetic-committed-write")
        assert_unavailable(response)
    with pg() as session:
        row = session.get(RateState, identity)
        assert row is not None and row.tokens == 100
    assert calls == 1 and "PRIVATE" not in response.text + json.dumps(events)
