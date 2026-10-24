from __future__ import annotations

import importlib.util
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from threading import Event, current_thread
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import capacity_sample
from generation_application.ports import BusinessIdentity
from generation_contracts.defaults import default_key_policy, default_limits
from generation_contracts.dispatch import DispatchPolicy
from generation_contracts.queue_queries import QueueQuery
from generation_contracts.validation import WorkerEvent
from generation_domain.dispatch import estimated_service
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.models import (
    ApiKey,
    Attempt,
    AuditEvent,
    Channel,
    ClaimRequest,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    Project,
    PublicModel,
    QuotaBucket,
    Tenant,
    UsageLedger,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.spec_isolation import state_for
from generation_infrastructure.transactions import GLOBAL_ID
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration


def refresh_node(pg, catalog, **capacity):
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        node.observed_at = utcnow()
        node.inventory = {"capacity_sequence": 1, "capacity": {"boot_id": str(node.current_boot_id), "observed_at_ms": int(utcnow().timestamp() * 1000), "sample": capacity_sample(**capacity)}}


def clone_tenant(pg, catalog, *, task_type="text_to_image", manifest_extra=None):
    """Synthetic scoped catalog on the same physical node/GPU; no real approval."""
    with pg.begin() as session:
        tenant = Tenant(name="Synthetic competing tenant", limits=default_limits())
        session.add(tenant)
        session.flush()
        project = Project(tenant_id=tenant.id, name="Synthetic competing project", limits=default_limits())
        session.add(project)
        session.flush()
        policy = default_key_policy()
        for selection in ("models", "workflow_releases", "channels"):
            policy[selection] = {"mode": "all_authorized", "selected_ids": []}
        key = ApiKey(tenant_id=tenant.id, project_id=project.id, name="Synthetic competing key", policy=policy)
        workflow = Workflow(tenant_id=tenant.id, project_id=project.id, name="Synthetic competing workflow", task_type=task_type, draft={})
        session.add_all([key, workflow])
        session.flush()
        manifest = {**deepcopy(session.get(WorkflowRelease, catalog["release_id"]).manifest), "task_type": task_type, **(manifest_extra or {})}
        release = WorkflowRelease(tenant_id=tenant.id, project_id=project.id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        channel = Channel(tenant_id=tenant.id, project_id=project.id, node_id=catalog["node_id"], gpu_id=catalog["gpu_id"], name="Synthetic competing channel", base_url="http://127.0.0.1:18190", enabled=True, desired_revision=1, applied_revision=1, verified_revision=1)
        session.add_all([release, channel])
        session.flush()
        session.add(WorkflowBinding(tenant_id=tenant.id, project_id=project.id, channel_id=channel.id, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest))
        session.add(PublicModel(tenant_id=tenant.id, project_id=project.id, alias="synthetic-image", task_type=task_type, release_id=release.id, channel_ids=[public_id("ch", channel.id)], defaults={"size": "1024x1024", "quality": "standard"}, published=True))
        return {**catalog, "identity": BusinessIdentity(tenant.id, project.id, key.id, policy), "channel_ids": [channel.id], "release_id": release.id, "release_digest": release.digest, "task_type": task_type}


def create_job(pg, catalog, *, priority=0, wait=0):
    request = {"model": "synthetic-image", "task_type": catalog.get("task_type", "text_to_image"), "input": {"prompt": "Synthetic fair dispatch"}}
    if request["task_type"].endswith("video"):
        request["parameters"] = {"seconds": 5}
    created = JobRepository(pg).create(catalog["identity"], request, str(uuid4()), "synthetic-fairness")
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", created["id"]))
        job.priority = priority
        if wait:
            job.created_at = utcnow() - timedelta(seconds=wait)
    return created["id"]


def leases(pg, settings):
    return LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)


def finish_preparation(repository, catalog, claim, *, error_code="preparation_failed"):
    event = WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=public_id("node", catalog["node_id"]), boot_id=str(catalog["boot_id"]), job_id=claim["job_id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=2, observed_at=int(utcnow().timestamp()), type="execution_failed", payload={"error_code": error_code, "execution_stop_confirmed": True, "evidence_digest": "b" * 64})
    return repository.event(catalog["node_id"], event)


def global_cursor(session):
    return session.get(QuotaBucket, ("0_global", GLOBAL_ID, "active"))


def test_persistent_cost_share_across_recreated_repositories(pg, catalog, test_settings):
    other = clone_tenant(pg, catalog, task_type="text_to_video")
    for _ in range(10):
        create_job(pg, catalog, priority=100)
    for _ in range(4):
        create_job(pg, other, priority=-100)
    selected = []
    for _ in range(12):
        refresh_node(pg, catalog)
        repository = leases(pg, test_settings)
        claim = repository.claim(catalog["node_id"], catalog["boot_id"])
        assert claim
        with pg() as session:
            attempt = session.get(Attempt, parse_id("att", claim["attempt_id"]))
            job = session.get(Job, attempt.job_id)
            selected.append(job.tenant_id)
            assert attempt.channel_snapshot["dispatch"]["estimated_service_milliseconds"] == estimated_service(job.task_type, None)[0]
        finish_preparation(repository, catalog, claim)
    assert selected.count(catalog["identity"].tenant_id) == 10
    assert selected.count(other["identity"].tenant_id) == 2
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 12
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "job.claimed")) == 12
        for identity in (catalog["identity"], other["identity"]):
            assert session.get(QuotaBucket, ("1_tenant", identity.tenant_id, "active")).dispatch_virtual_milliseconds == 600_000


def test_claim_refreshes_locked_counters_after_concurrent_generation_release(pg, catalog, test_settings, monkeypatch):
    import generation_infrastructure.leases as module

    identity = catalog["identity"]
    with pg.begin() as session:
        gpu = Gpu(node_id=catalog["node_id"], hardware_id="SYNTHETIC-RELEASE-RACE", name="Synthetic race GPU")
        session.add(gpu)
        session.flush()
        channel = Channel(tenant_id=identity.tenant_id, project_id=identity.project_id, node_id=catalog["node_id"], gpu_id=gpu.id, name="Synthetic race channel", base_url="http://127.0.0.1:18191", enabled=True, desired_revision=1, applied_revision=1, verified_revision=1)
        session.add(channel)
        session.flush()
        session.add(WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel.id, release_id=catalog["release_id"], desired_digest=catalog["release_digest"], applied_digest=catalog["release_digest"], verified_digest=catalog["release_digest"]))
        model = session.scalar(select(PublicModel).where(PublicModel.tenant_id == identity.tenant_id))
        model.channel_ids = [*model.channel_ids, public_id("ch", channel.id)]
    first_id, second_id = create_job(pg, catalog), create_job(pg, catalog)
    repository = leases(pg, test_settings)
    first = repository.claim(catalog["node_id"], catalog["boot_id"])
    assert first and first["job_id"] == first_id
    selected, released = Event(), Event()
    original = module.lock_buckets

    def pause_claim_before_lock(*args, **kwargs):
        if current_thread().name.startswith("dispatch-race"):
            cached = args[0].get(QuotaBucket, ("0_global", GLOBAL_ID, "active"))
            assert cached.generating_jobs == 1
            selected.set()
            assert released.wait(10), "concurrent release did not complete"
            locked = original(*args, **kwargs)
            assert locked["0_global", "active"] is cached
            return locked
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "lock_buckets", pause_claim_before_lock)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="dispatch-race") as executor:
        pending = executor.submit(repository.claim, catalog["node_id"], catalog["boot_id"])
        try:
            assert selected.wait(10), "claim did not reach the live-counter lock"
            assert finish_preparation(repository, catalog, first)["status"] == "failed"
        finally:
            released.set()
        second = pending.result(timeout=15)
    assert second and second["job_id"] == second_id
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GpuOccupancy)) == 1
        assert session.get(Attempt, parse_id("att", first["attempt_id"])).closed_at is not None
        assert global_cursor(session).dispatch_sequence == 2
        for kind, identifier in (("0_global", GLOBAL_ID), ("1_tenant", identity.tenant_id), ("2_project", identity.project_id), ("3_key", identity.key_id)):
            assert session.get(QuotaBucket, (kind, identifier, "active")).generating_jobs == 1


def test_same_tenant_old_low_priority_overtakes_new_high_priority(pg, catalog, test_settings):
    old = create_job(pg, catalog, priority=-100, wait=1000)
    create_job(pg, catalog, priority=100)
    refresh_node(pg, catalog)
    assert leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])["job_id"] == old


def test_overdue_tenants_rotate_even_with_large_service_debt(pg, catalog, test_settings):
    other = clone_tenant(pg, catalog)
    first = create_job(pg, catalog, wait=400)
    second = create_job(pg, other, wait=350)
    with pg.begin() as session:
        for identity, service, sequence in ((catalog["identity"], 7_200_000, 7), (other["identity"], 60_000, 8)):
            tenant = session.get(QuotaBucket, ("1_tenant", identity.tenant_id, "active"))
            tenant.dispatch_virtual_milliseconds, tenant.dispatch_sequence = service, sequence
        global_cursor(session).dispatch_sequence = 8
    refresh_node(pg, catalog)
    repository = leases(pg, test_settings)
    claim = repository.claim(catalog["node_id"], catalog["boot_id"])
    assert claim["job_id"] == first
    finish_preparation(repository, catalog, claim)
    assert repository.claim(catalog["node_id"], catalog["boot_id"])["job_id"] == second


@pytest.mark.parametrize("gate", ["quota", "channel_disabled", "unverified", "binding", "unapproved", "spec_isolation"])
def test_ineligible_overdue_tenant_does_not_block_another(pg, catalog, test_settings, gate):
    other = clone_tenant(pg, catalog)
    blocked = create_job(pg, catalog, priority=100, wait=400)
    expected = create_job(pg, other, priority=-100)
    with pg.begin() as session:
        if gate == "quota":
            key = session.get(ApiKey, catalog["identity"].key_id)
            policy = deepcopy(key.policy)
            policy["limits"]["generating_jobs"] = {"mode": "bounded", "value": 0}
            key.policy = policy
        elif gate == "unapproved":
            session.get(WorkflowRelease, catalog["release_id"]).validation = {"test_only": False}
        elif gate == "spec_isolation":
            state = state_for(session, session.get(Job, parse_id("job", blocked)), session.get(Gpu, catalog["gpu_id"]))
            state.is_active, state.isolated_at, state.reason = True, utcnow(), "manual_investigation"
        else:
            for channel in session.scalars(select(Channel).where(Channel.id.in_(catalog["channel_ids"]))):
                if gate == "channel_disabled":
                    channel.enabled = False
                elif gate == "unverified":
                    channel.verified_revision = None
                else:
                    session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id)).verified_digest = None
    refresh_node(pg, catalog)
    claim = leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
    assert claim["job_id"] == expected
    with pg() as session:
        assert session.get(Job, parse_id("job", blocked)).state == "queued"
        assert session.get(QuotaBucket, ("1_tenant", catalog["identity"].tenant_id, "active")).dispatch_sequence == 0
        assert global_cursor(session).dispatch_sequence == 1


@pytest.mark.parametrize("profile", ["matching", "wrong_gpu", "wrong_id", "unapproved"])
def test_only_matching_approved_profile_changes_the_dispatch_estimate(pg, catalog, test_settings, profile):
    extra = {"runtime_profiles": [{"id": "synthetic-profile", "gpu_hardware_id": "SYNTHETIC-GPU-1" if profile != "wrong_gpu" else "OTHER-GPU", "warm_p95_milliseconds": 12345}]}
    other = clone_tenant(pg, catalog, manifest_extra=extra)
    create_job(pg, other)
    with pg.begin() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == other["channel_ids"][0]))
        if profile != "unapproved":
            binding.runtime_approval = {"profile_id": "other-profile" if profile == "wrong_id" else "synthetic-profile"}
    refresh_node(pg, catalog)
    claim = leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
    with pg() as session:
        dispatch = session.get(Attempt, parse_id("att", claim["attempt_id"])).channel_snapshot["dispatch"]
        assert dispatch["estimated_service_milliseconds"] == (12345 if profile == "matching" else 60_000)
        assert dispatch["cost_source"] == ("approved_profile" if profile == "matching" else "image_baseline")


def test_duplicate_empty_and_closed_claims_never_double_charge(pg, catalog, test_settings):
    create_job(pg, catalog)
    create_job(pg, catalog)
    repository = leases(pg, test_settings)
    refresh_node(pg, catalog)
    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(lambda _: repository.claim(catalog["node_id"], catalog["boot_id"], "synthetic-shared-claim"), range(4)))
    assert len({outcome["attempt_id"] for outcome in outcomes}) == 1
    assert repository.claim(catalog["node_id"], catalog["boot_id"], "synthetic-empty") is None
    finish_preparation(repository, catalog, outcomes[0])
    assert repository.claim(catalog["node_id"], catalog["boot_id"], "synthetic-shared-claim")["closed"]
    assert repository.claim(catalog["node_id"], catalog["boot_id"], "synthetic-empty") is None
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 1
    assert repository.claim(catalog["node_id"], catalog["boot_id"], "synthetic-fresh")["attempt_id"] != outcomes[0]["attempt_id"]
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 2
        assert session.scalar(select(func.count()).select_from(Attempt)) == 2


def test_claim_failure_rolls_back_cursor_attempt_request_and_gpu(pg, catalog, test_settings, monkeypatch):
    create_job(pg, catalog)
    refresh_node(pg, catalog)

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic failure before claim commit")

    with monkeypatch.context() as patch:
        patch.setattr("generation_infrastructure.leases.record_change", fail)
        with pytest.raises(RuntimeError, match="before claim commit"):
            leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"], "synthetic-rolled-back")
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == global_cursor(session).dispatch_virtual_milliseconds == 0
        assert all(session.scalar(select(func.count()).select_from(model)) == 0 for model in (Attempt, GpuOccupancy, ClaimRequest))
        assert session.scalar(select(Job.state)) == "queued"
    assert leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"], "synthetic-rolled-back")
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 1


@pytest.mark.parametrize(("scope", "period", "virtual", "sequence"), [("1_tenant", "active", -1, 0), ("0_global", "active", 0, -1), ("3_key", "active", 1, 1), ("1_tenant", "2026-10-24", 1, 1), ("2_project", "active", 0, 1)])
def test_database_rejects_invalid_dispatch_cursors(pg, catalog, scope, period, virtual, sequence):
    identifiers = {"1_tenant": catalog["identity"].tenant_id, "2_project": catalog["identity"].project_id, "3_key": catalog["identity"].key_id, "0_global": GLOBAL_ID}
    with pytest.raises(IntegrityError), pg.begin() as session:
        session.add(QuotaBucket(scope_kind=scope, scope_id=identifiers[scope], period=period, dispatch_virtual_milliseconds=virtual, dispatch_sequence=sequence))


def migration():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/d682a0b14139_dispatch_fairness.py"
    spec = importlib.util.spec_from_file_location("dispatch_fairness_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_preserves_quotas_and_initializes_no_fabricated_history(pg, catalog):
    create_job(pg, catalog)
    with pg() as session:
        before = session.execute(text("SELECT scope_kind,scope_id,period,daily_jobs,unfinished_jobs,generating_jobs FROM quota_buckets ORDER BY scope_kind,scope_id,period")).all()
        with Operations.context(MigrationContext.configure(session.connection())):
            migration().downgrade()
            migration().upgrade()
        assert session.execute(text("SELECT scope_kind,scope_id,period,daily_jobs,unfinished_jobs,generating_jobs FROM quota_buckets ORDER BY scope_kind,scope_id,period")).all() == before
        assert session.execute(text("SELECT count(*) FROM quota_buckets WHERE dispatch_virtual_milliseconds <> 0 OR dispatch_sequence <> 0")).scalar_one() == 0


def test_migration_refuses_to_discard_committed_dispatch_history(pg, catalog, test_settings):
    create_job(pg, catalog)
    refresh_node(pg, catalog)
    leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
    with pytest.raises(DBAPIError, match="Dispatch history requires forward recovery"), pg() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            migration().downgrade()
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 1


def test_queue_snapshot_exposes_only_fixed_policy(pg, catalog, test_settings):
    snapshot = AdminQueries(pg, test_settings).queue_snapshot(QueueQuery())
    assert snapshot["dispatch_policy"] == DispatchPolicy.current().model_dump()
    assert "dispatch_sequence" not in str(snapshot) and "dispatch_virtual_milliseconds" not in str(snapshot)


def test_output_capacity_filter_precedes_overdue_fairness(pg, catalog, test_settings):
    heavy = clone_tenant(pg, catalog, manifest_extra={"output": {"max_bytes": 6 * 1024**3, "expected_count": 1}})
    blocked = create_job(pg, heavy, wait=400)
    expected = create_job(pg, catalog)
    refresh_node(pg, catalog, free_disk_bytes=10 * 1024**3)
    claim = leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
    assert claim["job_id"] == expected
    with pg() as session:
        assert session.get(Job, parse_id("job", blocked)).state == "queued"
        assert session.get(QuotaBucket, ("1_tenant", heavy["identity"].tenant_id, "active")).dispatch_sequence == 0


@pytest.mark.parametrize("gate", ["disabled", "management_hold"])
def test_unavailable_gpu_is_filtered_without_releasing_it(pg, catalog, test_settings, gate):
    with pg.begin() as session:
        available = Gpu(node_id=catalog["node_id"], hardware_id="SYNTHETIC-AVAILABLE", name="Synthetic available GPU")
        session.add(available)
        session.flush()
        other_gpu = available.id
    other = clone_tenant(pg, {**catalog, "gpu_id": other_gpu})
    blocked = create_job(pg, catalog, wait=400)
    expected = create_job(pg, other)
    with pg.begin() as session:
        gpu = session.get(Gpu, catalog["gpu_id"])
        if gate == "disabled":
            gpu.enabled = False
        else:
            gpu.management_hold = uuid4()
    refresh_node(pg, catalog)
    assert leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])["job_id"] == expected
    with pg() as session:
        assert session.get(Job, parse_id("job", blocked)).state == "queued"
        gpu = session.get(Gpu, catalog["gpu_id"])
        assert not gpu.enabled if gate == "disabled" else gpu.management_hold is not None


def test_key_disable_and_scope_edits_do_not_cancel_accepted_routing(pg, catalog, test_settings):
    expected = create_job(pg, catalog)
    with pg.begin() as session:
        key = session.get(ApiKey, catalog["identity"].key_id)
        key.enabled = False
        policy = deepcopy(key.policy)
        policy["channels"] = {"mode": "selected", "selected_ids": []}
        key.policy = policy
    refresh_node(pg, catalog)
    assert leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])["job_id"] == expected


def test_internal_retry_charges_each_actual_attempt_without_new_admission_or_usage(pg, catalog, test_settings):
    expected = create_job(pg, catalog)
    repository = leases(pg, test_settings)
    refresh_node(pg, catalog)
    first = repository.claim(catalog["node_id"], catalog["boot_id"])
    assert finish_preparation(repository, catalog, first, error_code="preparation_transport_failure")["status"] == "retry_wait"
    with pg.begin() as session:
        session.get(Job, parse_id("job", expected)).retry_after = utcnow() - timedelta(seconds=1)
        assert global_cursor(session).dispatch_sequence == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0
    assert Maintenance(pg).tick()["retry_queued"] == 1
    second = repository.claim(catalog["node_id"], catalog["boot_id"])
    assert second["job_id"] == expected and second["attempt_id"] != first["attempt_id"]
    assert finish_preparation(repository, catalog, second)["status"] == "failed"
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 2
        tenant = session.get(QuotaBucket, ("1_tenant", catalog["identity"].tenant_id, "active"))
        assert tenant.dispatch_virtual_milliseconds == 120_000
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        daily = list(session.scalars(select(QuotaBucket).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.period != "active")))
        assert len(daily) == 1 and daily[0].daily_jobs == 1 and daily[0].dispatch_sequence == daily[0].dispatch_virtual_milliseconds == 0


@pytest.mark.parametrize("field", ["dispatch_virtual_milliseconds", "dispatch_sequence"])
def test_counter_overflow_rejects_claim_and_rolls_back_all_new_facts(pg, catalog, test_settings, field):
    expected = create_job(pg, catalog)
    with pg.begin() as session:
        setattr(global_cursor(session), field, 2**63 - 1)
    refresh_node(pg, catalog)
    with pytest.raises(DomainError) as outcome:
        leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"], "synthetic-overflow")
    assert outcome.value.code == "dispatch_accounting_exhausted"
    with pg() as session:
        assert session.get(Job, parse_id("job", expected)).state == "queued"
        assert getattr(global_cursor(session), field) == 2**63 - 1
        assert all(session.scalar(select(func.count()).select_from(model)) == 0 for model in (Attempt, GpuOccupancy, ClaimRequest))


def test_global_dispatch_cursor_requires_the_real_global_scope_id(pg):
    with pytest.raises(IntegrityError), pg.begin() as session:
        session.add(QuotaBucket(scope_kind="0_global", scope_id=uuid4(), period="active", dispatch_sequence=1))


def test_binding_scan_budget_fails_closed_without_claiming_or_charging(pg, catalog, test_settings, monkeypatch):
    create_job(pg, catalog)
    refresh_node(pg, catalog)
    monkeypatch.setattr("generation_infrastructure.dispatch.MAX_ROUTE_MATCHES", 1)
    with pytest.raises(DomainError) as outcome:
        leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
    assert outcome.value.code == "dispatch_catalog_capacity"
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 0
        assert session.scalar(select(func.count()).select_from(Attempt)) == 0


def spawn_claim(database_url, encryption_key, node_id, boot_id, ready, start, results):
    engine = None
    try:
        url = make_url(database_url)
        assert (url.host, url.port, url.database) == ("127.0.0.1", 55438, "render_control_test")
        engine = create_engine(database_url, connect_args={"connect_timeout": 3, "options": "-c statement_timeout=30000 -c lock_timeout=15000"})
        repository = LeaseRepository(sessionmaker(engine, expire_on_commit=False), SecretVault(encryption_key), allow_synthetic_catalog=True)
        ready.put(True)
        if not start.wait(60):
            raise RuntimeError("Synthetic process start barrier timed out")
        claim = repository.claim(node_id, boot_id, "synthetic-process-" + uuid4().hex)
        results.put({"passed": claim is not None, "job_id": claim["job_id"] if claim else None})
    except Exception as exc:
        results.put({"passed": False, "error_type": type(exc).__name__})
    finally:
        if engine is not None:
            engine.dispose()


def test_separate_native_processes_share_one_fair_arbitration(pg, catalog, test_settings):
    other = clone_tenant(pg, catalog)
    with pg.begin() as session:
        for item in (catalog, other):
            identity = item["identity"]
            for record in (session.get(Tenant, identity.tenant_id), session.get(Project, identity.project_id)):
                limits = deepcopy(record.limits)
                limits["generating_jobs"]["value"] = 50
                record.limits = limits
            key = session.get(ApiKey, identity.key_id)
            policy = deepcopy(key.policy)
            policy["limits"]["generating_jobs"]["value"] = 50
            key.policy = policy
        for number in range(3):
            gpu = Gpu(node_id=catalog["node_id"], hardware_id=f"SYNTHETIC-FAIR-{number}", name="Synthetic competing GPU")
            session.add(gpu)
            session.flush()
            for item in (catalog, other):
                identity = item["identity"]
                channel = Channel(tenant_id=identity.tenant_id, project_id=identity.project_id, node_id=catalog["node_id"], gpu_id=gpu.id, name="Synthetic process channel", base_url="http://127.0.0.1:18191", enabled=True, desired_revision=1, applied_revision=1, verified_revision=1)
                session.add(channel)
                session.flush()
                session.add(WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel.id, release_id=item["release_id"], desired_digest=item["release_digest"], applied_digest=item["release_digest"], verified_digest=item["release_digest"]))
                model = session.scalar(select(PublicModel).where(PublicModel.tenant_id == identity.tenant_id))
                model.channel_ids = [*model.channel_ids, public_id("ch", channel.id)]
    for item in (catalog, other):
        for _ in range(4):
            create_job(pg, item, priority=100 if item is catalog else -100)
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(target=spawn_claim, args=(test_settings.database_url.get_secret_value(), test_settings.encryption_key.get_secret_value(), catalog["node_id"], catalog["boot_id"], ready, start, results)) for _ in range(4)]
    try:
        for process in processes:
            process.start()
        assert all(ready.get(timeout=60) is True for _ in processes)
        refresh_node(pg, catalog)
        start.set()
        outcomes = [results.get(timeout=60) for _ in processes]
        for process in processes:
            process.join(15)
        assert all(item["passed"] for item in outcomes), outcomes
        assert len({item["job_id"] for item in outcomes}) == 4
        assert all(process.exitcode == 0 for process in processes)
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(10)
        ready.close()
        results.close()
        ready.join_thread()
        results.join_thread()
    with pg() as session:
        assert global_cursor(session).dispatch_sequence == 4
        assert session.scalar(select(func.count()).select_from(GpuOccupancy)) == 4
        assert dict(session.execute(select(Job.tenant_id, func.count()).where(Job.state == "leased").group_by(Job.tenant_id)).all()) == {catalog["identity"].tenant_id: 2, other["identity"].tenant_id: 2}
        assert {row.channel_snapshot["dispatch"]["sequence"] for row in session.scalars(select(Attempt))} == {1, 2, 3, 4}
