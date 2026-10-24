from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.queue_queries import QueueOptionQuery, QueueQuery
from generation_domain.values import public_id, utcnow
from generation_infrastructure.admin_jobs import JobAdministration
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.models import (
    AdminMutation,
    AdminUser,
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    PublicModel,
    Workflow,
    WorkflowRelease,
)
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-queue-filter-test-password"


def seed(session: Session, catalog: dict[str, Any], *, state: str = "queued", **values: Any) -> Job:
    now = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
    owner = catalog["identity"]
    data = dict(tenant_id=owner.tenant_id, project_id=owner.project_id, key_id=owner.key_id, release_id=catalog["release_id"], model_alias="history-image", task_type="text_to_image", state=state, request={"input": {"prompt": "PRIVATE SYNTHETIC PROMPT"}}, execution={}, request_fingerprint="a" * 64, execution_fingerprint="b" * 64, eligible_channels=[public_id("ch", identifier) for identifier in catalog["channel_ids"]], accepted_at=now, created_at=now, deadline=now + timedelta(days=3), accepted_period="2026-10-23")
    session.add(job := Job(**{**data, **values}))
    session.flush()
    return job


def assign(session: Session, job: Job, catalog: dict[str, Any], channel: int = 0, fence: int = 1, seconds: int = 30) -> Attempt:
    attempt = Attempt(job_id=job.id, channel_id=catalog["channel_ids"][channel], channel_snapshot={}, node_id=catalog["node_id"], boot_id=catalog["boot_id"], gpu_id=catalog["gpu_id"], fence=fence, lease_expires_at=utcnow() + timedelta(minutes=1), created_at=job.accepted_at + timedelta(seconds=seconds))
    session.add(attempt)
    session.flush()
    job.current_attempt_id = attempt.id
    return attempt


def test_filters_intersect_frozen_history_time_and_current_or_eligible_channel(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    with pg.begin() as session:
        target = seed(session, catalog)
        target_id = public_id("job", target.id)
        running = seed(session, catalog, state="running")
        attempt = assign(session, running, catalog, channel=1)
        session.add(GpuOccupancy(gpu_id=catalog["gpu_id"], attempt_id=attempt.id, job_id=running.id, uncertain=True))
        seed(session, catalog, model_alias="other-image")
        seed(session, catalog, created_at=target.created_at + timedelta(hours=1))
        seed(session, catalog, state="admission_pending", accepted_at=None)
        workflow_id = session.get(WorkflowRelease, catalog["release_id"]).workflow_id
        # Moving the public alias to another release must not rewrite job history.
        other = Workflow(tenant_id=target.tenant_id, project_id=target.project_id, name="Other", task_type="text_to_image", draft={})
        session.add(other)
        session.flush()
        release = WorkflowRelease(tenant_id=target.tenant_id, project_id=target.project_id, workflow_id=other.id, version=1, digest="c" * 64, manifest={}, validated=False)
        session.add(release)
        session.flush()
        seed(session, catalog, release_id=release.id)
        session.scalar(select(PublicModel)).release_id = release.id
        epoch = int(target.created_at.timestamp())
    owner = catalog["identity"]
    query = QueueQuery(tenant_id=public_id("ten", owner.tenant_id), project_id=public_id("prj", owner.project_id), key_id=public_id("key", owner.key_id), model="history-image", workflow_id=public_id("wf", workflow_id), release_id=public_id("wfr", catalog["release_id"]), channel_id=public_id("ch", catalog["channel_ids"][0]), state="queued", created_from=epoch, created_before=epoch + 3600)
    jobs = JobAdministration(pg, test_settings).list(query)
    assert [row["id"] for row in jobs["data"]] == [target_id]
    assert jobs["filters"] == query.filters()
    snapshot = AdminQueries(pg, test_settings).queue_snapshot(query)
    assert snapshot["states"] == {"queued": 1} and snapshot["global_unassigned_queued"] == snapshot["unfinished"] == 1
    assert snapshot["occupied_physical_gpus"] == 1
    assert len(snapshot["channels"]) == 1 and snapshot["channels"][0]["shared_candidates"] == 1
    assert snapshot["channels"][0]["gpu_occupied"] and snapshot["channels"][0]["gpu_occupancy_uncertain"]
    # A running job's old candidate list cannot make it belong to the other channel.
    assert JobAdministration(pg, test_settings).list(QueueQuery(state="running", channel_id=query.channel_id))["data"] == []
    assigned = AdminQueries(pg, test_settings).queue_snapshot(QueueQuery(state="running", channel_id=public_id("ch", catalog["channel_ids"][1])))
    assert assigned["unfinished"] == assigned["channels"][0]["assigned_unfinished"] == 1
    assert assigned["global_unassigned_queued"] == assigned["channels"][0]["shared_candidates"] == 0
    assert JobAdministration(pg, test_settings).list(QueueQuery(created_before=epoch))["data"] == []
    assert JobAdministration(pg, test_settings).list(QueueQuery(project_id=public_id("prj", uuid4())))["data"] == []


def test_waiting_stops_at_first_attempt_and_detail_uses_same_clock(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    with pg.begin() as session:
        running = seed(session, catalog, state="running")
        assign(session, running, catalog, fence=1, seconds=30)
        assign(session, running, catalog, fence=2, seconds=90)
        cancelled = seed(session, catalog, state="cancelled", terminal_at=running.accepted_at + timedelta(seconds=17))
        pending = seed(session, catalog, state="admission_pending", accepted_at=None)
        missing = seed(session, catalog, accepted_at=None)
        future = seed(session, catalog, accepted_at=utcnow() + timedelta(days=1))
        ids = {"running": running.id, "cancelled": cancelled.id, "pending": pending.id, "missing": missing.id, "future": future.id}
    service = JobAdministration(pg, test_settings)
    rows = {row["id"]: row for row in service.list(QueueQuery())["data"]}
    assert rows[public_id("job", ids["running"])]["queue_wait_seconds"] == 30
    assert rows[public_id("job", ids["cancelled"])]["queue_wait_seconds"] == 17
    for name in ("pending", "missing", "future"):
        assert rows[public_id("job", ids[name])]["queue_wait_seconds"] is None
    actor = AdminIdentity(uuid4(), "Synthetic", "administrator", uuid4())
    detail = service.detail(actor, ids["running"], "queue-wait-test")
    assert detail["queue_wait_seconds"] == 30
    assert detail["first_assigned_at"] == rows[detail["id"]]["first_assigned_at"]


def test_snapshot_batches_fifty_channels_and_paginates_without_false_observations(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    with pg.begin() as session:
        source = session.get(Channel, catalog["channel_ids"][0])
        source.observation = {"queue_observed_at": utcnow().timestamp(), "queue_revision": 1, "queue": {"reachable": True, "queue_running": 2, "queue_pending": 4}}
        other = session.get(Channel, catalog["channel_ids"][1])
        other.observation = {"queue_observed_at": (utcnow() - timedelta(seconds=30)).timestamp(), "queue_revision": 1, "queue": {"reachable": True, "queue_running": 100, "queue_pending": 200}}
        session.get(Gpu, catalog["gpu_id"]).management_hold = uuid4()
        seed(session, catalog)
        for index in range(51):
            session.add(Channel(tenant_id=source.tenant_id, project_id=source.project_id, node_id=source.node_id, gpu_id=source.gpu_id, name=f"Synthetic extra {index:03}", base_url="http://127.0.0.1:18188"))
    engine = pg.kw["bind"]
    statements: list[str] = []
    def capture(_: Any, __: Any, statement: str, ___: Any, ____: Any, _____: Any) -> None:
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        small = AdminQueries(pg, test_settings).queue_snapshot(QueueQuery(channel_id=public_id("ch", catalog["channel_ids"][0])))
        small_count = len(statements)
        statements.clear()
        large = AdminQueries(pg, test_settings).queue_snapshot()
        assert len(statements) == small_count <= 10
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(large["channels"]) == 50 and large["channels_has_more"]
    assert large["global_unassigned_queued"] == 1 and sum(row["shared_candidates"] for row in large["channels"]) == 2
    assert small["channels"][0]["comfy_queue_running"] == 2 and small["channels"][0]["gpu_management_hold"]
    page2 = AdminQueries(pg, test_settings).queue_snapshot(QueueQuery(page=2))
    assert len(page2["channels"]) == 3 and not page2["channels_has_more"]
    assert not ({row["id"] for row in large["channels"]} & {row["id"] for row in page2["channels"]})
    stale = AdminQueries(pg, test_settings).channel_queue(catalog["channel_ids"][1])
    assert stale["observation_status"] == "unknown" and stale["comfy_queue_running"] is None


def test_metadata_includes_archived_workflows_and_historical_aliases_with_safe_paging(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    with pg.begin() as session:
        seed(session, catalog, model_alias="retired-only")
        workflow = session.get(Workflow, session.get(WorkflowRelease, catalog["release_id"]).workflow_id)
        workflow.archived = True
        workflow.draft = {"private_graph": "DO NOT EXPOSE"}
        workflow_id = public_id("wf", workflow.id)
        for index in range(51):
            seed(session, catalog, model_alias=f"history-{index:03}")
    queries = AdminQueries(pg, test_settings)
    options = queries.queue_filter_options(QueueOptionQuery(kind="model", q="history-"))
    assert len(options["data"]) == 50 and options["has_more"]
    assert len(queries.queue_filter_options(QueueOptionQuery(kind="model", q="history-", page=2))["data"]) == 1
    for kind in ("tenant", "project", "model", "workflow", "release", "channel"):
        result = queries.queue_filter_options(QueueOptionQuery(kind=kind))
        assert result["data"] and all(set(row) == {"value", "label"} for row in result["data"])
    assert queries.queue_filter_options(QueueOptionQuery(kind="workflow"))["data"][0]["value"] == workflow_id
    assert queries.queue_filter_options(QueueOptionQuery(kind="release", workflow_id=public_id("wf", uuid4())))["data"] == []
    assert queries.queue_filter_options(QueueOptionQuery(kind="model", q="retired-only"))["data"] == [{"value": "retired-only", "label": "retired-only"}]
    assert queries.queue_filter_options(QueueOptionQuery(kind="model", q="%"))["data"] == []


def test_persisted_admin_cancel_from_prior_version_replays_without_new_metadata(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    from generation_infrastructure.jobs import JobRepository

    created = JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic backward compatibility"}}, uuid4().hex, "queue-compatibility")
    with pg.begin() as session:
        session.add(AdminUser(username="queue-admin", password_hash=password_hash(PASSWORD), role="administrator"))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": "queue-admin", "password": PASSWORD}).json()
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        path = "/admin/api/v1/jobs/" + created["id"] + "/cancel"
        headers = {"Idempotency-Key": "prior-version-cached-cancel"}
        assert client.post(path, headers=headers).status_code == 202
        with pg.begin() as session:
            cached = session.scalar(select(AdminMutation))
            cached.result = {key: value for key, value in cached.result.items() if key not in {"release_id", "accepted_at", "first_assigned_at", "queue_wait_seconds"}}
        replay = client.post(path, headers=headers)
        assert replay.status_code == 202, replay.text
        assert replay.json()["id"] == created["id"] and replay.json()["release_id"] is None
        assert replay.json()["queue_wait_seconds"] is None


@pytest.mark.parametrize("parameters", [{"state": "busy"}, {"created_from": "1.2"}, {"created_from": 9, "created_before": 9}, {"tenant_id": "prj_" + "a" * 32}, {"page": 10001}, {"unknown": "ignored"}])
def test_http_query_validation_and_admin_auth(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, parameters: dict[str, Any]) -> None:
    with pg.begin() as session:
        session.add(AdminUser(username="queue-admin", password_hash=password_hash(PASSWORD), role="administrator"))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        for path in ("snapshot", "jobs", "filter-options"):
            assert client.get("/admin/api/v1/queues/" + path).status_code == 401
        login = client.post("/admin/api/v1/session", json={"username": "queue-admin", "password": PASSWORD}, headers={"Origin": test_settings.public_origin})
        assert login.status_code == 200
        for path in ("snapshot", "jobs"):
            invalid = client.get("/admin/api/v1/queues/" + path, params=parameters)
            assert invalid.status_code == 422, invalid.text
        valid = client.get("/admin/api/v1/queues/jobs", params={"state": "queued", "created_from": 0})
        assert valid.status_code == 200, valid.text
        assert valid.json()["filters"] == {"state": "queued", "created_from": 0}
        for kind in ("tenant", "project", "model", "workflow", "release", "channel"):
            response = client.get("/admin/api/v1/queues/filter-options", params={"kind": kind})
            assert response.status_code == 200, response.text
