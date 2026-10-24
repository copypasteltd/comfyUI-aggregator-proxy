from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from typing import Any

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.errors import DomainError
from generation_domain.values import utcnow
from generation_infrastructure import auth as auth_module
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.models import AdminSession, AdminUser, AuditEvent, OutboxEvent
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-session-password-only"


def test_sse_rechecks_idle_expiry_without_persisting_activity(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    initial = clock[0]
    app = create_app(auth.settings)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/admin/api/v1/events")
    request = Request({"type": "http", "method": "GET", "headers": [(b"cookie", ("gen_admin=" + login["token"]).encode())]})
    async def connected() -> bool:
        return False
    async def advance(_: float) -> None:
        clock[0] += timedelta(seconds=30)
    monkeypatch.setattr(request, "is_disconnected", connected)
    monkeypatch.setattr("control_api.main.asyncio.sleep", advance)
    async def consume() -> None:
        response = await endpoint(request, auth.admin(login["token"], write=False, origin=None, csrf=None))
        values = [value async for value in response.body_iterator]
        assert len(values) == 3
        assert "connected" in values[0] and "refresh" in values[1] and "session_revoked" in values[2]
    asyncio.run(consume())
    with pg() as session:
        assert session.scalar(select(AdminSession)).last_activity_at == initial


def test_waiting_activity_checks_deadline_after_lock(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    entered = Event()
    def renew() -> Any:
        entered.set()
        return activity(auth, login)
    with ThreadPoolExecutor(1) as executor:
        with pg.begin() as session:
            session.scalar(select(AdminSession).with_for_update())
            future = executor.submit(renew)
            assert entered.wait(5)
            clock[0] += timedelta(seconds=60)
        with pytest.raises(DomainError):
            future.result(timeout=5)


def setup(
    pg: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    role: str = "administrator",
) -> tuple[AuthRepository, dict[str, Any], list[Any]]:
    clock = [utcnow()]
    monkeypatch.setattr(auth_module, "utcnow", lambda: clock[0])
    with pg.begin() as session:
        session.add(
            AdminUser(username="session-test", password_hash=password_hash(PASSWORD), role=role)
        )
    auth = AuthRepository(
        pg, settings.model_copy(update={"session_seconds": 120, "session_idle_seconds": 60})
    )
    return auth, auth.login("session-test", PASSWORD, "127.0.0.1", "session-test"), clock


def activity(auth: AuthRepository, login: dict[str, Any]) -> Any:
    return auth.activity(
        login["token"], origin=auth.settings.public_origin, csrf=login["csrf_token"]
    )


@pytest.mark.parametrize("role", ["administrator", "operator", "workflow_maintainer", "observer"])
def test_each_role_renews_only_current_session_without_changing_absolute_expiry(
    pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch, role)
    initial = login["lifetime"]
    clock[0] += timedelta(seconds=35)
    renewed = activity(auth, login)
    assert renewed.absolute_expires_at == initial["absolute_expires_at"]
    assert renewed.idle_expires_at == initial["idle_expires_at"] + 35
    assert auth.admin(login["token"], write=False, origin=None, csrf=None).role == role
    clock[0] += timedelta(seconds=35)
    assert activity(auth, login).idle_expires_at == initial["absolute_expires_at"]


@pytest.mark.parametrize(
    ("absolute", "microseconds", "valid"),
    [(False, -1, True), (False, 0, False), (True, -1, True), (True, 0, False)],
)
def test_exact_idle_and_absolute_cutoffs_are_enforced_without_reviving(
    pg: sessionmaker[Session],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    absolute: bool,
    microseconds: int,
    valid: bool,
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    if absolute:
        with pg.begin() as session:
            session.scalar(select(AdminSession)).last_activity_at = clock[0] + timedelta(seconds=90)
    clock[0] += timedelta(seconds=120 if absolute else 60, microseconds=microseconds)
    with pg() as session:
        before = session.scalar(select(AdminSession)).last_activity_at
    if valid:
        assert activity(auth, login).absolute_expires_at == login["lifetime"]["absolute_expires_at"]
    else:
        with pytest.raises(DomainError, match="管理会话已失效"):
            activity(auth, login)
        with pg() as session:
            assert session.scalar(select(AdminSession)).last_activity_at == before


@pytest.mark.parametrize(("age", "valid"), [(59, True), (60, False), (61, False)])
def test_historical_null_activity_uses_created_time_without_backfill(
    pg: sessionmaker[Session],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    age: int,
    valid: bool,
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    with pg.begin() as session:
        row = session.scalar(select(AdminSession))
        row.created_at = clock[0] - timedelta(seconds=age)
        row.last_activity_at = None
    if valid:
        assert (
            auth.snapshot(login["token"])["lifetime"]["idle_expires_at"]
            <= login["lifetime"]["idle_expires_at"]
        )
    else:
        with pytest.raises(DomainError):
            auth.admin(login["token"], write=False, origin=None, csrf=None)
    with pg() as session:
        assert session.scalar(select(AdminSession)).last_activity_at is None


def test_passive_reads_and_concurrent_activity_do_not_create_audit_noise(
    pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)

    def counts() -> tuple[Any, ...]:
        with pg() as session:
            return (
                session.scalar(select(func.count()).select_from(AuditEvent)),
                session.scalar(select(func.count()).select_from(OutboxEvent)),
            )

    before = counts()
    initial = clock[0]
    clock[0] += timedelta(seconds=30)
    for _ in range(12):
        auth.admin(login["token"], write=False, origin=None, csrf=None)
        assert (
            auth.snapshot(login["token"])["lifetime"]["idle_expires_at"]
            == login["lifetime"]["idle_expires_at"]
        )
    with pg() as session:
        assert session.scalar(select(AdminSession)).last_activity_at == initial
    with ThreadPoolExecutor(6) as executor:
        values = list(executor.map(lambda _: activity(auth, login), range(6)))
    assert len({value.idle_expires_at for value in values}) == 1
    with pg() as session:
        assert session.scalar(select(AdminSession)).last_activity_at == clock[0]
    assert counts() == before


@pytest.mark.parametrize(
    "invalid", ["revoked", "disabled", "revision", "pepper", "future_activity", "forged_token"]
)
def test_activity_rechecks_current_security_state(
    pg: sessionmaker[Session],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    with pg.begin() as session:
        row, user = session.scalar(select(AdminSession)), session.scalar(select(AdminUser))
        if invalid == "revoked":
            row.revoked_at = clock[0]
        if invalid == "disabled":
            user.enabled = False
        if invalid == "revision":
            user.revision += 1
        if invalid == "pepper":
            row.pepper_version += 1
        if invalid == "future_activity":
            row.last_activity_at = clock[0] + timedelta(seconds=1)
    if invalid == "forged_token":
        login["token"] = login["token"][:-1] + ("a" if login["token"][-1] != "a" else "b")
    with pytest.raises(DomainError) as error:
        activity(auth, login)
    assert error.value.status == 401


def test_activity_waiting_on_revocation_cannot_revive_session(
    pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    entered = Event()

    def renew() -> Any:
        entered.set()
        return activity(auth, login)

    with ThreadPoolExecutor(1) as executor:
        with pg.begin() as session:
            row = session.scalar(select(AdminSession).with_for_update())
            row.revoked_at = clock[0]
            session.flush()
            future = executor.submit(renew)
            assert entered.wait(5)
        with pytest.raises(DomainError):
            future.result(timeout=5)
    with pg() as session:
        assert session.scalar(select(AdminSession)).last_activity_at == clock[0]


def test_failed_activity_commit_rolls_back_lifetime(
    pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    initial = clock[0]
    clock[0] += timedelta(seconds=30)

    def fail(session: Session) -> None:
        if any(isinstance(row, AdminSession) for row in session.dirty):
            raise RuntimeError("Synthetic commit failure")

    event.listen(pg.class_, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic commit failure"):
            activity(auth, login)
    finally:
        event.remove(pg.class_, "before_commit", fail)
    with pg() as session:
        assert session.scalar(select(AdminSession)).last_activity_at == initial


@pytest.mark.parametrize(
    ("origin", "csrf", "status"),
    [
        (None, True, 403),
        ("https://wrong.invalid", True, 403),
        ("correct", False, 403),
        ("correct", True, 200),
    ],
)
def test_activity_http_origin_csrf_and_response(
    pg: sessionmaker[Session],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    origin: str | None,
    csrf: bool,
    status: int,
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    with TestClient(create_app(auth.settings), base_url=auth.settings.public_origin) as client:
        client.cookies.set("gen_admin", login["token"], path="/admin")
        headers = (
            {"Origin": auth.settings.public_origin if origin == "correct" else origin}
            if origin
            else {}
        )
        if csrf:
            headers["X-CSRF-Token"] = login["csrf_token"]
        response = client.post(
            "/admin/api/v1/session/activity", json={"activity": "interactive"}, headers=headers
        )
        assert response.status_code == status, response.text
        if status == 200:
            assert response.json() == login["lifetime"]
            assert "set-cookie" not in response.headers
        else:
            assert response.json()["error"]["code"] == "csrf_denied"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"activity": "background"},
        {"activity": "interactive", "server_time": 1},
        {"activity": "interactive", "user_id": "other"},
    ],
)
def test_activity_rejects_client_time_identity_and_background_payload(
    pg: sessionmaker[Session],
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    auth, login, clock = setup(pg, test_settings, monkeypatch)
    with TestClient(create_app(auth.settings), base_url=auth.settings.public_origin) as client:
        client.cookies.set("gen_admin", login["token"], path="/admin")
        response = client.post(
            "/admin/api/v1/session/activity",
            json=payload,
            headers={"Origin": auth.settings.public_origin, "X-CSRF-Token": login["csrf_token"]},
        )
        assert response.status_code == 422


@pytest.mark.parametrize("prefix", ["sk-gen", "worker", "gateway"])
def test_bearer_identities_cannot_renew_admin_session(
    pg: sessionmaker[Session], test_settings: Settings, prefix: str
) -> None:
    from generation_infrastructure.security import issue_secret

    _, token = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        response = client.post(
            "/admin/api/v1/session/activity",
            json={"activity": "interactive"},
            headers={"Authorization": "Bearer " + token, "Origin": test_settings.public_origin},
        )
        assert response.status_code == 401


@pytest.mark.parametrize("field", ["session_seconds", "session_idle_seconds"])
@pytest.mark.parametrize("value", [59, 86401])
def test_session_configuration_rejects_out_of_range(
    test_settings: Settings, field: str, value: int
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings.model_validate({**test_settings.model_dump(), field: value})
