from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from conftest import reset_test_database
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.admin import JobPriority
from generation_contracts.queue_queries import QueueQuery
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.retries import RETRY_CODE
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.admin_jobs import JobAdministration
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.models import (
    AdminUser,
    ApiKey,
    Attempt,
    Base,
    GatewayRequestFence,
    GatewayService,
    GpuOccupancy,
    Job,
    OutboxEvent,
    QuotaBucket,
    UsageLedger,
    WorkerInbox,
)
from generation_infrastructure.retries import promote_due_retries
from generation_infrastructure.security import SecretVault, password_hash
from sqlalchemy import create_engine, func, select, text
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic retry transaction"}}


def event(catalog, claim, sequence=2, **payload):
    return WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=public_id("node", catalog["node_id"]), boot_id=str(catalog["boot_id"]), job_id=claim["job_id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=sequence, observed_at=int(utcnow().timestamp()), type="execution_failed", payload={"error_code": RETRY_CODE, "execution_stop_confirmed": True, "evidence_digest": "a" * 64, **payload})


def setup(pg, catalog, settings):
    jobs = JobRepository(pg)
    leases = LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    result = jobs.create(catalog["identity"], REQUEST, str(uuid4()), "synthetic-retry")
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == result["id"]
    return jobs, leases, claim


def make_due(pg, claim):
    with pg.begin() as session:
        session.get(Job, parse_id("job", claim["job_id"])).retry_after = utcnow() - timedelta(seconds=1)


def key_counters(session, key_id):
    return {bucket.period: (bucket.daily_jobs, bucket.unfinished_jobs, bucket.generating_jobs, bucket.daily_image_count) for bucket in session.scalars(select(QuotaBucket).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.scope_id == key_id))}


def test_scheduled_retry_releases_generation_but_preserves_admission_and_snapshot(pg, catalog, test_settings):
    jobs, leases, claim = setup(pg, catalog, test_settings)
    with pg() as session:
        before = session.get(Job, parse_id("job", claim["job_id"]))
        snapshot = deepcopy(before.execution)
        fingerprint = before.execution_fingerprint
        accepted_period = before.accepted_period
    result = leases.event(catalog["node_id"], event(catalog, claim))
    assert result["status"] == "retry_wait"
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        attempt = session.get(Attempt, parse_id("att", claim["attempt_id"]))
        assert job.current_attempt_id is None and job.retry_count == 1 and job.retry_source_attempt_id == attempt.id
        assert utcnow() + timedelta(seconds=3) < job.retry_after < utcnow() + timedelta(seconds=6)
        assert job.execution == snapshot and job.execution_fingerprint == fingerprint and job.accepted_period == accepted_period
        assert job.error_code is None and job.terminal_at is None and attempt.closed_at and attempt.generation_released_at
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is None
        counters = key_counters(session, catalog["identity"].key_id)
        assert counters["active"][1:3] == (1, 0)
        assert counters[accepted_period][0] == counters[accepted_period][3] == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.kind == "gateway.settlement")) == 0
    assert leases.claim(catalog["node_id"], catalog["boot_id"]) is None
    assert promote_due_retries(pg) == 0
    assert jobs.get(catalog["identity"], parse_id("job", claim["job_id"]))["retry"]["count"] == 1


def test_two_retries_then_single_terminal_accounting_and_stable_seed(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    original = deepcopy(claim["execution"])
    previous_fence = claim["fencing_token"]
    for index in range(3):
        response = leases.event(catalog["node_id"], event(catalog, claim))
        if index == 2:
            assert response["status"] == "failed"
            break
        assert response["status"] == "retry_wait"
        make_due(pg, claim)
        assert promote_due_retries(pg) == 1
        claim = leases.claim(catalog["node_id"], catalog["boot_id"])
        assert claim and claim["execution"] == original and claim["fencing_token"] > previous_fence
        previous_fence = claim["fencing_token"]
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        assert job.retry_count == 2 and job.retry_after is None and job.terminal_at
        assert session.scalar(select(func.count()).select_from(Attempt)) == 3
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        counters = key_counters(session, job.key_id)
        assert counters["active"][1:3] == (0, 0)
        assert counters[job.accepted_period][0] == 1 and counters[job.accepted_period][3] == 0


def test_concurrent_identical_failure_replays_once_and_stale_new_event_is_rejected(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    failure = event(catalog, claim)
    with ThreadPoolExecutor(4) as pool:
        responses = list(pool.map(lambda _: leases.event(catalog["node_id"], failure), range(4)))
    assert responses == [responses[0]] * 4 and responses[0]["status"] == "retry_wait"
    make_due(pg, claim)
    assert promote_due_retries(pg) == 1
    new_claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert new_claim and new_claim["attempt_id"] != claim["attempt_id"]
    assert leases.event(catalog["node_id"], failure) == responses[0]
    with pytest.raises(DomainError):
        leases.event(catalog["node_id"], event(catalog, claim, sequence=3))
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        assert job.retry_count == 1 and job.current_attempt_id == parse_id("att", new_claim["attempt_id"])
        assert session.scalar(select(func.count()).select_from(WorkerInbox).where(WorkerInbox.event_id == UUID(failure.event_id))) == 1


def test_multi_runner_promotes_once_without_releasing_another_job_gpu(pg, catalog, test_settings):
    jobs, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    jobs.create(catalog["identity"], REQUEST, str(uuid4()), "synthetic-second")
    other = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert other and other["job_id"] != claim["job_id"]
    make_due(pg, claim)
    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(lambda _: promote_due_retries(pg), range(4))) == 1
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]).attempt_id == parse_id("att", other["attempt_id"])
        assert session.get(Job, parse_id("job", claim["job_id"])).state == "queued"


@pytest.mark.parametrize("stop", [False, True])
def test_unknown_stays_occupied_and_legacy_generic_failure_is_terminal(pg, catalog, test_settings, stop):
    _, leases, claim = setup(pg, catalog, test_settings)
    outcome = leases.event(catalog["node_id"], event(catalog, claim, execution_stop_confirmed=stop, error_code="preparation_failed" if stop else RETRY_CODE))
    assert outcome["status"] == ("failed" if stop else "reconciling")
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        assert job.retry_count == 0
        assert (session.get(GpuOccupancy, catalog["gpu_id"]) is not None) is (not stop)


def test_cancel_and_due_runners_settle_once(pg, catalog, test_settings):
    jobs, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    make_due(pg, claim)
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(jobs.cancel, catalog["identity"], parse_id("job", claim["job_id"]), "synthetic-cancel"), *[pool.submit(promote_due_retries, pg) for _ in range(3)]]
        [future.result() for future in futures]
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        assert job.state == "cancelled" and job.retry_after is None
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1


def test_deadline_expiry_is_recovered_by_runner_and_accounted_once(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    with pg.begin() as session:
        session.get(Job, parse_id("job", claim["job_id"])).deadline = utcnow() - timedelta(seconds=1)
    assert Maintenance(pg).tick()["queue_expired"] == 1
    assert Maintenance(pg).tick()["queue_expired"] == 0
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).state == "expired"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1


def test_new_concurrency_cap_is_checked_but_key_disable_is_not_implicit_cancel(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    make_due(pg, claim)
    assert promote_due_retries(pg) == 1
    with pg.begin() as session:
        key = session.get(ApiKey, catalog["identity"].key_id)
        key.enabled = False
        policy = deepcopy(key.policy)
        policy["limits"]["generating_jobs"] = {"mode": "bounded", "value": 0}
        key.policy = policy
    assert leases.claim(catalog["node_id"], catalog["boot_id"]) is None
    with pg.begin() as session:
        key = session.get(ApiKey, catalog["identity"].key_id)
        policy = deepcopy(key.policy)
        policy["limits"]["generating_jobs"] = {"mode": "bounded", "value": 2}
        key.policy = policy
    assert leases.claim(catalog["node_id"], catalog["boot_id"])["job_id"] == claim["job_id"]


@pytest.mark.parametrize("evidence", [None, {"release": {"execution_stop_confirmed": True, "error_code": RETRY_CODE, "evidence_digest": "invalid"}}])
def test_incomplete_source_evidence_never_promotes(pg, catalog, test_settings, evidence):
    _, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    make_due(pg, claim)
    with pg.begin() as session:
        session.get(Attempt, parse_id("att", claim["attempt_id"])).generation_evidence = evidence
    assert promote_due_retries(pg) == 0


def test_database_enforces_retry_count_and_schedule_consistency(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    for changes in ({"retry_count": 3}, {"current_attempt_id": parse_id("att", claim["attempt_id"])}, {"retry_error_code": None}, {"retry_source_attempt_id": None}, {"retry_after": None}):
        with pytest.raises(IntegrityError), pg.begin() as session:
            job = session.get(Job, parse_id("job", claim["job_id"]))
            for key, value in changes.items():
                setattr(job, key, value)
            session.flush()


def test_retry_wait_is_visible_in_channel_filter_without_becoming_ready_candidate(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    page = JobAdministration(pg, test_settings).list(QueueQuery(channel_id=claim["channel"]["id"]))
    assert len(page["data"]) == 1 and page["data"][0]["status"] == "retry_wait"
    assert page["data"][0]["retry"]["count"] == 1


def test_retry_wait_priority_is_revision_guarded_and_does_not_change_backoff(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    with pg.begin() as session:
        session.add(AdminUser(username="retry-admin", password_hash=password_hash("Synthetic-retry-admin-password"), role="administrator"))
    auth = AuthRepository(pg, test_settings)
    login = auth.login("retry-admin", "Synthetic-retry-admin-password", "127.0.0.1", "synthetic")
    actor = auth.admin(login["token"], write=False, origin=None, csrf=None)
    administration = JobAdministration(pg, test_settings)
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        revision, eligible = job.revision, job.retry_after
    request = JobPriority(expected_revision=revision, priority=20)
    changed = administration.priority(actor, parse_id("job", claim["job_id"]), request, "synthetic-priority-token", "synthetic")
    assert changed["priority"] == 20 and changed["status"] == "retry_wait"
    assert administration.priority(actor, parse_id("job", claim["job_id"]), request, "synthetic-priority-token", "synthetic") == changed
    with pytest.raises(DomainError):
        administration.priority(actor, parse_id("job", claim["job_id"]), request, "synthetic-stale-priority", "synthetic")
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).retry_after == eligible
    JobRepository(pg).cancel(catalog["identity"], parse_id("job", claim["job_id"]), "synthetic-cancel-after-priority")
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Origin"] = test_settings.public_origin
        client.post("/admin/api/v1/session", json={"username": "retry-admin", "password": "Synthetic-retry-admin-password"}).raise_for_status()
        result = client.get("/admin/api/v1/jobs/" + claim["job_id"])
        result.raise_for_status()
        detail = result.json()
        assert detail["status"] == "cancelled" and detail["retry"]["next_at"] is None
        assert set(detail["retry"]) == {"count", "limit", "next_at", "last_error"}
        assert "input" not in detail


@pytest.mark.parametrize("guard", ["deadline", "validation", "cancel", "intent", "submitted"])
def test_ineligible_confirmed_failure_is_terminal_without_internal_retry(pg, catalog, test_settings, guard):
    _, leases, claim = setup(pg, catalog, test_settings)
    if guard in ("deadline", "validation"):
        with pg.begin() as session:
            job = session.get(Job, parse_id("job", claim["job_id"]))
            if guard == "deadline":
                job.deadline = utcnow() + timedelta(seconds=4)
            else:
                job.is_validation = True
    if guard == "cancel":
        JobRepository(pg).cancel(catalog["identity"], parse_id("job", claim["job_id"]), "synthetic-cancel")
    sequence = 2
    if guard in ("intent", "submitted"):
        preparing = event(catalog, claim, sequence=1).model_copy(update={"type": "preparing", "payload": {"execution_digest": claim["execution_digest"]}})
        intent = event(catalog, claim, sequence=2).model_copy(update={"type": "submit_intent", "payload": {"association_id": str(uuid4()), "workflow_digest": claim["workflow_digest"], "journal_committed": True}})
        leases.event(catalog["node_id"], preparing)
        leases.event(catalog["node_id"], intent)
        sequence = 3
        if guard == "submitted":
            submitted = event(catalog, claim, sequence=3).model_copy(update={"type": "submitted", "payload": {"prompt_id": "synthetic-submitted"}})
            leases.event(catalog["node_id"], submitted)
            sequence = 4
    outcome = leases.event(catalog["node_id"], event(catalog, claim, sequence=sequence))
    assert outcome["status"] == ("cancelled" if guard == "cancel" else "failed")
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        assert job.retry_count == 0 and job.retry_after is None
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.scalar(select(UsageLedger.image_count)) == 0


def test_test_database_cleanup_preserves_relation_files_and_enforces_foreign_keys(pg, catalog, test_settings):
    _, leases, claim = setup(pg, catalog, test_settings)
    leases.event(catalog["node_id"], event(catalog, claim))
    with pg.begin() as session:
        gateway = GatewayService(name="Synthetic cleanup guard", issuer="sub2api", allowed_cidrs=["127.0.0.1/32"], credential_identifier="synthetic-cleanup", credential_verifier="0" * 64)
        session.add(gateway)
        session.flush()
        gateway_id = gateway.id
        session.add(GatewayRequestFence(gateway_id=gateway_id, provider_request_id="synthetic-cleanup", external_task_ref="synthetic-cleanup-task", price_version="synthetic-v1", authorization_ref="synthetic-authorization", external_tenant_ref="synthetic-tenant", external_key_ref="synthetic-key"))
    query = text("SELECT c.relname,pg_relation_filenode(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'")
    triggers = text("SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,t.tgname")
    with pg() as session:
        before = dict(session.execute(query).all())
        before_triggers = session.execute(triggers).all()
        assert session.get(Job, parse_id("job", claim["job_id"])).retry_source_attempt_id is not None
    with pytest.raises(IntegrityError, match="gateway_request_fence_immutable"), pg.begin() as session:
        session.execute(GatewayRequestFence.__table__.delete())
    with pytest.raises(IntegrityError) as foreign_key, pg.begin() as session:
        session.execute(GatewayService.__table__.delete().where(GatewayService.id == gateway_id))
    assert foreign_key.value.orig.sqlstate == "23503"
    reset_test_database(pg)
    with pg() as session:
        assert dict(session.execute(query).all()) == before
        assert session.execute(triggers).all() == before_triggers
        assert all(session.scalar(select(func.count()).select_from(table)) == 0 for table in Base.metadata.sorted_tables)


def test_test_cleanup_refuses_main_database_before_any_write(test_settings):
    url = make_url(test_settings.database_url.get_secret_value()).set(database="render_control")
    # The server enforces read-only transactions even if this guard regresses.
    engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=5000", "connect_timeout": 3})
    writes = []

    @sqlalchemy_event.listens_for(engine, "before_cursor_execute")
    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith(("update", "delete", "truncate", "insert", "alter", "create", "drop")):
            writes.append(statement.split()[0])

    try:
        with pytest.raises(RuntimeError, match="Refusing test cleanup"):
            reset_test_database(sessionmaker(engine))
        assert not writes
    finally:
        engine.dispose()
