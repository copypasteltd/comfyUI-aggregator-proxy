from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from generation_contracts.recovery import Reconciliation
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    Attempt,
    AuditEvent,
    Gpu,
    GpuOccupancy,
    Job,
    QuotaBucket,
    UsageLedger,
)
from generation_infrastructure.recovery import RecoveryRepository
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def prepare(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings) -> tuple[RecoveryRepository, LeaseRepository, dict[str, Any], dict[str, Any]]:
    job = JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "host recovery fixture"}}, str(uuid4()), "host-test")
    leases = LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    association = str(uuid4())
    for seq, (kind, payload) in enumerate([("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": association, "workflow_digest": claim["workflow_digest"], "journal_committed": True})], 1):
        leases.event(catalog["node_id"], WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=public_id("node", catalog["node_id"]), boot_id=claim["boot_id"], job_id=job["id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=seq, observed_at=int(utcnow().timestamp()), type=kind, payload=payload))
    with pg() as session:
        gpu = session.get(Gpu, catalog["gpu_id"])
        assert gpu
        hardware_id = gpu.hardware_id
    body = {"boot_id": claim["boot_id"], "attempt_id": claim["attempt_id"], "previous_boot_id": claim["boot_id"], "previous_fencing_token": claim["fencing_token"], "association_id": association, "prompt_id": None, "observation": "absent", "source": "unknown", "evidence_digest": "f" * 64, "host_checkpoint": {"protocol": "windows-host-uptime-reset-v1", "nonce": "a" * 32, "host_identity_digest": "a" * 64, "uptime_ms": 900000, "gpu_hardware_id": hardware_id, "channel_digest": fingerprint({"base_url": claim["channel"]["base_url"].rstrip("/")}), "locality": "same-host-tcp-v1"}}
    return RecoveryRepository(leases), leases, job, body


def stopped(body: dict[str, Any], digest: str, boot: str, **changes: Any) -> Reconciliation:
    proof = {"protocol": "windows-host-uptime-reset-v1", "checkpoint_digest": digest, "host_identity_digest": "a" * 64, "uptime_ms": 1000, "comfy_observation": "absent", **changes}
    observation = {"observation": "host_restarted", "source": "native_host_uptime", "prompt_id": body["prompt_id"], "host_restart": proof}
    return Reconciliation.model_validate({**body, **observation, "boot_id": boot, "host_checkpoint": None, "evidence_digest": fingerprint(observation)})


@pytest.mark.parametrize("cancel", [False, True])
def test_confirmed_host_restart_closes_once_and_keeps_original_attempt(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, cancel: bool) -> None:
    recovery, leases, job, body = prepare(pg, catalog, test_settings)
    result = recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(body))
    assert result["status"] == "attention_required" and not result["safe_to_resubmit"]
    assert recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(body)) == result
    if cancel:
        JobRepository(pg).cancel(catalog["identity"], parse_id("job", job["id"]), "cancel-before-restart")
    boot = uuid4()
    leases.register(catalog["node_id"], boot, {"gpus": []})
    proof = stopped(body, result["host_checkpoint_digest"], str(boot))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: recovery.reconcile(catalog["node_id"], proof), range(2)))
    target = "cancelled" if cancel else "failed"
    assert results == [{"status": "closed", "job_status": target}] * 2
    with pg() as session:
        attempt = session.get(Attempt, parse_id("att", body["attempt_id"]))
        stored = session.get(Job, parse_id("job", job["id"]))
        assert attempt and attempt.closed_at and attempt.generation_released_at and attempt.recovery_checkpoint
        assert stored and stored.current_attempt_id == attempt.id and stored.retry_count == 0
        assert stored.error_code == (None if cancel else "execution_host_restarted")
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is None
        assert session.scalar(select(func.count()).select_from(UsageLedger).where(UsageLedger.job_id == stored.id)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "recovery.host_restart_confirmed")) == 1
        for bucket in session.scalars(select(QuotaBucket)):
            if bucket.period == "active":
                assert bucket.generating_jobs == bucket.unfinished_jobs == 0
            else:
                assert bucket.daily_jobs == 1 and bucket.daily_image_count == 0
    with pytest.raises(DomainError):
        leases.renew(catalog["node_id"], catalog["boot_id"], parse_id("att", body["attempt_id"]), body["previous_fencing_token"])


@pytest.mark.parametrize("changes", [{"uptime_ms": 900001}, {"uptime_ms": 900000}, {"uptime_ms": 899001}, {"host_identity_digest": "c" * 64}, {"checkpoint_digest": "c" * 64}])
def test_missing_reset_or_mismatched_proof_keeps_occupancy(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, changes: dict[str, Any]) -> None:
    recovery, leases, job, body = prepare(pg, catalog, test_settings)
    result = recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(body))
    boot = uuid4()
    leases.register(catalog["node_id"], boot, {"gpus": []})
    with pytest.raises(DomainError):
        recovery.reconcile(catalog["node_id"], stopped(body, result["host_checkpoint_digest"], str(boot), **changes))
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is not None
        assert session.get(Job, parse_id("job", job["id"])).state == "reconciling"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_checkpoint_cannot_be_overwritten_or_used_after_prompt_resume(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    recovery, leases, job, body = prepare(pg, catalog, test_settings)
    result = recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(body))
    altered = {**body, "host_checkpoint": {**body["host_checkpoint"], "nonce": "b" * 32}}
    with pytest.raises(DomainError, match="禁止覆盖"):
        recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(altered))
    boot = uuid4()
    leases.register(catalog["node_id"], boot, {"gpus": []})
    found = Reconciliation.model_validate({**body, "boot_id": str(boot), "host_checkpoint": None, "observation": "found", "source": "queue_running", "prompt_id": "original-known-prompt"})
    assert recovery.reconcile(catalog["node_id"], found)["status"] == "resumed"
    with pytest.raises(DomainError):
        recovery.reconcile(catalog["node_id"], stopped(body, result["host_checkpoint_digest"], str(boot)))
    with pg() as session:
        assert session.get(Attempt, parse_id("att", body["attempt_id"])).recovery_checkpoint is None
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is not None


@pytest.mark.parametrize("field", ["gpu_hardware_id", "channel_digest", "association_id", "prompt_id", "previous_boot_id", "previous_fencing_token"])
def test_checkpoint_identity_mismatch_rolls_back_all_changes(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, field: str) -> None:
    recovery, _, job, body = prepare(pg, catalog, test_settings)
    if field in ("gpu_hardware_id", "channel_digest"):
        body["host_checkpoint"] = {**body["host_checkpoint"], field: "GPU-OTHER" if field == "gpu_hardware_id" else "c" * 64}
    else:
        body[field] = 99 if field == "previous_fencing_token" else str(uuid4())
    with pytest.raises(DomainError):
        recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(body))
    with pg() as session:
        stored = session.get(Job, parse_id("job", job["id"]))
        attempt = session.get(Attempt, stored.current_attempt_id)
        assert attempt.recovery_checkpoint is None and attempt.closed_at is None
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is not None
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


@pytest.mark.parametrize("fault", ["same_worker_boot", "missing_checkpoint", "wrong_evidence", "wrong_association", "wrong_fence"])
def test_incomplete_stop_proof_never_releases_generation(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, fault: str) -> None:
    recovery, leases, _, body = prepare(pg, catalog, test_settings)
    digest = "f" * 64
    if fault != "missing_checkpoint":
        digest = recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(body))["host_checkpoint_digest"]
    boot = catalog["boot_id"] if fault == "same_worker_boot" else uuid4()
    leases.register(catalog["node_id"], boot, {"gpus": []})
    proof = stopped(body, digest, str(boot))
    changes = {"wrong_evidence": {"evidence_digest": "0" * 64}, "wrong_association": {"association_id": str(uuid4())}, "wrong_fence": {"previous_fencing_token": 99}}
    proof = Reconciliation.model_validate({**proof.model_dump(), **changes.get(fault, {})})
    with pytest.raises(DomainError):
        recovery.reconcile(catalog["node_id"], proof)
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is not None
        assert session.get(Attempt, parse_id("att", body["attempt_id"])).closed_at is None
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_downgrade_preserves_persisted_recovery_facts(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    recovery, _, _, body = prepare(pg, catalog, test_settings)
    recovery.reconcile(catalog["node_id"], Reconciliation.model_validate(body))
    path = Path(__file__).resolve().parents[2] / "migrations/versions/04d152a00001_host_recovery_checkpoint.py"
    specification = spec_from_file_location("host_recovery_migration_under_test", path)
    assert specification and specification.loader
    migration = module_from_spec(specification)
    specification.loader.exec_module(migration)
    with pg() as session:
        version = session.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(RuntimeError, match="preserve checkpoints"), pg.begin() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.downgrade()
    with pg() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == version
        assert session.get(Attempt, parse_id("att", body["attempt_id"])).recovery_checkpoint
