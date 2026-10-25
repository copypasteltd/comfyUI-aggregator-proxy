from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import capacity_sample
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.retries import OOM_CODE, RETRY_CODE
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.models import (
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    OutboxEvent,
    PublicModel,
    QuotaBucket,
    SpecIsolation,
    UsageLedger,
    WorkerInbox,
    WorkflowBinding,
)
from generation_infrastructure.retries import promote_due_retries
from generation_infrastructure.security import SecretVault
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration
REQUEST = {
    "model": "synthetic-image",
    "task_type": "text_to_image",
    "input": {"prompt": "Synthetic confirmed retry"},
}


def alternative(pg, catalog, *, other_node=True):
    with pg.begin() as session:
        original = session.get(Node, catalog["node_id"])
        if other_node:
            node = Node(
                name="Synthetic alternative worker",
                credential_identifier=uuid4().hex,
                credential_verifier="0" * 64,
                current_boot_id=uuid4(),
                observed_at=utcnow(),
            )
            node.inventory = {
                "capacity_sequence": 1,
                "capacity": {
                    "boot_id": str(node.current_boot_id),
                    "observed_at_ms": int(utcnow().timestamp() * 1000),
                    "sample": capacity_sample(),
                },
            }
            session.add(node)
            session.flush()
        else:
            node = original
        gpu = Gpu(
            node_id=node.id,
            hardware_id="SYNTHETIC-ALTERNATIVE-" + uuid4().hex,
            name="Protocol alternative",
        )
        session.add(gpu)
        session.flush()
        channel = Channel(
            tenant_id=catalog["identity"].tenant_id,
            project_id=catalog["identity"].project_id,
            node_id=node.id,
            gpu_id=gpu.id,
            name="Synthetic alternate channel",
            base_url="http://127.0.0.1:18219",
            enabled=True,
            desired_revision=1,
            applied_revision=1,
            verified_revision=1,
            observed_at=utcnow(),
        )
        session.add(channel)
        session.flush()
        session.add(
            WorkflowBinding(
                tenant_id=channel.tenant_id,
                project_id=channel.project_id,
                channel_id=channel.id,
                release_id=catalog["release_id"],
                desired_digest=catalog["release_digest"],
                applied_digest=catalog["release_digest"],
                verified_digest=catalog["release_digest"],
                observed_at=utcnow(),
            )
        )
        model = session.scalar(select(PublicModel).where(PublicModel.alias == "synthetic-image"))
        model.channel_ids = [*model.channel_ids, public_id("ch", channel.id)]
        return {
            "node_id": node.id,
            "boot_id": node.current_boot_id,
            "gpu_id": gpu.id,
            "channel_id": channel.id,
        }


def emit(repo, claim, kind, payload, seq):
    event = WorkerEvent(
        protocol_version="0.4.0",
        event_id=str(uuid4()),
        node_id=claim["node_id"],
        boot_id=claim["boot_id"],
        job_id=claim["job_id"],
        attempt_id=claim["attempt_id"],
        fencing_token=claim["fencing_token"],
        journal_seq=seq,
        observed_at=int(utcnow().timestamp()),
        type=kind,
        payload=payload,
    )
    return event, repo.event(parse_id("node", claim["node_id"]), event)


def begin(pg, catalog, settings):
    jobs = JobRepository(pg)
    result = jobs.create(catalog["identity"], REQUEST, str(uuid4()), "synthetic-confirmed-retry")
    repo = LeaseRepository(
        pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True
    )
    claim = repo.claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == result["id"]
    return jobs, repo, claim


def running(repo, claim):
    association, prompt = str(uuid4()), "synthetic-prompt-" + uuid4().hex
    emit(repo, claim, "preparing", {"execution_digest": claim["execution_digest"]}, 1)
    emit(
        repo,
        claim,
        "submit_intent",
        {
            "association_id": association,
            "workflow_digest": claim["workflow_digest"],
            "journal_committed": True,
        },
        2,
    )
    emit(repo, claim, "submitted", {"prompt_id": prompt}, 3)
    proof = {
        "kind": "comfy-oom-v1",
        "prompt_id": prompt,
        "association_id": association,
        "exception_type": "torch.OutOfMemoryError",
        "history_digest": "a" * 64,
        "queue_digest": "b" * 64,
        "queue_absent": True,
    }
    return {
        "error_code": OOM_CODE,
        "execution_stop_confirmed": True,
        "evidence_digest": fingerprint(proof),
        "retry_evidence": proof,
    }


def due(pg, claim):
    with pg.begin() as session:
        session.get(Job, parse_id("job", claim["job_id"])).retry_after = utcnow() - timedelta(
            seconds=1
        )


def fresh(repo, target):
    repo.heartbeat(target["node_id"], target["boot_id"], [], capacity_sample(sequence=2))


@pytest.mark.parametrize("other_node", [True, False])
def test_oom_crosses_physical_gpu_preserves_job_and_records_failure(
    pg, catalog, test_settings, other_node
):
    target = alternative(pg, catalog, other_node=other_node)
    if not other_node:
        with pg.begin() as session:
            session.get(Gpu, target["gpu_id"]).enabled = False
    jobs, repo, claim = begin(pg, catalog, test_settings)
    if not other_node:
        with pg.begin() as session:
            session.get(Gpu, target["gpu_id"]).enabled = True
    before = jobs.get(catalog["identity"], parse_id("job", claim["job_id"]))
    payload = running(repo, claim)
    failure, response = emit(repo, claim, "execution_failed", payload, 4)
    assert response["status"] == "retry_wait"
    assert repo.event(catalog["node_id"], failure) == response
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        source = session.get(Attempt, parse_id("att", claim["attempt_id"]))
        assert (
            job.current_attempt_id is None
            and job.retry_count == 1
            and job.retry_error_code == OOM_CODE
        )
        assert (
            source.closed_at
            and source.generation_released_at
            and source.generation_evidence["release"] == payload
        )
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is None
        state = session.scalar(
            select(SpecIsolation).where(SpecIsolation.gpu_id == catalog["gpu_id"])
        )
        assert state.failure_count == 1 and state.last_error_code == OOM_CODE
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(WorkerInbox)
                .where(WorkerInbox.event_id == failure.event_id)
            )
            == 1
        )
    assert promote_due_retries(pg) == 0
    due(pg, claim)
    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(lambda _: promote_due_retries(pg), range(4))) == 1
    fresh(repo, target)
    if other_node:
        assert repo.claim(catalog["node_id"], catalog["boot_id"]) is None
    second = repo.claim(target["node_id"], target["boot_id"])
    assert second and second["channel"]["gpu_id"] == public_id("gpu", target["gpu_id"])
    assert (
        second["execution"] == claim["execution"]
        and second["execution_digest"] == claim["execution_digest"]
    )
    assert second["job_id"] == claim["job_id"] and second["attempt_id"] != claim["attempt_id"]
    assert (
        jobs.get(catalog["identity"], parse_id("job", claim["job_id"]))["created_at"]
        == before["created_at"]
    )


def test_mixed_oom_and_preparation_share_budget_and_never_return_to_oom_gpu(
    pg, catalog, test_settings
):
    target = alternative(pg, catalog)
    _, repo, claim = begin(pg, catalog, test_settings)
    emit(repo, claim, "execution_failed", running(repo, claim), 4)
    due(pg, claim)
    assert promote_due_retries(pg) == 1
    second = repo.claim(target["node_id"], target["boot_id"])
    assert second
    emit(
        repo,
        second,
        "execution_failed",
        {"error_code": RETRY_CODE, "execution_stop_confirmed": True, "evidence_digest": "c" * 64},
        1,
    )
    due(pg, second)
    assert promote_due_retries(pg) == 1
    assert repo.claim(catalog["node_id"], catalog["boot_id"]) is None
    third = repo.claim(target["node_id"], target["boot_id"])
    assert third and third["job_id"] == claim["job_id"] and third["execution"] == claim["execution"]
    emit(
        repo,
        third,
        "execution_failed",
        {"error_code": RETRY_CODE, "execution_stop_confirmed": True, "evidence_digest": "d" * 64},
        1,
    )
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        assert job.state == "failed" and job.retry_count == 2
        assert session.scalar(select(func.count()).select_from(Attempt)) == 3
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        counters = list(
            session.scalars(select(QuotaBucket).where(QuotaBucket.scope_kind == "3_key"))
        )
        assert next(b for b in counters if b.period == "active").unfinished_jobs == 0
        assert next(b for b in counters if b.period == job.accepted_period).daily_jobs == 1


def test_second_oom_is_terminal_even_when_a_third_gpu_exists(pg, catalog, test_settings):
    target = alternative(pg, catalog)
    alternative(pg, catalog)
    _, repo, claim = begin(pg, catalog, test_settings)
    emit(repo, claim, "execution_failed", running(repo, claim), 4)
    due(pg, claim)
    assert promote_due_retries(pg) == 1
    second = repo.claim(target["node_id"], target["boot_id"])
    assert second
    _, outcome = emit(repo, second, "execution_failed", running(repo, second), 4)
    assert outcome["status"] == "failed"
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).retry_count == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert sorted(session.scalars(select(SpecIsolation.failure_count))) == [1, 1]


@pytest.mark.parametrize(
    "reason",
    [
        "absent",
        "same_gpu_only",
        "disabled_gpu",
        "disabled_node",
        "disabled_channel",
        "draining",
        "unverified",
        "unapproved",
        "stale_node",
        "outside_frozen_routes",
        "no_capacity",
    ],
)
def test_unqualified_alternative_does_not_authorize_oom_retry(pg, catalog, test_settings, reason):
    target = alternative(pg, catalog) if reason not in ("absent", "same_gpu_only") else None
    _, repo, claim = begin(pg, catalog, test_settings)
    if target:
        with pg.begin() as session:
            channel, gpu, node = (
                session.get(model, target[key])
                for model, key in ((Channel, "channel_id"), (Gpu, "gpu_id"), (Node, "node_id"))
            )
            if reason == "disabled_gpu":
                gpu.enabled = False
            if reason == "disabled_node":
                node.enabled = False
            if reason == "disabled_channel":
                channel.enabled = False
            if reason == "draining":
                channel.draining = True
            if reason == "unverified":
                channel.verified_revision = None
            if reason == "unapproved":
                from generation_infrastructure.models import WorkflowRelease

                session.get(WorkflowRelease, catalog["release_id"]).validation = {
                    "test_only": False
                }
            if reason == "stale_node":
                node.observed_at = utcnow() - timedelta(seconds=16)
            if reason == "outside_frozen_routes":
                job = session.get(Job, parse_id("job", claim["job_id"]))
                job.eligible_channels = [
                    c for c in job.eligible_channels if c != public_id("ch", channel.id)
                ]
            if reason == "no_capacity":
                node.inventory = {}
    _, outcome = emit(repo, claim, "execution_failed", running(repo, claim), 4)
    assert outcome["status"] == "failed"
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).retry_count == 0
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1


@pytest.mark.parametrize("field", ["prompt_id", "association_id", "evidence_digest"])
def test_mismatched_proof_keeps_original_occupancy(pg, catalog, test_settings, field):
    alternative(pg, catalog)
    _, repo, claim = begin(pg, catalog, test_settings)
    payload = running(repo, claim)
    if field == "evidence_digest":
        payload[field] = "0" * 64
    else:
        payload["retry_evidence"][field] = str(uuid4())
        payload["evidence_digest"] = fingerprint(payload["retry_evidence"])
    with pytest.raises(DomainError, match="当前执行尝试"):
        emit(repo, claim, "execution_failed", payload, 4)
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).state == "running"
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is not None


@pytest.mark.parametrize("guard", ["legacy", "unknown_stop", "cancel", "validation", "deadline"])
def test_oom_guards_keep_existing_safe_semantics(pg, catalog, test_settings, guard):
    alternative(pg, catalog)
    jobs, repo, claim = begin(pg, catalog, test_settings)
    payload = running(repo, claim)
    if guard in ("legacy", "unknown_stop"):
        payload.pop("retry_evidence")
        if guard == "unknown_stop":
            payload["execution_stop_confirmed"] = False
    if guard == "cancel":
        jobs.cancel(catalog["identity"], parse_id("job", claim["job_id"]), "synthetic-cancel")
    if guard in ("validation", "deadline"):
        with pg.begin() as session:
            job = session.get(Job, parse_id("job", claim["job_id"]))
            if guard == "validation":
                job.is_validation = True
            else:
                job.deadline = utcnow() + timedelta(seconds=4)
    _, result = emit(repo, claim, "execution_failed", payload, 4)
    assert result["status"] == {"unknown_stop": "reconciling", "cancel": "cancelled"}.get(
        guard, "failed"
    )
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).retry_count == 0
        assert (session.get(GpuOccupancy, catalog["gpu_id"]) is not None) is (
            guard == "unknown_stop"
        )


def test_old_writer_cannot_insert_another_attempt_on_failed_physical_gpu(
    pg, catalog, test_settings
):
    alternative(pg, catalog)
    _, repo, claim = begin(pg, catalog, test_settings)
    emit(repo, claim, "execution_failed", running(repo, claim), 4)
    due(pg, claim)
    assert promote_due_retries(pg) == 1
    for channel_id in catalog["channel_ids"]:
        with (
            pytest.raises(IntegrityError, match="retry_failed_physical_gpu"),
            pg.begin() as session,
        ):
            session.add(
                Attempt(
                    job_id=parse_id("job", claim["job_id"]),
                    channel_id=channel_id,
                    channel_snapshot={},
                    node_id=catalog["node_id"],
                    boot_id=catalog["boot_id"],
                    gpu_id=catalog["gpu_id"],
                    fence=999,
                    lease_expires_at=utcnow() + timedelta(seconds=60),
                )
            )
            session.flush()


def test_concurrent_duplicate_oom_and_cancel_due_race_commit_once(pg, catalog, test_settings):
    alternative(pg, catalog)
    jobs, repo, claim = begin(pg, catalog, test_settings)
    payload = running(repo, claim)
    event, expected = emit(repo, claim, "execution_failed", payload, 4)
    with ThreadPoolExecutor(4) as pool:
        assert (
            list(pool.map(lambda _: repo.event(catalog["node_id"], event), range(4)))
            == [expected] * 4
        )
    due(pg, claim)
    with ThreadPoolExecutor(4) as pool:
        work = [
            pool.submit(
                jobs.cancel,
                catalog["identity"],
                parse_id("job", claim["job_id"]),
                "synthetic-cancel",
            ),
            *[pool.submit(promote_due_retries, pg) for _ in range(3)],
        ]
        [item.result() for item in work]
    with pg() as session:
        job = session.get(Job, parse_id("job", claim["job_id"]))
        assert job.state == "cancelled" and job.retry_count == 1 and job.retry_after is None
        assert session.scalar(select(SpecIsolation.failure_count)) == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1


def test_approval_revoked_while_waiting_cannot_claim_and_deadline_settles_once(
    pg, catalog, test_settings
):
    target = alternative(pg, catalog)
    _, repo, claim = begin(pg, catalog, test_settings)
    emit(repo, claim, "execution_failed", running(repo, claim), 4)
    due(pg, claim)
    assert promote_due_retries(pg) == 1
    with pg.begin() as session:
        session.get(Channel, target["channel_id"]).enabled = False
    assert repo.claim(target["node_id"], target["boot_id"]) is None
    assert repo.claim(catalog["node_id"], catalog["boot_id"]) is None
    with pg.begin() as session:
        session.get(Job, parse_id("job", claim["job_id"])).deadline = utcnow() - timedelta(
            seconds=1
        )
    assert Maintenance(pg).tick()["queue_expired"] == 1
    assert Maintenance(pg).tick()["queue_expired"] == 0
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.kind == "gateway.settlement")
            )
            == 0
        )


@pytest.mark.parametrize("last_reason_is_preparation", [False, True])
def test_downgrade_refuses_oom_retry_facts_even_after_later_preparation_failure(pg, catalog, test_settings, last_reason_is_preparation):
    target = alternative(pg, catalog)
    _, repo, claim = begin(pg, catalog, test_settings)
    emit(repo, claim, "execution_failed", running(repo, claim), 4)
    if last_reason_is_preparation:
        due(pg, claim)
        assert promote_due_retries(pg) == 1
        second = repo.claim(target["node_id"], target["boot_id"])
        assert second
        emit(repo, second, "execution_failed", {"error_code": RETRY_CODE, "execution_stop_confirmed": True, "evidence_digest": "c" * 64}, 1)
    path = Path(__file__).resolve().parents[2] / "migrations/versions/2c9f1d6a4830_confirmed_failure_retry.py"
    spec = spec_from_file_location("confirmed_retry_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with pg() as session:
        schema_before = session.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError, match="forward recovery"), pg.begin() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.downgrade()
    with pg() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == schema_before
        assert session.scalar(text("SELECT tgenabled FROM pg_trigger WHERE tgname='enforce_retry_gpu_fence' AND NOT tgisinternal")) == "O"


@pytest.mark.parametrize("mode", ["alternative", "no_alternative", "deadline", "cancel", "legacy"])
def test_reconciled_confirmed_failure_uses_its_evidence_and_correct_terminal_transition(pg, catalog, test_settings, mode):
    if mode != "no_alternative":
        alternative(pg, catalog)
    jobs, repo, claim = begin(pg, catalog, test_settings)
    payload = running(repo, claim)
    if mode == "cancel":
        jobs.cancel(catalog["identity"], parse_id("job", claim["job_id"]), "synthetic-reconcile-cancel")
    if mode == "deadline":
        with pg.begin() as session:
            session.get(Job, parse_id("job", claim["job_id"])).deadline = utcnow() + timedelta(seconds=4)
    if mode == "legacy":
        payload.pop("retry_evidence")
    _, unknown = emit(repo, claim, "reconcile_observation", {"evidence_digest": "d" * 64, "queue_observation": "unknown", "process_boot_changed": False}, 4)
    assert unknown["status"] == "reconciling"
    _, result = emit(repo, claim, "execution_failed", payload, 5)
    assert result["status"] == {"alternative": "retry_wait", "cancel": "cancelled"}.get(mode, "failed")
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is None
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == (0 if mode == "alternative" else 1)


@pytest.mark.parametrize("drift", ["none", "profile", "approval", "binding_digest", "hold", "isolation"])
def test_retry_requires_current_matching_runtime_approval_at_claim(pg, catalog, test_settings, drift):
    from generation_infrastructure.models import WorkflowRelease
    from generation_infrastructure.spec_isolation import state_for

    target = alternative(pg, catalog)
    with pg.begin() as session:
        original = session.get(WorkflowRelease, catalog["release_id"])
        gpu = session.get(Gpu, target["gpu_id"])
        source_gpu = session.get(Gpu, catalog["gpu_id"])
        manifest = {**original.manifest, "runtime_profiles": [{"id": "approved-alternative", "gpu_hardware_id": gpu.hardware_id}, {"id": "approved-source", "gpu_hardware_id": source_gpu.hardware_id}]}
        release = WorkflowRelease(tenant_id=original.tenant_id, project_id=original.project_id, workflow_id=original.workflow_id,
                                  version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": False})
        session.add(release)
        session.flush()
        for channel_id in [*catalog["channel_ids"], target["channel_id"]]:
            session.add(WorkflowBinding(tenant_id=release.tenant_id, project_id=release.project_id, channel_id=channel_id,
                                        release_id=release.id, desired_digest=release.digest, applied_digest=release.digest,
                                        verified_digest=release.digest, runtime_approval={"profile_id": "approved-alternative" if channel_id == target["channel_id"] else "approved-source", "evidence_id": "synthetic-only"}))
        session.scalar(select(PublicModel).where(PublicModel.alias == "synthetic-image")).release_id = release.id
        catalog["release_id"], catalog["release_digest"] = release.id, release.digest
    _, repo, claim = begin(pg, catalog, test_settings)
    assert emit(repo, claim, "execution_failed", running(repo, claim), 4)[1]["status"] == "retry_wait"
    due(pg, claim)
    assert promote_due_retries(pg) == 1
    if drift != "none":
        with pg.begin() as session:
            binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == target["channel_id"], WorkflowBinding.release_id == catalog["release_id"]))
            if drift == "profile":
                binding.runtime_approval = {"profile_id": "wrong-hardware-profile"}
            elif drift == "approval":
                binding.runtime_approval = None
            elif drift == "binding_digest":
                binding.desired_digest = binding.applied_digest = binding.verified_digest = "f" * 64
            elif drift == "hold":
                session.get(Gpu, target["gpu_id"]).management_hold = uuid4()
            elif drift == "isolation":
                gpu = session.execute(select(Gpu).where(Gpu.id == target["gpu_id"]).with_for_update()).scalar_one()
                state = state_for(session, session.get(Job, parse_id("job", claim["job_id"])), gpu)
                state.is_active, state.isolated_at, state.reason = True, utcnow(), "manual_investigation"
    next_claim = repo.claim(target["node_id"], target["boot_id"])
    assert bool(next_claim) is (drift == "none")
