from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.alert_queries import AlertFailure, AlertQuery
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_alerts import AlertQueries
from generation_infrastructure.admin_jobs import JobAdministration
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    Attempt,
    AuditEvent,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    PublicModel,
    UsageLedger,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import SecretVault, issue_secret
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, REQUEST, admin_context
from tests.integration.test_audio import audio_catalog as audio_catalog

pytestmark = pytest.mark.integration


def failed(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings, *, code: str = "comfy_oom", stopped: bool = True, body: dict[str, Any] | None = None) -> tuple[str, str]:
    from conftest import capacity_sample

    jobs = JobRepository(pg)
    selected = jobs.create(catalog["identity"], body or REQUEST, str(uuid4()), "synthetic-alert")
    leases = LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    with pg() as session:
        sequence = session.get(Node, catalog["node_id"]).inventory.get("capacity_sequence", 0) + 1
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=sequence))
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == selected["id"]
    events = [("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": claim["workflow_digest"], "journal_committed": True}), ("submitted", {"prompt_id": "synthetic-private-prompt"}), ("execution_failed", {"error_code": code, "execution_stop_confirmed": stopped, "evidence_digest": "d" * 64})]
    for seq, (kind, payload) in enumerate(events, 1):
        leases.event(catalog["node_id"], WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=claim["node_id"], boot_id=claim["boot_id"], job_id=claim["job_id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=seq, observed_at=int(utcnow().timestamp()), type=kind, payload=payload))
    return selected["id"], claim["attempt_id"]


@pytest.mark.parametrize("code,kind", [("comfy_oom", "job_oom_recent"), ("comfy_output_invalid", "job_output_invalid_recent")])
def test_real_failure_protocol_projects_only_safe_context_without_mutations(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, code: str, kind: Any) -> None:
    job_id, attempt_id = failed(pg, catalog, test_settings, code=code)
    query = AlertQueries(pg, test_settings)
    with pg() as session:
        before = (session.scalar(select(func.count()).select_from(AuditEvent)), session.scalar(select(func.count()).select_from(UsageLedger)), session.get(Gpu, catalog["gpu_id"]).fence)
    result = query.snapshot(kind=kind)
    assert result["total"] == 1
    item = result["data"][0]
    assert item["resource_id"] == job_id and item["id"] == "alert:" + kind + ":" + job_id and item["severity"] == "warning"
    context = AlertFailure.model_validate(item["failure"])
    assert context.code == code and context.model == "synthetic-image" and context.attempt_id == attempt_id
    assert context.release_id == public_id("wfr", catalog["release_id"])
    assert "private" not in json.dumps(result) and "request" not in json.dumps(result)
    assert query.snapshot(kind=kind)["data"] == result["data"]
    with pg() as session:
        after = (session.scalar(select(func.count()).select_from(AuditEvent)), session.scalar(select(func.count()).select_from(UsageLedger)), session.get(Gpu, catalog["gpu_id"]).fence)
        assert before == after and session.get(GpuOccupancy, catalog["gpu_id"]) is None


@pytest.mark.parametrize("offset,expected", [(86401, 0), (86400, 1), (1, 1), (0, 1), (-1, 0)])
def test_failure_window_uses_terminal_time_inclusive_and_rejects_future(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch, offset: int, expected: int) -> None:
    job_id, attempt_id = failed(pg, catalog, test_settings)
    clock = utcnow().replace(microsecond=0) + timedelta(days=2)
    monkeypatch.setattr("generation_infrastructure.admin_alerts.utcnow", lambda: clock)
    at = clock - timedelta(seconds=offset)
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", job_id))
        attempt = session.get(Attempt, parse_id("att", attempt_id))
        job.accepted_at, job.terminal_at = at - timedelta(minutes=1), at
        attempt.generation_released_at = attempt.closed_at = at
    result = AlertQueries(pg, test_settings).snapshot(kind="job_oom_recent")
    assert result["total"] == expected
    if expected:
        assert result["data"][0]["recorded_at"] == int(at.timestamp())


@pytest.mark.parametrize("invalid", ["generic", "running", "cancelled", "succeeded", "missing_terminal", "open_attempt", "no_generation_release", "non_current", "wrong_order", "future_close"])
def test_incomplete_or_unrelated_facts_never_become_confirmed_oom(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, invalid: str) -> None:
    job_id, attempt_id = failed(pg, catalog, test_settings)
    with pg.begin() as session:
        job, attempt = session.get(Job, parse_id("job", job_id)), session.get(Attempt, parse_id("att", attempt_id))
        if invalid == "generic":
            job.error_code = "comfy_execution_failed"
            job.request = {**job.request, "private_error_message": "CUDA out of memory"}
        elif invalid in ("running", "cancelled", "succeeded"):
            job.state = invalid
        elif invalid == "missing_terminal":
            job.terminal_at = None
        elif invalid == "open_attempt":
            attempt.closed_at = None
        elif invalid == "no_generation_release":
            attempt.generation_released_at = None
        elif invalid == "non_current":
            job.current_attempt_id = None
        elif invalid == "wrong_order":
            attempt.generation_released_at = attempt.closed_at + timedelta(seconds=1)
        else:
            attempt.closed_at = utcnow() + timedelta(days=1)
    assert AlertQueries(pg, test_settings).snapshot(kind="job_oom_recent")["total"] == 0


def test_unconfirmed_oom_keeps_unknown_gpu_and_has_no_confirmed_failure_alert(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    _, attempt_id = failed(pg, catalog, test_settings, stopped=False)
    result = AlertQueries(pg, test_settings).snapshot(kind="job_oom_recent")
    assert result["total"] == 0
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain
        assert session.get(Attempt, parse_id("att", attempt_id)).closed_at is None
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_audio_failure_uses_same_evidence_and_separate_resource_accounting(pg: sessionmaker[Session], audio_catalog: dict[str, Any], test_settings: Settings) -> None:
    job_id, _ = failed(pg, audio_catalog, test_settings, body={"model": "synthetic-audio", "task_type": "text_to_audio", "input": {"prompt": "Synthetic private sound"}})
    item = AlertQueries(pg, test_settings).snapshot(query=AlertQuery(resource_id=job_id, kind="job_oom_recent"))["data"][0]
    assert item["failure"]["task_type"] == "text_to_audio" and item["failure"]["model"] == "synthetic-audio"
    with pg() as session:
        usage = session.scalar(select(UsageLedger))
        assert usage.audio_milliseconds == usage.video_milliseconds == usage.image_count == 0


def test_video_failure_is_not_reported_as_image_or_audio(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = catalog["identity"]
    with pg.begin() as session:
        workflow = Workflow(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Synthetic alert video", task_type="text_to_video", draft={})
        session.add(workflow)
        session.flush()
        manifest = {**session.get(WorkflowRelease, catalog["release_id"]).manifest, "task_type": "text_to_video"}
        release = WorkflowRelease(tenant_id=identity.tenant_id, project_id=identity.project_id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        for channel in catalog["channel_ids"]:
            session.add(WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest, observed_at=utcnow()))
        session.add(PublicModel(tenant_id=identity.tenant_id, project_id=identity.project_id, alias="synthetic-alert-video", task_type="text_to_video", release_id=release.id, channel_ids=[public_id("ch", c) for c in catalog["channel_ids"]], defaults={"size": "1280x720", "seconds": 2, "quality": "standard"}, published=True))
    job_id, _ = failed(pg, catalog, test_settings, code="comfy_output_invalid", body={"model": "synthetic-alert-video", "task_type": "text_to_video", "input": {"prompt": "Synthetic clip"}})
    result = AlertQueries(pg, test_settings).snapshot(query=AlertQuery(kind="job_output_invalid_recent", severity="warning", resource_id=job_id))
    assert result["total"] == 1 and result["data"][0]["failure"]["task_type"] == "text_to_video"


def test_new_retry_does_not_remove_recent_original_failure(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path) -> None:
    from tests.integration.test_spec_isolation_operations import complete, event

    job_id, _ = failed(pg, catalog, test_settings)
    actor, _, _ = admin_context(pg, test_settings)
    retried = JobAdministration(pg, test_settings).retry(actor, parse_id("job", job_id), str(uuid4()), "test")
    assert retried["id"] != job_id
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == retried["id"]
    for seq, (kind, payload) in enumerate([("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": claim["workflow_digest"], "journal_committed": True}), ("submitted", {"prompt_id": "synthetic-retry"})], 1):
        leases.event(catalog["node_id"], event(catalog, claim, seq, kind, payload))
    complete(pg, test_settings, leases, catalog, claim, tmp_path)
    with pg() as session:
        assert session.get(Job, parse_id("job", retried["id"])).state == "succeeded"
    assert AlertQueries(pg, test_settings).snapshot(query=AlertQuery(kind="job_oom_recent", resource_id=job_id))["total"] == 1


def test_fifty_row_pages_have_stable_order_safe_projection_and_no_duplicates(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    selected, attempted = failed(pg, catalog, test_settings)
    # Clone only synthetic finalized records for the read-page fixture. This is
    # not an execution or capacity acceptance test and never dispatches clones.
    with pg.begin() as session:
        source = session.get(Job, parse_id("job", selected))
        attempt = session.get(Attempt, parse_id("att", attempted))
        for i in range(52):
            job_values = {column.key: getattr(source, column.key) for column in inspect(Job).columns if column.key not in ("id", "current_attempt_id")}
            clone = Job(**job_values, id=uuid4(), current_attempt_id=None)
            session.add(clone)
            session.flush()
            attempt_values = {column.key: getattr(attempt, column.key) for column in inspect(Attempt).columns if column.key not in ("id", "job_id", "fence")}
            copied = Attempt(**attempt_values, id=uuid4(), job_id=clone.id, fence=100 + i)
            session.add(copied)
            session.flush()
            clone.current_attempt_id = copied.id
    statements: list[str] = []
    engine = pg.kw["bind"]

    def capture(_conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _many: bool) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        query = AlertQueries(pg, test_settings)
        first, second = query.snapshot(1, "job_oom_recent"), query.snapshot(2, "job_oom_recent")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert first["total"] == second["total"] == 53
    assert len(first["data"]) == 50 and first["has_more"] and len(second["data"]) == 3 and not second["has_more"]
    assert len({item["id"] for item in first["data"] + second["data"]}) == 53
    assert [item["resource_id"] for item in first["data"] + second["data"]] == sorted(item["resource_id"] for item in first["data"] + second["data"])
    assert not any("jobs.request" in statement or "channel_snapshot" in statement for statement in statements)


def test_filters_intersect_and_exact_prefix_prevents_wrong_resource_matches(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    job_id, _ = failed(pg, catalog, test_settings)
    queries = AlertQueries(pg, test_settings)
    assert queries.snapshot(query=AlertQuery(kind="job_oom_recent", resource_id=job_id, severity="warning"))["total"] == 1
    for query in [AlertQuery(kind="job_oom_recent", severity="critical"), AlertQuery(kind="job_output_invalid_recent", resource_id=job_id), AlertQuery(resource_id=job_id.replace("job_", "gpu_")), AlertQuery(resource_id=public_id("job", uuid4()))]:
        assert queries.snapshot(query=query)["total"] == 0


def test_count_rows_and_context_share_same_read_only_snapshot(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    job_id, _ = failed(pg, catalog, test_settings)
    counted, proceed = threading.Event(), threading.Event()
    engine = pg.kw["bind"]

    def after_count(_conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _many: bool) -> None:
        if "SELECT count(*)" in statement and "active_alerts" in statement and not counted.is_set():
            counted.set()
            assert proceed.wait(5)

    event.listen(engine, "after_cursor_execute", after_count)
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(AlertQueries(pg, test_settings).snapshot, kind="job_oom_recent")
            try:
                assert counted.wait(5)
                with pg.begin() as session:
                    session.get(Job, parse_id("job", job_id)).error_code = "comfy_execution_failed"
            finally:
                proceed.set()
            result = future.result(timeout=5)
    finally:
        event.remove(engine, "after_cursor_execute", after_count)
    assert result["total"] == len(result["data"]) == 1 and result["data"][0]["failure"]["code"] == "comfy_oom"
    assert AlertQueries(pg, test_settings).snapshot(kind="job_oom_recent")["total"] == 0


@pytest.mark.parametrize("corrupt", ["PRIVATE-invalid-timestamp", True, 1.25, -1, 10**40, None, {}, []])
def test_corrupt_capacity_timestamp_is_unknown_without_cast_failure_or_echo(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, corrupt: Any) -> None:
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        node.observed_at = utcnow()
        node.inventory = {**node.inventory, "capacity": {**node.inventory["capacity"], "observed_at_ms": corrupt}}
    result = AlertQueries(pg, test_settings).snapshot(kind="worker_capacity_unavailable")
    assert result["total"] == 1 and result["data"][0]["capacity"] is None
    assert "PRIVATE" not in json.dumps(result)


def test_real_database_lock_wait_has_bounded_safe_failure(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    failed(pg, catalog, test_settings)
    with pg.begin() as held:
        held.execute(text("LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE"))
        started = time.monotonic()
        with ThreadPoolExecutor(1) as pool, pytest.raises(DomainError) as failure:
            pool.submit(AlertQueries(pg, test_settings).snapshot, kind="job_oom_recent").result(timeout=5)
        assert failure.value.code == "alert_query_unavailable" and failure.value.status == 503
        assert time.monotonic() - started < 4
        assert "SELECT" not in str(failure.value) and "postgres" not in str(failure.value)
    assert AlertQueries(pg, test_settings).snapshot(kind="job_oom_recent")["total"] == 1


@pytest.mark.parametrize("role", ["administrator", "operator", "workflow_maintainer", "observer"])
def test_http_roles_query_bounds_and_unknown_fields(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str) -> None:
    job_id, _ = failed(pg, catalog, test_settings)
    admin_context(pg, test_settings, role=role)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get("/admin/api/v1/alerts").status_code == 401
        assert client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD}, headers={"Origin": test_settings.public_origin}).status_code == 200
        response = client.get("/admin/api/v1/alerts", params={"kind": "job_oom_recent", "severity": "warning", "resource_id": job_id})
        assert response.status_code == 200 and response.json()["total"] == 1
        for query in [{"page": 1001}, {"page": "true"}, {"page": "1.0"}, {"resource_id": "job_short"}, {"severity": "fatal"}, {"private": "DO-NOT-ECHO"}]:
            rejected = client.get("/admin/api/v1/alerts", params=query)
            assert rejected.status_code == 422 and "DO-NOT-ECHO" not in rejected.text


@pytest.mark.parametrize("prefix", ["sk-gen", "worker", "gateway"])
def test_business_worker_gateway_credentials_cannot_read_alerts(pg: sessionmaker[Session], test_settings: Settings, prefix: str) -> None:
    _, secret = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get("/admin/api/v1/alerts", headers={"Authorization": "Bearer " + secret}).status_code == 401
