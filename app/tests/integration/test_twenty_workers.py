from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from conftest import capacity_sample
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.values import public_id, utcnow
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import (
    ApiKey,
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Node,
    Project,
    PublicModel,
    QuotaBucket,
    Tenant,
    WorkflowBinding,
)
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def test_twenty_http_workers_share_fifty_candidates_without_double_claim(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, record_testsuite_property: Any) -> None:
    """20 authenticated ASGI HTTP protocol clients; no GPU or Windows host is simulated as real."""
    identity = catalog["identity"]
    workers, all_channels = [], []
    with pg.begin() as session:
        for entity, identifier in ((Tenant, identity.tenant_id), (Project, identity.project_id)):
            row = session.get(entity, identifier)
            limits = deepcopy(row.limits)
            limits["unfinished_jobs"]["value"], limits["generating_jobs"]["value"] = 60, 20
            row.limits = limits
        key = session.get(ApiKey, identity.key_id)
        policy = deepcopy(key.policy)
        policy["limits"]["unfinished_jobs"]["value"], policy["limits"]["generating_jobs"]["value"] = 60, 20
        key.policy = policy
        for i in range(20):
            credential_id, secret = issue_secret("worker")
            if i == 0:
                node = session.get(Node, catalog["node_id"])
                gpu = session.get(Gpu, catalog["gpu_id"])
                channels = [session.get(Channel, cid) for cid in catalog["channel_ids"]]
            else:
                node = Node(name=f"Synthetic protocol worker {i}", credential_identifier=credential_id, credential_verifier=verifier(secret, test_settings.credential_pepper.get_secret_value()), current_boot_id=uuid4(), observed_at=utcnow())
                session.add(node)
                session.flush()
                gpu = Gpu(node_id=node.id, hardware_id=f"SYNTHETIC-20-GPU-{i}", name="Protocol-only GPU")
                session.add(gpu)
                session.flush()
                channels = [Channel(tenant_id=identity.tenant_id, project_id=identity.project_id, node_id=node.id, gpu_id=gpu.id, name=f"Synthetic {i}/{j}", base_url=f"http://127.0.0.1:{19000+i*2+j}", enabled=True, desired_revision=1, applied_revision=1, verified_revision=1, observed_at=utcnow()) for j in range(2)]
                session.add_all(channels)
                session.flush()
                for channel in channels:
                    session.add(WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel.id, release_id=catalog["release_id"], desired_digest=catalog["release_digest"], applied_digest=catalog["release_digest"], verified_digest=catalog["release_digest"], observed_at=utcnow()))
            node.credential_identifier = credential_id
            node.credential_verifier = verifier(secret, test_settings.credential_pepper.get_secret_value())
            all_channels.extend(public_id("ch", channel.id) for channel in channels)
            workers.append({"node_id": node.id, "boot_id": str(node.current_boot_id), "secret": secret})
        session.scalar(select(PublicModel)).channel_ids = all_channels
    jobs = JobRepository(pg)
    with ThreadPoolExecutor(max_workers=10) as pool:
        accepted = list(pool.map(lambda _: jobs.create(identity, {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic 20-worker contention"}}, str(uuid4()), "20-protocol-workers"), range(50)))
    assert len({job["id"] for job in accepted}) == 50
    snapshot = AdminQueries(pg, test_settings).queue_snapshot()
    assert snapshot["global_unassigned_queued"] == 50
    assert len(snapshot["channels"]) == 40
    assert sum(row["shared_candidates"] for row in snapshot["channels"]) == 2000
    with pg.begin() as session:
        session.execute(update(Node).values(observed_at=utcnow()))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50200)) as client:
        stopped = Event()
        for worker in workers:
            response = client.post("/internal/v1/heartbeat", json={"protocol_version": "0.4.0", "boot_id": worker["boot_id"], "observations": [], "capacity": capacity_sample(sequence=2)}, headers={"Authorization": "Bearer " + worker["secret"]})
            assert response.status_code == 200

        def heartbeat(worker: dict[str, Any]) -> None:
            sequence = 2
            while not stopped.is_set():
                sequence += 1
                response = client.post("/internal/v1/heartbeat", json={"protocol_version": "0.4.0", "boot_id": worker["boot_id"], "observations": [], "capacity": capacity_sample(sequence=sequence)}, headers={"Authorization": "Bearer " + worker["secret"]})
                assert response.status_code == 200, response.text
                stopped.wait(2)

        def claim(worker: dict[str, Any]) -> dict[str, Any] | None:
            body = {"protocol_version": "0.4.0", "boot_id": worker["boot_id"], "request_id": str(uuid4())}
            headers = {"Authorization": "Bearer " + worker["secret"]}
            first = client.post("/internal/v1/claims", json=body, headers=headers)
            assert first.status_code == 200, first.text
            # A lost reply reuses its durable request ID and gets the same lease.
            replay = client.post("/internal/v1/claims", json=body, headers=headers)
            assert replay.status_code == 200, replay.text
            a, b = first.json()["lease"], replay.json()["lease"]
            assert a and b
            assert (a["attempt_id"], a["job_id"], a["fencing_token"]) == (b["attempt_id"], b["job_id"], b["fencing_token"])
            return first.json()["lease"]

        with ThreadPoolExecutor(max_workers=20) as heartbeat_pool:
            heartbeats = [heartbeat_pool.submit(heartbeat, worker) for worker in workers]
            try:
                with ThreadPoolExecutor(max_workers=20) as pool:
                    claims = list(pool.map(claim, workers))
            finally:
                stopped.set()
                for future in heartbeats:
                    future.result(timeout=30)
        assert all(claims)
        assert len({claim["job_id"] for claim in claims if claim}) == 20
        assert len({claim["channel"]["gpu_id"] for claim in claims if claim}) == 20
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GpuOccupancy)) == 20
        assert session.scalar(select(func.count()).select_from(Attempt)) == 20
        active = list(session.scalars(select(QuotaBucket).where(QuotaBucket.period == "active")))
        assert all(row.unfinished_jobs == 50 and row.generating_jobs == 20 for row in active)
    snapshot = AdminQueries(pg, test_settings).queue_snapshot()
    assert snapshot["global_unassigned_queued"] == 30 and snapshot["unfinished"] == 50
    assert snapshot["occupied_physical_gpus"] == 20
    record_testsuite_property("protocol_workers", 20)
    record_testsuite_property("shared_channels", 40)
    record_testsuite_property("accepted_jobs", 50)
    record_testsuite_property("real_windows_or_gpu_tested", False)
