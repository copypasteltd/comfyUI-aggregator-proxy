from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from generation_domain.values import parse_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.models import Attempt, GpuOccupancy, Job, QuotaBucket, UsageLedger
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.fault]
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic crash test"}}


def test_expired_execution_remains_occupied_and_queue_expiry_settles_once(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    jobs = JobRepository(pg)
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    jobs.create(catalog["identity"], REQUEST, str(uuid4()), "synthetic")
    jobs.create(catalog["identity"], REQUEST, str(uuid4()), "synthetic")
    claimed = leases.claim(catalog["node_id"], catalog["boot_id"], "synthetic-request-0001")
    assert claimed
    replay = leases.claim(catalog["node_id"], catalog["boot_id"], "synthetic-request-0001")
    assert replay and replay["attempt_id"] == claimed["attempt_id"]
    with pg.begin() as session:
        attempt = session.get(Attempt, parse_id("att", claimed["attempt_id"]))
        attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
        queued = session.scalar(select(Job).where(Job.state == "queued"))
        queued.deadline = utcnow() - timedelta(seconds=1)
    maintenance = Maintenance(pg)
    result = maintenance.tick()
    assert result == {"queue_expired": 1, "lease_reconciling": 1, "cancellation_targets_processed": 0, "retry_queued": 0, "admission_expired": 0, "access_events_deleted": 0, "audit_events_deleted": 0}
    assert maintenance.tick() == {"queue_expired": 0, "lease_reconciling": 0, "cancellation_targets_processed": 0, "retry_queued": 0, "admission_expired": 0, "access_events_deleted": 0, "audit_events_deleted": 0}
    with pg() as session:
        occupied = session.get(GpuOccupancy, catalog["gpu_id"])
        assert occupied and occupied.uncertain
        assert session.scalar(select(Job.state).where(Job.id == parse_id("job", claimed["job_id"]))) == "reconciling"
        active = session.scalar(select(QuotaBucket).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.period == "active"))
        assert active.unfinished_jobs == 1 and active.generating_jobs == 1
        assert len(list(session.scalars(select(UsageLedger)))) == 1
