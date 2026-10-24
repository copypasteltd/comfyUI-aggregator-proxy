from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.audit_queries import AuditQuery
from generation_contracts.model_artifacts import ModelArtifactCreate, ModelArtifactPatch
from generation_contracts.responses import AuditSnapshot
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id
from generation_infrastructure.admin_audit import AuditQueries
from generation_infrastructure.admin_model_artifacts import ModelArtifactAdministration
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.models import (
    AdminMutation,
    AdminUser,
    AuditEvent,
    ModelArtifact,
    OutboxEvent,
)
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
PASSWORD = "Synthetic-audit-test-password"


def actor(pg: sessionmaker[Session], settings: Settings, role: str = "administrator") -> tuple[AdminIdentity, dict[str, Any]]:
    with pg.begin() as session:
        session.add(AdminUser(username="audit-" + role, password_hash=password_hash(PASSWORD), role=role))
    repository = AuthRepository(pg, settings)
    login = repository.login("audit-" + role, PASSWORD, "127.0.0.1", "audit-login")
    return repository.admin(login["token"], write=False, origin=None, csrf=None), login


def seed(session: Session, **values: Any) -> AuditEvent:
    row = AuditEvent(**{"actor": "system", "action": "channel.updated", "resource": str(UUID(int=7)), "request_id": "audit-request", "created_at": NOW, "detail": {}, **values})
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("generation_infrastructure.admin_audit.utcnow", lambda: NOW + timedelta(seconds=10))


def test_intersection_half_open_boundaries_and_resource_public_id(pg: sessionmaker[Session], test_settings: Settings, clock: None) -> None:
    with pg.begin() as session:
        included = seed(session, detail={"revision": 3})
        seed(session, actor="runner")
        seed(session, action="key.created")
        seed(session, request_id="other")
        seed(session, resource=str(UUID(int=8)))
        seed(session, created_at=NOW - timedelta(seconds=1))
        seed(session, created_at=NOW + timedelta(seconds=1))
    query = AuditQuery(actor="system", action="channel.updated", resource=public_id("ch", UUID(int=7)), request_id="audit-request", created_from=int(NOW.timestamp()), created_before=int(NOW.timestamp()) + 1)
    result = AuditSnapshot.model_validate(AuditQueries(pg, test_settings).snapshot(query))
    assert result.total == 1 and result.data[0].id == public_id("audit", included.id)
    assert result.data[0].revision_after == 3 and result.data[0].revision_before is None
    assert result.snapshot_at == int(NOW.timestamp()) + 10 and result.filters == query.filters()


def test_same_timestamp_pages_stable_and_bounded(pg: sessionmaker[Session], test_settings: Settings, clock: None) -> None:
    with pg.begin() as session:
        for number in range(103):
            seed(session, id=UUID(int=number + 1))
    service = AuditQueries(pg, test_settings)
    pages = [service.snapshot(AuditQuery(page=number)) for number in (1, 2, 3, 10000)]
    assert [len(page["data"]) for page in pages] == [50, 50, 3, 0]
    assert [page["has_more"] for page in pages] == [True, True, False, False]
    assert all(page["total"] == 103 for page in pages)
    assert [parse_id("audit", row["id"]).int for page in pages[:3] for row in page["data"]] == list(range(103, 0, -1))


def test_safe_sql_projection_names_history_and_operation_links(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    with pg.begin() as session:
        user = AdminUser(username="Current audit name", password_hash="unused", role="observer")
        session.add(user)
        session.flush()
        seed(session, actor=public_id("usr", user.id), detail={"revision_before": 1, "revision_after": 2, "operation_id": public_id("op", UUID(int=4)), "authorization": "PRIVATE-SENTINEL", "workflow": {"prompt": "PRIVATE-SENTINEL"}})
        seed(session, actor=public_id("key", catalog["identity"].key_id))
        seed(session, actor=public_id("node", catalog["node_id"]))
        oversized = seed(session, detail={"count": 5, "secret": "PRIVATE-SENTINEL" * 2000})
        unknown = seed(session, actor="usr_" + "f" * 32)
        derived = seed(session, action="operation.probe_channel")
    result = AuditQueries(pg, test_settings).snapshot(AuditQuery())
    by_id = {row["id"]: row for row in result["data"]}
    user_row = next(row for row in result["data"] if row["actor_name"] == "Current audit name")
    assert (user_row["revision_before"], user_row["revision_after"]) == (1, 2)
    assert user_row["detail_redacted"] and "PRIVATE-SENTINEL" not in str(result)
    assert by_id[public_id("audit", oversized.id)]["detail"] == {}
    assert by_id[public_id("audit", oversized.id)]["detail_redacted"]
    assert by_id[public_id("audit", unknown.id)]["actor_name"] is None
    assert by_id[public_id("audit", derived.id)]["operation_id"] == public_id("op", UUID(int=7))
    assert by_id[public_id("audit", unknown.id)]["revision_after"] is None


def test_snapshot_is_read_only_and_count_excludes_concurrent_insert(pg: sessionmaker[Session], test_settings: Settings, clock: None) -> None:
    with pg.begin() as session:
        seed(session)
    engine = pg.kw["bind"]
    statements: list[str] = []
    inserted = False
    def observe(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        nonlocal inserted
        statements.append(statement)
        if "count(*)" in statement and "audit_events" in statement and not inserted:
            inserted = True
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar() == "on"
            assert connection.exec_driver_sql("SHOW transaction_isolation").scalar() == "repeatable read"
            with pg.begin() as concurrent:
                seed(concurrent, request_id="concurrent")
    event.listen(engine, "after_cursor_execute", observe)
    try:
        result = AuditQueries(pg, test_settings).snapshot(AuditQuery())
    finally:
        event.remove(engine, "after_cursor_execute", observe)
    assert result["total"] == len(result["data"]) == 1
    assert "CASE WHEN" in next(sql for sql in statements if "bounded_detail" in sql)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_audit_query_database_failure_is_sanitized(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise DBAPIError("secret-SQL", {}, Exception("PRIVATE-SENTINEL"))
    monkeypatch.setattr(AuditQueries, "_snapshot", fail)
    with pytest.raises(DomainError) as rejected:
        AuditQueries(pg, test_settings).snapshot(AuditQuery())
    assert rejected.value.status == 503 and "PRIVATE" not in str(rejected.value)


@pytest.mark.parametrize("role", ["administrator", "observer", "operator"])
def test_http_identity_permission_and_query_shape(pg: sessionmaker[Session], test_settings: Settings, role: str) -> None:
    _, login = actor(pg, test_settings, role)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get("/admin/api/v1/audit-events").status_code == 401
        client.cookies.set("gen_admin", login["token"])
        response = client.get("/admin/api/v1/audit-events")
        # Audit visibility is controlled by the frozen role map.
        from generation_contracts.admin import ROLE_PERMISSIONS
        allowed = "audit:read" in ROLE_PERMISSIONS[role]
        assert response.status_code == (200 if allowed else 403)
        if allowed:
            assert "total" in response.json() and "snapshot_at" in response.json()
            for query in ("page=true", "page=1.5", "page=10001", "tenant_id=self", "created_from=2&created_before=1", "actor=system%0A"):
                assert client.get("/admin/api/v1/audit-events?" + query).status_code == 422
            assert client.get("/admin/api/v1/audit-events?page=2&created_from=0").status_code == 200
        client.cookies.clear()
        assert client.get("/admin/api/v1/audit-events", headers={"Authorization": "Bearer sk-private-synthetic"}).status_code == 401


def test_revision_replay_and_audit_outbox_entity_atomic_rollback(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity, _ = actor(pg, test_settings)
    service = ModelArtifactAdministration(pg, test_settings)
    request = ModelArtifactCreate(tenant_id=public_id("ten", catalog["identity"].tenant_id), project_id=public_id("prj", catalog["identity"].project_id), name="Synthetic audit model", kind="checkpoint", sha256="d" * 64, bytes=100, relative_path="checkpoints/audit.safetensors")
    created = service.create(identity, request, "audit-create", "audit-write")
    assert service.create(identity, request, "audit-create", "audit-write-replay") == created
    identifier = parse_id("ma", created["id"])
    patch = ModelArtifactPatch(expected_revision=created["revision"], name="Audit updated")
    updated = service.patch(identity, identifier, patch, "audit-update", "audit-write")
    assert service.patch(identity, identifier, patch, "audit-update", "audit-write-replay") == updated
    rows = AuditQueries(pg, test_settings).snapshot(AuditQuery(resource=created["id"]))["data"]
    assert [(row["revision_before"], row["revision_after"]) for row in rows] == [(1, 2), (None, 1)]
    with pg() as session:
        before = [session.scalar(select(func.count()).select_from(model)) for model in (AuditEvent, OutboxEvent, AdminMutation)]
    def reject_audit(mapper: Any, connection: Any, target: AuditEvent) -> None:
        raise IntegrityError("synthetic-audit-write-refused", {}, Exception("owned failure"))
    event.listen(AuditEvent, "before_insert", reject_audit)
    try:
        with pytest.raises(IntegrityError):
            service.patch(identity, identifier, ModelArtifactPatch(expected_revision=2, enabled=False), "audit-failed-update", "audit-write")
    finally:
        event.remove(AuditEvent, "before_insert", reject_audit)
    with pg() as session:
        row = session.get(ModelArtifact, identifier)
        assert row and row.revision == 2 and row.enabled and row.name == "Audit updated"
        assert [session.scalar(select(func.count()).select_from(model)) for model in (AuditEvent, OutboxEvent, AdminMutation)] == before
