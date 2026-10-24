from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.operational import QueueStatistics, QueueStatisticsQuery
from generation_domain.errors import DomainError
from generation_domain.values import public_id
from generation_infrastructure.admin_jobs import JobAdministration
from generation_infrastructure.models import (
    AdminUser,
    Attempt,
    AuditEvent,
    GpuOccupancy,
    Job,
    OutboxEvent,
    QuotaBucket,
    WorkflowRelease,
)
from generation_infrastructure.operational import OperationalQueries
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
PASSWORD = "Synthetic-statistics-test-password"


def seed(session: Session, catalog: dict[str, Any], **values: Any) -> Job:
    owner = catalog["identity"]
    data = dict(tenant_id=owner.tenant_id, project_id=owner.project_id, key_id=owner.key_id, release_id=catalog["release_id"], model_alias="history-image", task_type="text_to_image", state="queued", request={"input": {"prompt": "PRIVATE STATISTICS PROMPT"}}, execution={}, request_fingerprint="a" * 64, execution_fingerprint="b" * 64, eligible_channels=[public_id("ch", identifier) for identifier in catalog["channel_ids"]], accepted_at=NOW - timedelta(minutes=10), created_at=NOW - timedelta(minutes=10), deadline=NOW + timedelta(days=1), accepted_period="2026-10-24")
    session.add(job := Job(**{**data, **values}))
    session.flush()
    return job


def assign(session: Session, job: Job, catalog: dict[str, Any], fence: int, created: datetime, *, current: bool = True) -> Attempt:
    attempt = Attempt(job_id=job.id, channel_id=catalog["channel_ids"][0], channel_snapshot={}, node_id=catalog["node_id"], boot_id=catalog["boot_id"], gpu_id=catalog["gpu_id"], fence=fence, lease_expires_at=NOW + timedelta(minutes=1), created_at=created)
    session.add(attempt)
    session.flush()
    if current:
        job.current_attempt_id = attempt.id
    return attempt


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("generation_infrastructure.operational.utcnow", lambda: NOW + timedelta(microseconds=999999))
    monkeypatch.setattr("generation_infrastructure.admin_jobs.utcnow", lambda: NOW)


@pytest.mark.parametrize(("window", "buckets", "width"), [("1h", 12, 300), ("24h", 24, 3600), ("7d", 28, 21600)])
def test_empty_statistics_have_fixed_bounds_and_unknown_rates(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None, window: str, buckets: int, width: int) -> None:
    result = QueueStatistics.model_validate(OperationalQueries(pg, test_settings).statistics(QueueStatisticsQuery.model_validate({"window": window, "page": 10000})))
    assert len(result.buckets) == buckets and result.bucket_seconds == width
    assert result.snapshot_at == int(NOW.timestamp()) and result.window_end == result.snapshot_at
    assert result.totals.completed == result.totals.accepted == 0
    assert result.success_rate_bps is None and result.successful_duration.p50_ms is None and result.successful_duration.p95_ms is None
    assert result.filters == {} and len(result.task_types) == 7 and len(result.failure_classes) == 6


def test_half_open_windows_and_different_accepted_terminal_cohorts(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    start = NOW - timedelta(hours=1)
    with pg.begin() as session:
        seed(session, catalog, state="succeeded", accepted_at=start - timedelta(seconds=10), terminal_at=start)
        seed(session, catalog, accepted_at=start)
        seed(session, catalog, state="succeeded", accepted_at=NOW, terminal_at=NOW)
        seed(session, catalog, state="failed", accepted_at=start - timedelta(seconds=2), terminal_at=start - timedelta(microseconds=1))
        seed(session, catalog, state="admission_pending", accepted_at=None)
        seed(session, catalog, state="succeeded", terminal_at=NOW - timedelta(microseconds=1), task_type="text_to_audio", is_validation=True)
    service = OperationalQueries(pg, test_settings)
    result = QueueStatistics.model_validate(service.statistics(QueueStatisticsQuery(window="1h")))
    assert result.totals.accepted == 1 and result.totals.succeeded == result.totals.completed == 1
    assert result.buckets[0].succeeded == 1 and result.successful_duration.p50_ms == 10000
    assert result.success_rate_bps == 10000
    included = QueueStatistics.model_validate(service.statistics(QueueStatisticsQuery(window="1h", include_validation=True)))
    assert included.totals.succeeded == included.totals.accepted == 2 and included.buckets[-1].succeeded == 1
    audio = service.statistics(QueueStatisticsQuery(window="1h", include_validation=True, task_type="text_to_audio"))
    assert audio["totals"]["succeeded"] == 1 and audio["task_types"][-2]["task_type"] == "text_to_audio"


def test_failure_categories_types_and_discrete_duration_percentiles(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    with pg.begin() as session:
        for seconds in (0, 1, 2, 10, 100):
            seed(session, catalog, state="succeeded", accepted_at=NOW - timedelta(seconds=seconds + 1), terminal_at=NOW - timedelta(seconds=1), task_type="text_to_audio")
        for accepted in (None, NOW):
            seed(session, catalog, state="succeeded", accepted_at=accepted, terminal_at=NOW - timedelta(seconds=1))
        for code in ("comfy_oom", "comfy_output_invalid", "preparation_failed", "preparation_transport_failure", "comfy_execution_failed", "private-unknown-code", None):
            seed(session, catalog, state="failed", terminal_at=NOW - timedelta(seconds=2), error_code=code, task_type="legacy_unknown")
        for state in ("cancelled", "expired"):
            seed(session, catalog, state=state, terminal_at=NOW - timedelta(seconds=3))
    value = OperationalQueries(pg, test_settings).statistics(QueueStatisticsQuery())
    result = QueueStatistics.model_validate(value)
    assert result.totals.succeeded == result.totals.failed == 7 and result.totals.completed == 16
    assert result.success_rate_bps == 5000 and result.successful_duration.samples == 5 and result.successful_duration.missing_samples == 2
    assert result.successful_duration.p50_ms == 2000 and result.successful_duration.p95_ms == 100000
    assert {item.category: item.count for item in result.failure_classes} == {"out_of_memory": 1, "invalid_output": 1, "preparation": 2, "execution": 1, "other": 1, "unclassified": 1}
    assert result.task_types[-1].failed == 7 and result.task_types[-2].succeeded == 5
    assert "PRIVATE STATISTICS PROMPT" not in str(value) and "private-unknown-code" not in str(value)


def test_intersection_filters_and_pagination_do_not_change_aggregate_cohort(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    owner = catalog["identity"]
    with pg.begin() as session:
        target = seed(session, catalog, state="succeeded", terminal_at=NOW - timedelta(seconds=1), task_type="text_to_audio")
        assign(session, target, catalog, 1, target.accepted_at + timedelta(seconds=10))
        seed(session, catalog, state="succeeded", terminal_at=target.terminal_at, model_alias="different")
        workflow_id = session.get(WorkflowRelease, catalog["release_id"]).workflow_id
    values = dict(window="1h", tenant_id=public_id("ten", owner.tenant_id), project_id=public_id("prj", owner.project_id), key_id=public_id("key", owner.key_id), workflow_id=public_id("wf", workflow_id), release_id=public_id("wfr", catalog["release_id"]), channel_id=public_id("ch", catalog["channel_ids"][0]), model="history-image", state="succeeded", created_from=int(target.created_at.timestamp()), created_before=int(NOW.timestamp()), task_type="text_to_audio")
    service = OperationalQueries(pg, test_settings)
    query = QueueStatisticsQuery.model_validate(values)
    result = service.statistics(query)
    assert result["totals"]["succeeded"] == result["totals"]["accepted"] == 1 and result["filters"] == query.filters()
    assert service.statistics(QueueStatisticsQuery.model_validate({**values, "page": 10000})) == result
    for key, value in {"tenant_id": public_id("ten", uuid4()), "project_id": public_id("prj", uuid4()), "key_id": public_id("key", uuid4()), "workflow_id": public_id("wf", uuid4()), "release_id": public_id("wfr", uuid4()), "channel_id": public_id("ch", catalog["channel_ids"][1]), "model": "missing", "state": "failed", "task_type": "text_to_image", "created_from": int(NOW.timestamp()) - 1}.items():
        empty = service.statistics(QueueStatisticsQuery.model_validate({**values, key: value}))
        assert empty["totals"]["succeeded"] == empty["totals"]["accepted"] == 0, key


def test_statistics_are_three_bounded_read_only_queries_with_no_accounting_effect(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    with pg.begin() as session:
        seed(session, catalog, state="succeeded", terminal_at=NOW - timedelta(seconds=1))
    tables = (Job, Attempt, GpuOccupancy, QuotaBucket, AuditEvent, OutboxEvent)
    def counts() -> list[int]:
        with pg() as session:
            return [session.scalar(select(func.count()).select_from(table)) for table in tables]
    before = counts()
    statements: list[str] = []
    def capture(connection: Any, _: Any, statement: str, __: Any, ___: Any, ____: Any) -> None:
        statements.append(statement)
        if statement.startswith("SELECT"):
            with connection.connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation'),current_setting('statement_timeout'),current_setting('lock_timeout')")
                assert cursor.fetchone() == ("on", "repeatable read", "1500ms", "1s")
    engine = pg.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        OperationalQueries(pg, test_settings).statistics(QueueStatisticsQuery())
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    selects = [s for s in statements if s.startswith("SELECT")]
    assert len(selects) == 3 and all("jobs.request" not in s and "jobs.execution" not in s for s in selects)
    assert before == counts()


def test_repeatable_read_hides_commit_between_aggregates(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    engine = pg.kw["bind"]
    inserted = False
    def inject(_: Any, __: Any, statement: str, ___: Any, ____: Any, _____: Any) -> None:
        nonlocal inserted
        if statement.startswith("SELECT") and not inserted:
            inserted = True
            with pg.begin() as session:
                seed(session, catalog, state="succeeded", terminal_at=NOW - timedelta(seconds=1))
    event.listen(engine, "after_cursor_execute", inject)
    try:
        snapshot = OperationalQueries(pg, test_settings).statistics(QueueStatisticsQuery())
    finally:
        event.remove(engine, "after_cursor_execute", inject)
    assert inserted and snapshot["totals"]["accepted"] == snapshot["totals"]["succeeded"] == 0
    assert OperationalQueries(pg, test_settings).statistics(QueueStatisticsQuery())["totals"]["succeeded"] == 1


def test_real_lock_timeout_is_redacted_service_unavailable(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    with pg.begin() as locker:
        locker.execute(text("LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(DomainError) as caught:
            OperationalQueries(pg, test_settings).statistics(QueueStatisticsQuery())
    assert caught.value.status == 503 and caught.value.code == "statistics_unavailable"
    assert "SELECT" not in str(caught.value) and "jobs" not in str(caught.value)


def test_detail_limits_attempts_preserves_current_and_uses_full_history_first_assignment(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None) -> None:
    with pg.begin() as session:
        job = seed(session, catalog, state="running", accepted_at=NOW - timedelta(minutes=10))
        attempts = [assign(session, job, catalog, index + 1, job.accepted_at + timedelta(seconds=index + 5), current=False) for index in range(105)]
        job.current_attempt_id = attempts[0].id
        identifier, current = job.id, public_id("att", attempts[0].id)
    actor = AdminIdentity(uuid4(), "Synthetic", "observer", uuid4())
    result = JobAdministration(pg, test_settings).detail(actor, identifier, "statistics-attempt-limit")
    assert len(result["attempts"]) == 100 and result["attempts_has_more"] and result["attempts"][0]["id"] == current
    assert result["queue_wait_seconds"] == 5 and result["timing"]["queue"]["milliseconds"] == 5000
    assert result["timing"]["elapsed"]["milliseconds"] == 600000
    assert result["attempts"][0]["timing"]["elapsed"]["status"] == "open"
    assert all(item["timing"]["submission"]["status"] == "unknown" for item in result["attempts"])


@pytest.mark.parametrize("parameters", [{"window": "30d"}, {"task_type": "speech"}, {"include_validation": "maybe"}, {"page": 10001}, {"created_from": 9, "created_before": 9}, {"unknown": "ignored"}])
def test_statistics_http_requires_admin_and_strict_query(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None, parameters: dict[str, Any]) -> None:
    with pg.begin() as session:
        session.add(AdminUser(username="statistics-observer", password_hash=password_hash(PASSWORD), role="observer"))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        path = "/admin/api/v1/queues/statistics"
        assert client.get(path).status_code == 401
        for token in ("sk-gen_" + "a" * 64, "worker_" + "b" * 64, "gateway_" + "c" * 64):
            assert client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 401
        assert client.post("/admin/api/v1/session", json={"username": "statistics-observer", "password": PASSWORD}, headers={"Origin": test_settings.public_origin}).status_code == 200
        assert client.get(path, params=parameters).status_code == 422
        valid = client.get(path, params={"window": "1h", "include_validation": "true", "task_type": "text_to_audio"})
        assert valid.status_code == 200, valid.text
        QueueStatistics.model_validate(valid.json())


@pytest.mark.parametrize("change", ["rate", "buckets", "categories", "samples", "percentiles", "task_types"])
def test_response_dto_rejects_inconsistent_aggregate_arithmetic(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, clock: None, change: str) -> None:
    result = deepcopy(OperationalQueries(pg, test_settings).statistics(QueueStatisticsQuery()))
    if change == "rate":
        result["success_rate_bps"] = 0
    elif change == "buckets":
        result["buckets"][0]["succeeded"] = 1
    elif change == "categories":
        result["failure_classes"][0]["category"] = result["failure_classes"][1]["category"]
    elif change == "samples":
        result["successful_duration"]["missing_samples"] = 1
    elif change == "percentiles":
        result["successful_duration"]["p50_ms"] = 0
    else:
        result["task_types"][0]["accepted"] = 1
    with pytest.raises(ValueError):
        QueueStatistics.model_validate(result)
