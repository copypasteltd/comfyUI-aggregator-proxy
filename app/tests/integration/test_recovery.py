from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from generation_contracts.recovery import Reconciliation
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.models import Attempt, GpuOccupancy
from generation_infrastructure.recovery import RecoveryRepository
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def test_absent_history_never_releases_but_matching_prompt_restores_same_attempt(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    jobs = JobRepository(pg)
    job = jobs.create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "recovery protocol"}}, str(uuid4()), "test")
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    association = str(uuid4())
    for seq, (kind, payload) in enumerate([("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": association, "workflow_digest": claim["workflow_digest"], "journal_committed": True})], 1):
        leases.event(catalog["node_id"], WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=public_id("node", catalog["node_id"]), boot_id=claim["boot_id"], job_id=job["id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=seq, observed_at=int(utcnow().timestamp()), type=kind, payload=payload))
    with pg.begin() as session:
        session.get(Attempt, parse_id("att", claim["attempt_id"])).lease_expires_at = utcnow() - timedelta(seconds=1)
    Maintenance(pg).tick()
    new_boot = uuid4()
    leases.register(catalog["node_id"], new_boot, {"gpus": []})
    body = {"boot_id": str(new_boot), "attempt_id": claim["attempt_id"], "previous_boot_id": claim["boot_id"], "previous_fencing_token": claim["fencing_token"], "association_id": association, "prompt_id": None, "observation": "absent", "source": "unknown", "evidence_digest": "f" * 64}
    recovery = RecoveryRepository(leases)
    result = recovery.reconcile(catalog["node_id"], Reconciliation(**body))
    assert result == {"status": "attention_required", "safe_to_resubmit": False}
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain
    found = Reconciliation(**{**body, "observation": "found", "source": "queue_running", "prompt_id": "accepted-before-response-loss"})
    resumed = recovery.reconcile(catalog["node_id"], found)
    assert resumed["lease"]["attempt_id"] == claim["attempt_id"]
    assert resumed["lease"]["fencing_token"] > claim["fencing_token"]
    assert resumed["lease"]["resume_state"] == "running"
    assert recovery.reconcile(catalog["node_id"], found)["lease"]["fencing_token"] == resumed["lease"]["fencing_token"]
    with pytest.raises(DomainError):
        leases.renew(catalog["node_id"], catalog["boot_id"], parse_id("att", claim["attempt_id"]), claim["fencing_token"])
    assert leases.renew(catalog["node_id"], new_boot, parse_id("att", claim["attempt_id"]), resumed["lease"]["fencing_token"])["status"] == "running"


def test_cancel_intent_survives_expired_lease_and_reconciliation(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    jobs = JobRepository(pg)
    job = jobs.create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "cancel recovery"}}, str(uuid4()), "test")
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    jobs.cancel(catalog["identity"], parse_id("job", job["id"]), "cancel")
    with pg.begin() as session:
        session.get(Attempt, parse_id("att", claim["attempt_id"])).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert Maintenance(pg).tick()["lease_reconciling"] == 1
    assert jobs.get(catalog["identity"], parse_id("job", job["id"]))["status"] == "reconciling"
