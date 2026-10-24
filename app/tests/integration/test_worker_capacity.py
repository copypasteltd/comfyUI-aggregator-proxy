from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from conftest import capacity_sample
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.values import parse_id, utcnow
from generation_infrastructure.admin_alerts import AlertQueries
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import Attempt, GpuOccupancy, Node, QuotaBucket
from generation_infrastructure.security import SecretVault, issue_secret, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic capacity gate"}}


def repository(pg: sessionmaker[Session], settings: Settings) -> LeaseRepository:
    return LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)


@pytest.mark.parametrize("change", ["missing", "stale", "boot", "disk", "memory", "spool", "upload", "unavailable"])
def test_capacity_gate_creates_no_attempt_occupancy_or_generation_charge(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, change: str) -> None:
    JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "capacity")
    leases = repository(pg, test_settings)
    overrides = {"disk": {"free_disk_bytes": 0}, "memory": {"available_memory_bytes": 0}, "spool": {"spool_bytes": 20 * 1024**3}, "upload": {"pending_uploads": 2}, "unavailable": {"spool_bytes": None}}
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        entry = {**node.inventory["capacity"]}
        if change == "missing":
            node.inventory = {}
        else:
            if change == "stale":
                entry["observed_at_ms"] = int((utcnow() - timedelta(seconds=16)).timestamp() * 1000)
            if change == "boot":
                entry["boot_id"] = str(uuid4())
            if change in overrides:
                entry["sample"] = capacity_sample(**overrides[change])
            node.inventory = {"capacity": entry, "capacity_sequence": 1}
    request = str(uuid4())
    assert leases.claim(catalog["node_id"], catalog["boot_id"], request) is None
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Attempt)) == 0
        assert session.scalar(select(func.count()).select_from(GpuOccupancy)) == 0
        assert session.scalar(select(func.coalesce(func.sum(QuotaBucket.generating_jobs), 0))) == 0
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=2))
    assert leases.claim(catalog["node_id"], catalog["boot_id"], request) is None  # Empty response replay.
    assert leases.claim(catalog["node_id"], catalog["boot_id"], str(uuid4()))


def test_capacity_does_not_block_committed_claim_replay_or_renewal(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "capacity")
    leases = repository(pg, test_settings)
    request = str(uuid4())
    claim = leases.claim(catalog["node_id"], catalog["boot_id"], request)
    assert claim
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=2, free_disk_bytes=0))
    replay = leases.claim(catalog["node_id"], catalog["boot_id"], request)
    assert replay and replay["attempt_id"] == claim["attempt_id"]
    assert leases.renew(catalog["node_id"], catalog["boot_id"], parse_id("att", claim["attempt_id"]), claim["fencing_token"])


def test_heartbeat_sequence_missing_snapshot_and_new_boot_never_revive_permission(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    leases = repository(pg, test_settings)
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=4, free_disk_bytes=0))
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=3))
    with pg() as session:
        assert session.get(Node, catalog["node_id"]).inventory["capacity"]["sample"]["status"] == "blocked"
    leases.register(catalog["node_id"], catalog["boot_id"], {"gpus": []})
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=3))
    with pg() as session:
        assert session.get(Node, catalog["node_id"]).inventory["capacity"]["sample"]["status"] == "blocked"
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [])
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=4))
    with pg() as session:
        assert "capacity" not in session.get(Node, catalog["node_id"]).inventory
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=5))
    new_boot = uuid4()
    leases.register(catalog["node_id"], new_boot, {"gpus": []})
    with pg() as session:
        assert session.get(Node, catalog["node_id"]).inventory == {"gpus": []}
    leases.heartbeat(catalog["node_id"], new_boot, [], capacity_sample(sequence=1))
    with pg() as session:
        assert session.get(Node, catalog["node_id"]).inventory["capacity_sequence"] == 1


def test_capacity_heartbeat_http_validates_derived_status(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    credential, token = issue_secret("worker")
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        node.credential_identifier, node.credential_verifier = credential, verifier(token, test_settings.credential_pepper.get_secret_value())
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        body = {"protocol_version": "0.4.0", "boot_id": str(catalog["boot_id"]), "observations": [], "capacity": capacity_sample(sequence=2)}
        headers = {"Authorization": "Bearer " + token}
        assert client.post("/internal/v1/heartbeat", json={**body, "capacity": {**body["capacity"], "free_disk_bytes": 0}}, headers=headers).status_code == 422
        assert client.post("/internal/v1/heartbeat", json=body, headers=headers).status_code == 200
        assert client.post("/internal/v1/heartbeat", json=body).status_code == 401


def test_admin_capacity_alerts_are_live_bounded_and_path_free(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    leases, alerts = repository(pg, test_settings), AlertQueries(pg, test_settings)
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=2, free_disk_bytes=0, available_memory_bytes=0, pending_uploads=2))
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        node.inventory = {**node.inventory, "private_path": "DO-NOT-EXPOSE"}
    for kind in ("worker_storage_pressure", "worker_memory_pressure", "worker_upload_backlog"):
        result = alerts.snapshot(kind=kind)
        assert result["total"] == 1 and result["data"][0]["capacity"]["pending_uploads"] == 2
        assert "DO-NOT-EXPOSE" not in json.dumps(result)
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [])
    assert alerts.snapshot(kind="worker_capacity_unavailable")["total"] == 1
    assert alerts.snapshot(kind="worker_storage_pressure")["total"] == 0
    with pg.begin() as session:
        session.get(Node, catalog["node_id"]).observed_at = utcnow() - timedelta(seconds=16)
    assert alerts.snapshot(kind="worker_capacity_unavailable")["total"] == 0
    assert alerts.snapshot(kind="node_heartbeat_stale")["total"] == 1
