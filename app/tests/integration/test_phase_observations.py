from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import Attempt, Job, WorkerInbox
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
FIELDS = ("preparing_at", "submit_intent_at", "submitted_at", "outputs_collected_at")


@pytest.mark.parametrize("rollback_phase", range(4))
def test_phase_observations_commit_with_event_and_ignore_replay_or_fenced_delivery(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch, rollback_phase: int) -> None:
    JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic phase clock"}}, uuid4().hex, "phase-clock")
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    identifier = parse_id("att", claim["attempt_id"])
    clock = utcnow()
    monkeypatch.setattr("generation_infrastructure.leases.utcnow", lambda: clock)
    phases = [("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": catalog["release_digest"], "journal_committed": True}), ("submitted", {"prompt_id": "synthetic-phase-prompt"}), ("outputs_collected", {"manifest_digest": "b" * 64, "output_count": 1, "files_stable": True})]
    events = []
    for index, (kind, payload) in enumerate(phases):
        clock += timedelta(seconds=1)
        event = WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=public_id("node", catalog["node_id"]), boot_id=str(catalog["boot_id"]), job_id=claim["job_id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=index + 1, observed_at=int(clock.timestamp()) - 3600, type=kind, payload=payload)
        with pytest.raises(DomainError):
            leases.event(catalog["node_id"], event.model_copy(update={"fencing_token": claim["fencing_token"] + 100}))
        if index == rollback_phase:
            def fail(*_: Any, **__: Any) -> None:
                raise RuntimeError("Synthetic transaction rollback")
            with monkeypatch.context() as temporary:
                temporary.setattr("generation_infrastructure.leases.record_change", fail)
                with pytest.raises(RuntimeError, match="Synthetic transaction rollback"):
                    leases.event(catalog["node_id"], event)
            with pg() as session:
                assert getattr(session.get(Attempt, identifier), FIELDS[index]) is None
                assert session.scalar(select(func.count()).select_from(WorkerInbox)) == index
                assert session.get(Attempt, identifier).journal_seq == index
        response = leases.event(catalog["node_id"], event)
        with pg() as session:
            assert getattr(session.get(Attempt, identifier), FIELDS[index]) == clock
            observed = tuple(getattr(session.get(Attempt, identifier), name) for name in FIELDS)
            assert session.get(Job, parse_id("job", claim["job_id"])).state == ("preparing", "submitting", "running", "collecting")[index]
        clock += timedelta(milliseconds=500)
        assert leases.event(catalog["node_id"], event) == response
        with pg() as session:
            assert tuple(getattr(session.get(Attempt, identifier), name) for name in FIELDS) == observed
        events.append(event)
    # Already accepted inbox entries remain replayable after a later Worker boot.
    leases.register(catalog["node_id"], uuid4(), {"gpus": []})
    for event in events:
        assert leases.event(catalog["node_id"], event)["accepted"]
    with pg() as session:
        assert tuple(getattr(session.get(Attempt, identifier), name) for name in FIELDS) == observed
