from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.admin_alerts import AlertQueries
from generation_infrastructure.admin_channels import ChannelAdministration
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminUser,
    Attempt,
    AuditEvent,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    ManagementOperation,
    Node,
    OutboxEvent,
)
from generation_infrastructure.security import SecretVault, issue_secret, password_hash, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic read-only protocol"}}


def leases_for(pg: sessionmaker[Session], settings: Settings) -> LeaseRepository:
    return LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)


def test_alert_rules_have_exact_cutoffs_safe_fields_and_no_mutations(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = utcnow().replace(microsecond=0) + timedelta(minutes=10)
    monkeypatch.setattr("generation_infrastructure.admin_alerts.utcnow", lambda: clock)
    jobs, leases = JobRepository(pg), leases_for(pg, test_settings)
    first = jobs.create(catalog["identity"], REQUEST, str(uuid4()), "read-fixture")
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    second = jobs.create(catalog["identity"], REQUEST, str(uuid4()), "read-fixture")
    third = jobs.create(catalog["identity"], REQUEST, str(uuid4()), "read-fixture")
    assert claim
    with pg.begin() as session:
        session.get(Job, parse_id("job", first["id"])).state = "reconciling"
        session.get(Job, parse_id("job", second["id"])).accepted_at = clock - timedelta(seconds=300)
        session.get(Job, parse_id("job", third["id"])).accepted_at = clock - timedelta(seconds=299)
        session.get(GpuOccupancy, catalog["gpu_id"]).uncertain = True
        session.get(Node, catalog["node_id"]).observed_at = clock - timedelta(seconds=15)
        channel = session.get(Channel, catalog["channel_ids"][0])
        channel.observed_at = clock - timedelta(seconds=15)
        channel.desired_revision = 2
        session.get(Channel, catalog["channel_ids"][1]).enabled = False
        actor = AdminUser(username="synthetic-alert-owner", password_hash="not-a-login-hash", role="observer")
        session.add(actor)
        session.flush()
        session.add_all([
            ManagementOperation(actor_id=actor.id, command="probe_channel", state="partial_failed", created_at=clock - timedelta(days=1), summary={"private": "DO-NOT-RETURN-RAW-RECEIPT"}),
            ManagementOperation(actor_id=actor.id, command="probe_channel", state="failed", created_at=clock - timedelta(days=1, seconds=1)),
            OutboxEvent(kind="gateway.settlement", aggregate_id=uuid4(), payload={"private": "DO-NOT-RETURN-LEDGER"}, created_at=clock - timedelta(seconds=300)),
        ])
    with pg() as session:
        before = session.scalar(select(func.count()).select_from(AuditEvent))
        lease_before = session.get(Attempt, parse_id("att", claim["attempt_id"])).lease_expires_at
    queries = AlertQueries(pg, test_settings)
    result = queries.snapshot()
    assert result["total"] == 8 and len({row["kind"] for row in result["data"]}) == 8
    assert result["data"][0]["kind"] == "gpu_occupancy_uncertain"
    assert result["data"] == queries.snapshot()["data"]
    assert "DO-NOT-RETURN" not in json.dumps(result)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == before
        assert session.get(Attempt, parse_id("att", claim["attempt_id"])).lease_expires_at == lease_before
        assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain
    with pg.begin() as session:
        session.get(Job, parse_id("job", first["id"])).state = "running"
        session.get(Job, parse_id("job", second["id"])).accepted_at = clock
        node = session.get(Node, catalog["node_id"])
        node.observed_at = clock
        node.inventory = {**node.inventory, "capacity": {**node.inventory["capacity"], "observed_at_ms": int(clock.timestamp() * 1000)}}
        channel = session.get(Channel, catalog["channel_ids"][0])
        channel.enabled = False
        session.get(GpuOccupancy, catalog["gpu_id"]).uncertain = False
        for operation in session.scalars(select(ManagementOperation)):
            operation.state = "succeeded"
        for event in session.scalars(select(OutboxEvent).where(OutboxEvent.kind == "gateway.settlement")):
            event.delivered_at = clock
    assert queries.snapshot()["total"] == 0


def test_alert_filter_and_pagination_do_not_drop_or_repeat_resources(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = utcnow().replace(microsecond=0)
    monkeypatch.setattr("generation_infrastructure.admin_alerts.utcnow", lambda: clock)
    with pg.begin() as session:
        for index in range(52):
            session.add(Node(name=f"Synthetic stale {index}", credential_identifier=uuid4().hex, credential_verifier="0" * 64, observed_at=clock - timedelta(seconds=16)))
        session.add(Node(name="Synthetic disabled", enabled=False, credential_identifier=uuid4().hex, credential_verifier="0" * 64, observed_at=clock - timedelta(days=1)))
    query = AlertQueries(pg, test_settings)
    first, second = query.snapshot(1, "node_heartbeat_stale"), query.snapshot(2, "node_heartbeat_stale")
    assert first["total"] == second["total"] == 52
    assert len(first["data"]) == 50 and first["has_more"]
    assert len(second["data"]) == 2 and not second["has_more"]
    assert len({row["id"] for row in first["data"] + second["data"]}) == 52


def test_node_detail_bounds_collections_and_omits_private_inventory_and_credentials(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    vault = SecretVault(test_settings.encryption_key.get_secret_value())
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        node.inventory = {"private": "SYNTHETIC-RAW-INVENTORY-SECRET"}
        for index in range(100):
            session.add(Gpu(node_id=node.id, hardware_id=f"SYNTHETIC-EXTRA-{index}", name="Synthetic GPU"))
            session.add(Channel(tenant_id=catalog["identity"].tenant_id, project_id=catalog["identity"].project_id, node_id=node.id, gpu_id=catalog["gpu_id"], name="Synthetic detail channel", base_url="http://127.0.0.1:8188", encrypted_auth=vault.encrypt("SYNTHETIC-CHANNEL-SECRET")))
        session.get(Channel, catalog["channel_ids"][1]).deleted_at = utcnow()
    result = ChannelAdministration(pg, test_settings).get_node(catalog["node_id"])
    assert len(result["gpus"]) == len(result["channels"]) == 100
    assert result["gpus_truncated"] and result["channels_truncated"]
    raw = json.dumps(result)
    for private in ("SYNTHETIC-RAW-INVENTORY-SECRET", "SYNTHETIC-CHANNEL-SECRET", "credential_verifier", "encrypted_auth", "inventory"):
        assert private not in raw
    assert public_id("ch", catalog["channel_ids"][1]) not in raw
    with pytest.raises(DomainError) as error:
        ChannelAdministration(pg, test_settings).get_node(uuid4())
    assert error.value.status == 404


def test_attempt_reads_expired_and_closed_receipts_without_releasing_or_renewing(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "read-fixture")
    leases = leases_for(pg, test_settings)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    attempt_id = parse_id("att", claim["attempt_id"])
    expiry = utcnow() - timedelta(seconds=1)
    with pg.begin() as session:
        session.get(Attempt, attempt_id).lease_expires_at = expiry
        session.get(GpuOccupancy, catalog["gpu_id"]).uncertain = True
    result = leases.attempt_status(catalog["node_id"], catalog["boot_id"], attempt_id, claim["fencing_token"])
    assert not result["lease_valid"] and result["reconciliation_required"]
    assert result["gpu_occupied"] and result["occupancy_uncertain"]
    assert not {"execution", "workflow", "channel", "prompt_id", "generation_evidence"} & result.keys()
    with pg.begin() as session:
        assert session.get(Attempt, attempt_id).lease_expires_at == expiry
        assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain
        session.get(Attempt, attempt_id).closed_at = utcnow()
    closed = leases.attempt_status(catalog["node_id"], catalog["boot_id"], attempt_id, claim["fencing_token"])
    assert closed["closed"] and not closed["lease_valid"] and not closed["reconciliation_required"]


def test_attempt_result_lease_survives_a_new_generation_fence(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    jobs, leases = JobRepository(pg), leases_for(pg, test_settings)
    for _ in range(2):
        jobs.create(catalog["identity"], REQUEST, str(uuid4()), "read-fixture")
    first = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert first
    events = [("preparing", {"execution_digest": first["execution_digest"]}), ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": catalog["release_digest"], "journal_committed": True}), ("submitted", {"prompt_id": "synthetic-read-prompt"}), ("outputs_collected", {"manifest_digest": "b" * 64, "output_count": 1, "files_stable": True}), ("gpu_released", {"gpu_id": public_id("gpu", catalog["gpu_id"]), "compute_stopped": True, "gpu_encoding_stopped": True, "evidence_digest": "c" * 64})]
    for sequence, (kind, payload) in enumerate(events, 1):
        leases.event(catalog["node_id"], WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=public_id("node", catalog["node_id"]), boot_id=str(catalog["boot_id"]), job_id=first["job_id"], attempt_id=first["attempt_id"], fencing_token=first["fencing_token"], journal_seq=sequence, observed_at=int(utcnow().timestamp()), type=kind, payload=payload))
    second = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert second and second["fencing_token"] > first["fencing_token"]
    result = leases.attempt_status(catalog["node_id"], catalog["boot_id"], parse_id("att", first["attempt_id"]), first["fencing_token"])
    assert result["lease_valid"] and result["generation_released"] and not result["gpu_occupied"]
    assert result["last_journal_seq"] == 5 and result["status"] == "uploading"


def test_snapshot_http_identity_boot_fence_and_parameter_guards(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    password = "Synthetic-read-observer-password"
    worker_identifier, worker_secret = issue_secret("worker")
    other_identifier, other_secret = issue_secret("worker")
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        node.credential_identifier = worker_identifier
        node.credential_verifier = verifier(worker_secret, test_settings.credential_pepper.get_secret_value())
        session.add(Node(name="Other synthetic node", credential_identifier=other_identifier, credential_verifier=verifier(other_secret, test_settings.credential_pepper.get_secret_value()), current_boot_id=uuid4(), observed_at=utcnow()))
        session.add(AdminUser(username="read-observer", password_hash=password_hash(password), role="observer"))
    JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "read-fixture")
    leases = leases_for(pg, test_settings)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    path = "/internal/v1/attempts/" + claim["attempt_id"]
    params = {"boot_id": str(catalog["boot_id"]), "fencing_token": claim["fencing_token"]}
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50122)) as client:
        assert client.get("/admin/api/v1/alerts").status_code == 401
        client.headers["Origin"] = test_settings.public_origin
        assert client.post("/admin/api/v1/session", json={"username": "read-observer", "password": password}).status_code == 200
        assert client.get("/admin/api/v1/alerts").status_code == 200
        assert client.get("/admin/api/v1/alerts", params={"kind": "unknown"}).status_code == 422
        assert client.get("/admin/api/v1/alerts", params={"page": 1001}).status_code == 422
        assert client.get("/admin/api/v1/nodes/" + public_id("node", catalog["node_id"])).status_code == 200
        assert client.get(path, params=params).status_code == 401
        headers = {"Authorization": "Bearer " + worker_secret}
        assert client.get(path, params=params, headers=headers).status_code == 200
        assert client.get(path, headers=headers).status_code == 422
        assert client.get(path, params={**params, "boot_id": "invalid"}, headers=headers).status_code == 422
        assert client.get(path, params={**params, "fencing_token": 0}, headers=headers).status_code == 422
        assert client.get(path, params=params, headers={"Authorization": "Bearer " + other_secret}).status_code == 404
        assert client.get(path, params={**params, "fencing_token": claim["fencing_token"] + 1}, headers=headers).status_code == 409
        assert client.get(path, params={**params, "boot_id": str(uuid4())}, headers=headers).status_code == 409
        leases.register(catalog["node_id"], uuid4(), {"gpus": []})
        assert client.get(path, params=params, headers=headers).status_code == 409
