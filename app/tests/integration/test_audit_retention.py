from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.errors import DomainError
from generation_infrastructure import audit_retention as module
from generation_infrastructure.audit_retention import (
    ACTION,
    LOCK,
    prune_audit_events,
    retention_snapshot,
)
from generation_infrastructure.models import AdminUser, AuditEvent, Base, OutboxEvent
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from pydantic import ValidationError
from sqlalchemy import event, func, literal, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
PASSWORD = "Synthetic-retention-password-only"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    original = module.func

    class Functions:
        def clock_timestamp(self) -> Any:
            return literal(NOW)

        def __getattr__(self, name: str) -> Any:
            return getattr(original, name)

    monkeypatch.setattr(module, "func", Functions())


def seed(pg: sessionmaker[Session], count: int = 1, created_at: datetime | None = None) -> None:
    with pg.begin() as session:
        session.add_all(
            AuditEvent(
                actor="synthetic",
                action="synthetic.created",
                resource=str(uuid4()),
                request_id="synthetic-retention",
                created_at=created_at or NOW - timedelta(days=181),
                detail={"private": "PRIVATE-RETENTION-SENTINEL"},
            )
            for _ in range(count)
        )


def unrelated(pg: sessionmaker[Session]) -> dict[str, list[Any]]:
    with pg() as session:
        result = {}
        for table in Base.metadata.sorted_tables:
            if table.name in {"audit_events", "outbox_events"}:
                continue
            result[table.name] = [
                tuple(row)
                for row in session.execute(select(table).order_by(*table.primary_key.columns))
            ]
        return result


def test_disabled_pruning_never_opens_a_database_transaction() -> None:
    def unavailable() -> Any:
        raise AssertionError("Disabled retention must not access sessions")

    assert prune_audit_events(unavailable) == 0


def test_cutoff_is_strict_and_future_recent_rows_survive(
    pg: sessionmaker[Session], clock: None
) -> None:
    seed(pg, created_at=NOW - timedelta(days=180, microseconds=1))
    for time in (NOW - timedelta(days=180), NOW, NOW + timedelta(days=1)):
        seed(pg, created_at=time)
    assert prune_audit_events(pg, 180) == 1
    with pg() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.actor == "synthetic")
            )
            == 3
        )
        summary = session.scalar(select(AuditEvent).where(AuditEvent.action == ACTION))
        assert summary.actor == "runner" and summary.detail == {
            "deleted": 1,
            "cutoff": int((NOW - timedelta(days=180)).timestamp()),
            "retention_days": 180,
        }
        assert "PRIVATE" not in str(summary.detail)
        outbox = session.scalar(select(OutboxEvent))
        assert outbox.aggregate_id.hex == summary.resource.replace("-", "") and outbox.payload == {
            "reason": ACTION
        }
    assert prune_audit_events(pg, 180) == 0


@pytest.mark.parametrize("plan", ["default", "nested_loop"])
def test_each_batch_is_bounded_and_restart_only_deletes_remaining_rows(
    pg: sessionmaker[Session], clock: None, plan: str
) -> None:
    seed(pg, 1003)

    def planner(session: Session, transaction: Any, connection: Any) -> None:
        if plan == "nested_loop":
            from sqlalchemy import text

            for setting in (
                "enable_hashjoin", "enable_mergejoin", "enable_material",
                "enable_hashagg", "enable_sort",
            ):
                connection.execute(text(f"SET LOCAL {setting} = off"))

    event.listen(pg.class_, "after_begin", planner)
    try:
        assert [prune_audit_events(pg, 180) for _ in range(4)] == [500, 500, 3, 0]
    finally:
        event.remove(pg.class_, "after_begin", planner)
    with pg() as session:
        assert sum(row.detail["deleted"] for row in session.scalars(select(AuditEvent))) == 1003
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 3


def test_parallel_runners_do_not_double_count_and_row_locks_are_skipped(
    pg: sessionmaker[Session], clock: None
) -> None:
    seed(pg, 501)
    with pg.begin() as held:
        locked = held.scalar(select(AuditEvent).order_by(AuditEvent.id).limit(1).with_for_update())
        with ThreadPoolExecutor(4) as executor:
            values = list(executor.map(lambda _: prune_audit_events(pg, 180), range(4)))
        assert sum(values) == 500
        assert held.get(AuditEvent, locked.id) is not None
    assert prune_audit_events(pg, 180) == 1
    with pg() as session:
        assert sum(row.detail["deleted"] for row in session.scalars(select(AuditEvent))) == 501


def test_global_cleanup_lock_returns_without_deleting(
    pg: sessionmaker[Session], clock: None
) -> None:
    seed(pg)
    with pg.begin() as held:
        held.execute(select(func.pg_advisory_xact_lock(LOCK)))
        assert prune_audit_events(pg, 180) == 0
    assert prune_audit_events(pg, 180) == 1


def test_table_lock_timeout_rolls_back_without_a_cleanup_record(pg: sessionmaker[Session], clock: None) -> None:
    from sqlalchemy import text
    seed(pg)
    with pg.begin() as held:
        held.execute(text("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(DBAPIError) as error:
            prune_audit_events(pg, 180)
        assert error.value.orig.sqlstate == "55P03"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_commit_failure_rolls_back_delete_summary_and_outbox(
    pg: sessionmaker[Session], clock: None
) -> None:
    seed(pg)

    def fail(session: Session) -> None:
        if any(isinstance(row, AuditEvent) and row.action == ACTION for row in session.new):
            raise RuntimeError("Synthetic retention commit failure")

    event.listen(pg.class_, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic retention commit failure"):
            prune_audit_events(pg, 180)
    finally:
        event.remove(pg.class_, "before_commit", fail)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_retention_leaves_tasks_credentials_quota_and_execution_facts_unchanged(
    pg: sessionmaker[Session], catalog: dict[str, Any], clock: None
) -> None:
    seed(pg)
    before = unrelated(pg)
    assert prune_audit_events(pg, 180) == 1
    assert unrelated(pg) == before


def test_snapshot_is_read_only_and_keeps_historical_policy_after_disabling(
    pg: sessionmaker[Session], clock: None
) -> None:
    seed(pg)
    configured = retention_snapshot(pg, 180)
    assert configured.enabled and configured.last_batch is None
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
    assert prune_audit_events(pg, 180) == 1
    disabled = retention_snapshot(pg, None)
    assert (
        not disabled.enabled
        and disabled.retention_days is None
        and disabled.last_batch.deleted == 1
        and disabled.last_batch.retention_days == 180
    )
    assert retention_snapshot(pg, 365).last_batch == disabled.last_batch


@pytest.mark.parametrize(
    "detail",
    [
        {"deleted": 0, "cutoff": 1, "retention_days": 180},
        {"deleted": 1, "cutoff": 1, "retention_days": 180, "body": "PRIVATE"},
        {"oversized": "PRIVATE" * 500},
    ],
)
def test_corrupt_summary_fails_closed_without_echoing_body(
    pg: sessionmaker[Session], clock: None, detail: dict[str, Any]
) -> None:
    with pg.begin() as session:
        session.add(
            AuditEvent(
                actor="runner",
                action=ACTION,
                resource=str(uuid4()),
                request_id="synthetic",
                detail=detail,
            )
        )
    with pytest.raises(DomainError) as failure:
        retention_snapshot(pg, 180)
    assert failure.value.status == 503 and "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    ("role", "status"),
    [("administrator", 200), ("operator", 403), ("workflow_maintainer", 403), ("observer", 403)],
)
def test_status_http_role_guard_and_no_query_side_effect(
    pg: sessionmaker[Session], test_settings: Settings, role: str, status: int
) -> None:
    seed(pg)
    with pg.begin() as session:
        session.add(
            AdminUser(username="retention-test", password_hash=password_hash(PASSWORD), role=role)
        )
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        login = client.post(
            "/admin/api/v1/session",
            headers={"Origin": test_settings.public_origin},
            json={"username": "retention-test", "password": PASSWORD},
        )
        assert login.status_code == 200
        response = client.get("/admin/api/v1/audit-retention")
        assert response.status_code == status, response.text
        if status == 200:
            assert response.json()["enabled"] is False and response.json()["retention_days"] is None
        assert client.post("/admin/api/v1/audit-retention").status_code == 405
    with pg() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.actor == "synthetic")
            )
            == 1
        )


@pytest.mark.parametrize("prefix", ["sk-gen", "worker", "gateway"])
def test_bearer_identities_cannot_read_retention(
    pg: sessionmaker[Session], test_settings: Settings, prefix: str
) -> None:
    from generation_infrastructure.security import issue_secret

    _, token = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert (
            client.get(
                "/admin/api/v1/audit-retention", headers={"Authorization": "Bearer " + token}
            ).status_code
            == 401
        )


@pytest.mark.parametrize("value", [0, 3651, True, 180.0, "180.5"])
def test_retention_configuration_rejects_ambiguous_or_out_of_range(
    test_settings: Settings, value: Any
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**test_settings.model_dump(), "audit_retention_days": value})


@pytest.mark.parametrize("value", [None, 1, 180, 3650, "180"])
def test_explicit_retention_configuration(test_settings: Settings, value: Any) -> None:
    settings = Settings.model_validate(
        {**test_settings.model_dump(), "audit_retention_days": value}
    )
    assert settings.audit_retention_days == (int(value) if value is not None else None)
