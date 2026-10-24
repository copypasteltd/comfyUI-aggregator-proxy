from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import Attempt, GpuOccupancy, Job, QuotaBucket
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic protocol test"}}


def lease_repository(pg: sessionmaker[Session], settings: Settings) -> LeaseRepository:
    return LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)


def test_concurrent_idempotency_accepts_and_reserves_once(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    repository = JobRepository(pg)
    with ThreadPoolExecutor(12) as executor:
        results = list(executor.map(lambda _: repository.create(catalog["identity"], REQUEST, "same-token", "synthetic"), range(12)))
    assert len({r["id"] for r in results}) == 1
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(QuotaBucket.daily_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.period != "active")) == 1
    with pytest.raises(DomainError, match="不同请求"):
        repository.create(catalog["identity"], {**REQUEST, "input": {"prompt": "changed"}}, "same-token", "synthetic")


def test_two_channels_share_one_gpu_and_old_upload_keeps_result_lease(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    jobs = JobRepository(pg)
    leases = lease_repository(pg, test_settings)
    for i in range(2):
        jobs.create(catalog["identity"], REQUEST, str(i), "synthetic")
    with ThreadPoolExecutor(8) as executor:
        claims = list(executor.map(lambda _: leases.claim(catalog["node_id"], catalog["boot_id"]), range(8)))
    accepted = [c for c in claims if c]
    assert len(accepted) == 1
    first = accepted[0]
    events = [
        ("preparing", {"execution_digest": first["execution_digest"]}),
        ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": catalog["release_digest"], "journal_committed": True}),
        ("submitted", {"prompt_id": "synthetic-prompt"}),
        ("outputs_collected", {"manifest_digest": "b" * 64, "output_count": 1, "files_stable": True}),
        ("gpu_released", {"gpu_id": public_id("gpu", catalog["gpu_id"]), "compute_stopped": True, "gpu_encoding_stopped": True, "evidence_digest": "c" * 64}),
    ]
    for sequence, (kind, payload) in enumerate(events, start=1):
        event = WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=public_id("node", catalog["node_id"]), boot_id=str(catalog["boot_id"]), job_id=first["job_id"], attempt_id=first["attempt_id"], fencing_token=first["fencing_token"], journal_seq=sequence, observed_at=int(utcnow().timestamp()), type=kind, payload=payload)
        response = leases.event(catalog["node_id"], event)
        assert leases.event(catalog["node_id"], event) == response
    second = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert second and second["fencing_token"] > first["fencing_token"]
    renewed = leases.renew(catalog["node_id"], catalog["boot_id"], parse_id("att", first["attempt_id"]), first["fencing_token"])
    assert renewed["status"] == "uploading"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GpuOccupancy)) == 1
        assert session.get(GpuOccupancy, catalog["gpu_id"]).attempt_id == parse_id("att", second["attempt_id"])


def test_expiry_does_not_free_gpu_or_allow_old_boot(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    jobs = JobRepository(pg)
    leases = lease_repository(pg, test_settings)
    for i in range(2):
        jobs.create(catalog["identity"], REQUEST, str(i), "synthetic")
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    with pg.begin() as session:
        attempt = session.get(Attempt, parse_id("att", claim["attempt_id"]))
        attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(DomainError, match="租约已失效"):
        leases.renew(catalog["node_id"], catalog["boot_id"], parse_id("att", claim["attempt_id"]), claim["fencing_token"])
    assert leases.claim(catalog["node_id"], catalog["boot_id"]) is None
    leases.register(catalog["node_id"], uuid4(), {"gpus": []})
    with pytest.raises(DomainError):
        leases.renew(catalog["node_id"], catalog["boot_id"], parse_id("att", claim["attempt_id"]), claim["fencing_token"])
