from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from conftest import capacity_sample
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.spec_isolation import spec_digest
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure import wait_estimates as module
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminUser,
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    QuotaBucket,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.runtime_basis import wait_basis
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.transactions import GLOBAL_ID
from generation_infrastructure.wait_estimates import WaitEstimates
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def seed(pg: sessionmaker[Session], catalog: dict[str, Any], *, samples: int = 10, busy: bool = True, task_type: str = "text_to_image", runtime_scale: int = 1) -> dict[str, Any]:
    """Synthetic database observations exercise the estimator, never real GPU acceptance."""
    now = utcnow()
    identity = catalog["identity"]
    with pg.begin() as session:
        gpu = session.get(Gpu, catalog["gpu_id"])
        node = session.get(Node, catalog["node_id"])
        channel = session.get(Channel, catalog["channel_ids"][0])
        old = session.get(WorkflowRelease, catalog["release_id"])
        assert gpu and node and channel and old
        profile = {"id": "measured-profile", "gpu_hardware_id": gpu.hardware_id, "warm_p95_milliseconds": 100000 * runtime_scale}
        manifest = {**deepcopy(old.manifest), "task_type": task_type, "runtime_profiles": [profile], "output": {"max_bytes": 1048576}}
        release = WorkflowRelease(tenant_id=identity.tenant_id, project_id=identity.project_id, workflow_id=old.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": False})
        session.add(release)
        session.flush()
        approval = {"profile_id": profile["id"], "evidence_id": "content_" + "a" * 32}
        binding = WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel.id, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest, runtime_approval=approval)
        session.add(binding)
        node.inventory = {"features": ["audio-output-v1"], "capacity": {"boot_id": str(node.current_boot_id), "observed_at_ms": int(now.timestamp() * 1000), "sample": capacity_sample()}}
        channel.observation = {"queue_observed_at": int(now.timestamp()), "queue_revision": channel.applied_revision, "queue": {"reachable": True, "queue_running": int(busy), "queue_pending": 0}}
        other = session.get(Channel, catalog["channel_ids"][1])
        other.observation = {"queue_observed_at": int(now.timestamp()), "queue_revision": other.applied_revision, "queue": {"reachable": True, "queue_running": 0, "queue_pending": 0}}
        inputs: dict[str, Any] = {"prompt": "synthetic fixture, no real GPU"}
        if task_type == "image_to_image":
            inputs["source_image"] = {"asset_id": "asset_" + "1" * 32}
        if task_type.startswith("references"):
            inputs["references"] = [{"asset_id": "asset_" + str(i) * 32} for i in (1, 2)]
        execution = {"input": inputs, "parameters": {"size": "1024x1024", "quality": "standard", "n": 1}, "workflow_api": {"sensitive": "never in forecast"}}
        basis = wait_basis(release_digest=release.digest, specification=spec_digest(task_type, execution), gpu_id=gpu.id, hardware_id=gpu.hardware_id,
            channel_id=channel.id, revision=channel.applied_revision, approval=approval, profile=profile, validation=False, test_only=False)

        def add_job(state: str, created: Any) -> Job:
            job = Job(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, release_id=release.id,
                model_alias="synthetic-image", task_type=task_type, state=state, request={}, execution=execution, request_fingerprint="a" * 64,
                execution_fingerprint=fingerprint(execution), eligible_channels=[public_id("ch", channel.id)], priority=0, created_at=created,
                accepted_at=created, deadline=now + timedelta(hours=1), accepted_period=now.date().isoformat())
            session.add(job)
            session.flush()
            return job

        for i in range(samples):
            released = now - timedelta(minutes=20, seconds=i)
            started = released - timedelta(seconds=(60 if i < samples // 2 else 100) * runtime_scale)
            history = add_job("succeeded", started - timedelta(seconds=1))
            attempt = Attempt(job_id=history.id, channel_id=channel.id, channel_snapshot={"wait_basis": basis}, node_id=node.id, boot_id=node.current_boot_id,
                gpu_id=gpu.id, fence=i + 1, lease_expires_at=released, created_at=started, outputs_collected_at=released,
                generation_released_at=released, closed_at=released)
            session.add(attempt)
            session.flush()
            history.current_attempt_id, history.terminal_at = attempt.id, released
        active_id = None
        if busy:
            active = add_job("running", now - timedelta(seconds=21))
            attempt = Attempt(job_id=active.id, channel_id=channel.id, channel_snapshot={"wait_basis": basis}, node_id=node.id, boot_id=node.current_boot_id,
                gpu_id=gpu.id, fence=samples + 1, lease_expires_at=now + timedelta(seconds=60), created_at=now - timedelta(seconds=20))
            session.add(attempt)
            session.flush()
            active.current_attempt_id, active_id = attempt.id, attempt.id
            session.add(GpuOccupancy(gpu_id=gpu.id, attempt_id=attempt.id, job_id=active.id))
        target = add_job("queued", now - timedelta(seconds=2))
        gpu.fence = samples + int(busy)
        for kind, identifier in (("0_global", GLOBAL_ID), ("1_tenant", identity.tenant_id), ("2_project", identity.project_id), ("3_key", identity.key_id)):
            session.add(QuotaBucket(scope_kind=kind, scope_id=identifier, period="active", generating_jobs=int(busy), unfinished_jobs=1 + int(busy)))
        return {"job": target.id, "active": active_id, "basis": basis, "now": now, "release": release.id, "binding": binding.id, "channel": channel.id}


@pytest.mark.parametrize("task_type", ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"])
def test_six_capabilities_use_actual_slot_samples_and_public_shape(pg, catalog, test_settings, monkeypatch, task_type):
    data = seed(pg, catalog, task_type=task_type)
    monkeypatch.setattr(module, "utcnow", lambda: data["now"])
    result = WaitEstimates(pg, test_settings).get(data["job"])
    assert result["status"] == "estimated" and result["reason"] is None
    assert (result["lower_wait_ms"], result["upper_wait_ms"]) == (40000, 80000)
    assert result["sample_count"] == result["minimum_route_samples"] == 10
    assert result["physical_gpus"] == 1 and result["queued_jobs"] == 1
    assert "synthetic fixture" not in str(result) and "sensitive" not in str(result)


@pytest.mark.parametrize("change,reason", [("insufficient", "insufficient_samples"), ("basis", "insufficient_samples"), ("lease", "uncertain_execution"), ("uncertain", "uncertain_execution"), ("overrun", "uncertain_execution"), ("fence", "uncertain_execution"), ("boot", "uncertain_execution"), ("observation", "stale_observation"), ("external", "uncertain_execution"), ("external_channel", "uncertain_execution"), ("capacity", "stale_observation"), ("approval", "no_ready_route"), ("reapproved", "insufficient_samples"), ("synthetic", "insufficient_samples")])
def test_unknown_cases_do_not_invent_zero_wait(pg, catalog, test_settings, monkeypatch, change, reason):
    data = seed(pg, catalog, samples=9 if change == "insufficient" else 10)
    monkeypatch.setattr(module, "utcnow", lambda: data["now"])
    with pg.begin() as session:
        attempt = session.get(Attempt, data["active"])
        channel = session.get(Channel, data["channel"])
        assert attempt and channel
        if change == "basis":
            attempt.channel_snapshot = {"wait_basis": "b" * 64}
        if change == "lease":
            attempt.lease_expires_at = data["now"]
        if change == "uncertain":
            session.get(GpuOccupancy, catalog["gpu_id"]).uncertain = True
        if change == "overrun":
            attempt.created_at = data["now"] - timedelta(seconds=100)
        if change == "fence":
            session.get(Gpu, catalog["gpu_id"]).fence += 1
        if change == "boot":
            attempt.boot_id = uuid4()
        if change == "observation":
            channel.observation = {**channel.observation, "queue_observed_at": int(data["now"].timestamp()) - 15}
        if change == "external":
            channel.observation = {**channel.observation, "queue": {"reachable": True, "queue_running": 1, "queue_pending": 1}}
        if change == "external_channel":
            other = session.scalar(select(Channel).where(Channel.gpu_id == catalog["gpu_id"], Channel.id != channel.id))
            other.observation = {**other.observation, "queue": {"reachable": True, "queue_running": 1, "queue_pending": 0}}
        if change == "capacity":
            session.get(Node, catalog["node_id"]).inventory = {}
        if change == "approval":
            session.get(WorkflowBinding, data["binding"]).runtime_approval = None
        if change == "reapproved":
            binding = session.get(WorkflowBinding, data["binding"])
            binding.runtime_approval = {**binding.runtime_approval, "evidence_id": "content_" + "b" * 32}
        if change == "synthetic":
            # Keep immutable release rows intact. A test-only attempt has no basis.
            for row in session.scalars(select(Attempt).where(Attempt.gpu_id == catalog["gpu_id"])):
                row.channel_snapshot = {"wait_basis": None}
    result = WaitEstimates(pg, test_settings).get(data["job"])
    assert result["status"] == "unknown" and result["reason"] == reason
    assert result["lower_wait_ms"] is result["upper_wait_ms"] is None


def test_forecast_is_read_only_and_uses_bounded_lateral_index_lookups(pg, catalog, test_settings, monkeypatch):
    data = seed(pg, catalog)
    monkeypatch.setattr(module, "utcnow", lambda: data["now"])
    statements = []
    engine = pg.kw["bind"]

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with pg() as session:
        before = session.execute(text("SELECT row_to_json(q) FROM quota_buckets q ORDER BY scope_kind,scope_id,period")).scalars().all()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert WaitEstimates(pg, test_settings).get(data["job"])["status"] == "estimated"
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert any("REPEATABLE READ READ ONLY" in sql for sql in statements)
    assert len(statements) <= 20
    assert sum("LATERAL" in sql and "ORDER BY attempts.fence DESC" in sql and "LIMIT" in sql for sql in statements) == 1
    assert not any(sql.lstrip().split()[0].upper() in {"UPDATE", "DELETE", "INSERT"} for sql in statements)
    assert not any("jobs.execution," in sql or "attempts.channel_snapshot," in sql for sql in statements)
    with pg() as session:
        assert session.execute(text("SELECT row_to_json(q) FROM quota_buckets q ORDER BY scope_kind,scope_id,period")).scalars().all() == before


def test_recent_fence_limit_precedes_history_sample_filter(pg, catalog, test_settings, monkeypatch):
    data = seed(pg, catalog, samples=210)
    monkeypatch.setattr(module, "utcnow", lambda: data["now"])
    result = WaitEstimates(pg, test_settings).get(data["job"])
    # The current running attempt consumes one of the latest 200 fence slots.
    assert result["status"] == "estimated"
    assert result["sample_count"] == result["minimum_route_samples"] == 199


def test_future_retry_ready_time_and_nonwaiting_states(pg, catalog, test_settings, monkeypatch):
    data = seed(pg, catalog, busy=False)
    monkeypatch.setattr(module, "utcnow", lambda: data["now"])
    reader = WaitEstimates(pg, test_settings)
    assert reader.get(data["job"])["upper_wait_ms"] == 0
    with pg.begin() as session:
        job = session.get(Job, data["job"])
        prior = Attempt(job_id=job.id, channel_id=data["channel"], channel_snapshot={}, node_id=catalog["node_id"], boot_id=catalog["boot_id"],
            gpu_id=catalog["gpu_id"], fence=11, lease_expires_at=data["now"] - timedelta(seconds=1), closed_at=data["now"] - timedelta(seconds=1))
        session.add(prior)
        session.flush()
        job.retry_count, job.retry_error_code, job.retry_source_attempt_id = 1, "preparation_transport_failure", prior.id
        job.state, job.retry_after = "retry_wait", data["now"] + timedelta(seconds=30)
    result = reader.get(data["job"])
    assert result["lower_wait_ms"] == result["upper_wait_ms"] == 30000
    with pg.begin() as session:
        job = session.get(Job, data["job"])
        job.state, job.cancellation_requested, job.retry_after = "cancel_requested", True, None
    result = reader.get(data["job"])
    assert result["status"] == "not_waiting" and result["reason"] == "not_queued"


def test_actual_claim_freezes_basis_and_replay_preserves_it(pg, catalog, test_settings, monkeypatch):
    data = seed(pg, catalog, busy=False)
    repository = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    request_id = str(uuid4())
    result = repository.claim(catalog["node_id"], catalog["boot_id"], request_id)
    assert result is not None
    with pg() as session:
        attempt = session.get(Attempt, parse_id("att", result["attempt_id"]))
        assert attempt.channel_snapshot["wait_basis"] == data["basis"]
    replay = repository.claim(catalog["node_id"], catalog["boot_id"], request_id)
    assert {key: value for key, value in replay.items() if key != "server_time"} == {key: value for key, value in result.items() if key != "server_time"}


def test_admin_session_permission_and_missing_job_http(pg, catalog, test_settings):
    data = seed(pg, catalog, busy=False)
    password = "Synthetic-wait-estimate-test-only"
    with pg.begin() as session:
        session.add(AdminUser(username="wait-observer", password_hash=password_hash(password), role="observer"))
    path = "/admin/api/v1/jobs/" + public_id("job", data["job"]) + "/wait-estimate"
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50000)) as client:
        client.headers["Origin"] = test_settings.public_origin
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer synthetic-business-key"}).status_code == 401
        assert client.post("/admin/api/v1/session", json={"username": "wait-observer", "password": password}).status_code == 200
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "estimated"
        assert client.get("/admin/api/v1/jobs/job_" + "f" * 32 + "/wait-estimate").status_code == 404
