from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from generation_contracts.audit_queries import AuditQuery
from generation_infrastructure import admin_audit
from generation_infrastructure.admin_audit import AuditQueries
from generation_infrastructure.models import AuditEvent
from generation_infrastructure.settings import Settings
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("page", [1, 2, 4, 10000])
def test_detail_conversion_is_bounded_by_selected_page(
    pg: sessionmaker[Session], test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch, page: int,
) -> None:
    """Count actual PostgreSQL expression calls, including discarded rows."""
    with pg.begin() as session:
        session.add_all([
            AuditEvent(
                id=UUID(int=number + 1), actor="system", action="channel.updated",
                resource=str(UUID(int=7)), request_id="projection-test",
                created_at=datetime(2026, 10, 24, tzinfo=UTC), detail={"count": number},
            )
            for number in range(153)
        ])
    engine = pg.kw["bind"]
    with engine.connect() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION pg_temp.audit_detail_probe(value text) RETURNS integer
            LANGUAGE plpgsql VOLATILE AS $$
            BEGIN
                PERFORM set_config('render_test.audit_detail_calls',
                    (current_setting('render_test.audit_detail_calls')::integer + 1)::text,
                    false);
                RETURN pg_catalog.octet_length(value);
            END $$
        """)
        connection.exec_driver_sql("SELECT set_config('render_test.audit_detail_calls', '0', false)")
        connection.commit()
        functions = SimpleNamespace(count=func.count, octet_length=func.pg_temp.audit_detail_probe)
        monkeypatch.setattr(admin_audit, "func", functions)
        sessions = sessionmaker(connection, expire_on_commit=False)
        try:
            result: dict[str, Any] = AuditQueries(sessions, test_settings).snapshot(AuditQuery(page=page))
            calls = int(connection.exec_driver_sql("SELECT current_setting('render_test.audit_detail_calls')").scalar_one())
            selected = min(51, max(0, 153 - (page - 1) * 50))
            assert calls <= selected * 2, (page, calls, selected)
            assert result["total"] == 153
            assert len(result["data"]) == min(50, selected)
            assert result["has_more"] == (selected == 51)
            expected = list(range(153 - (page - 1) * 50, max(0, 153 - page * 50), -1))
            assert [row["detail"]["count"] + 1 for row in result["data"]] == expected
        finally:
            connection.rollback()
            connection.exec_driver_sql("DROP FUNCTION pg_temp.audit_detail_probe(text)")
            connection.commit()
