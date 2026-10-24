from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.job_cancellations import JobCancellationBatch
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.job_cancellations import (
    DOCUMENT,
    JobCancellations,
    advance_job_cancellations,
)
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.models import (
    Attempt,
    AuditEvent,
    Gpu,
    GpuOccupancy,
    Job,
    ManagementOperation,
    OutboxEvent,
    PublicModel,
    QuotaBucket,
    UsageLedger,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, REQUEST, admin_context
from tests.integration.test_audio import audio_catalog as audio_catalog
from tests.integration.test_audio import collected
from tests.integration.test_gateway import admission_body
from tests.integration.test_gateway import gateway_client as gateway_client

pytestmark = pytest.mark.integration


def job(pg: sessionmaker[Session], catalog: dict[str, Any], *, audio: bool = False) -> str:
    request = {"model": "synthetic-audio", "task_type": "text_to_audio", "input": {"prompt": "private synthetic sound"}} if audio else REQUEST
    return JobRepository(pg).create(catalog["identity"], request, str(uuid4()), "test")["id"]


def batch(pg: sessionmaker[Session], settings: Settings, ids: list[str]) -> tuple[JobCancellations, UUID]:
    actor, _, _ = admin_context(pg, settings)
    service = JobCancellations(pg, settings)
    result = service.create(actor, JobCancellationBatch(job_ids=ids, reason="maintenance"), "synthetic-batch-key", "test")
    return service, parse_id("op", result["operation_id"])


def test_acceptance_is_durable_and_only_explicit_targets_are_cancelled(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    selected, unrelated = job(pg, catalog), job(pg, catalog)
    service, identifier = batch(pg, test_settings, [selected])
    assert service.get(identifier)["targets"][0]["current_status"] == "queued"
    assert JobCancellations(pg, test_settings).get(identifier)["status"] == "pending"
    assert advance_job_cancellations(pg) == 1
    result = service.get(identifier)
    assert result["processing_complete"] and result["status"] == "succeeded"
    assert result["targets"][0]["outcome"] == result["targets"][0]["current_status"] == "cancelled"
    assert advance_job_cancellations(pg) == 0
    with pg() as session:
        assert session.get(Job, parse_id("job", unrelated)).state == "queued"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        active = list(session.scalars(select(QuotaBucket).where(QuotaBucket.period == "active")))
        assert len(active) == 4 and all(row.unfinished_jobs == 1 and row.generating_jobs == 0 for row in active)
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "job.cancellation_processed")) == 1
    assert DOCUMENT not in str(AdminQueries(pg, test_settings).operations())
    assert "private synthetic" not in str(result)


def test_same_key_concurrent_acceptance_and_different_body_conflict(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    service = JobCancellations(pg, test_settings)
    body = JobCancellationBatch(job_ids=[job(pg, catalog)], reason="operator_request")
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: service.create(actor, body, "same-cancellation-key", "test"), range(6)))
    assert len({r["operation_id"] for r in results}) == 1
    with pytest.raises(DomainError, match="同一管理幂等键"):
        service.create(actor, body.model_copy(update={"reason": "maintenance"}), "same-cancellation-key", "test")
    assert advance_job_cancellations(pg) == 1
    assert service.create(actor, body, "same-cancellation-key", "test") == results[0]


def test_different_batches_do_not_double_account_or_settle(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    service = JobCancellations(pg, test_settings)
    body = JobCancellationBatch(job_ids=[job(pg, catalog)], reason="operator_request")
    for i in range(4):
        service.create(actor, body, f"different-batch-{i}", "test")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: advance_job_cancellations(pg), range(4)))
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "job.cancel_requested")) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "job.cancellation_processed")) == 4
        assert all(row.unfinished_jobs == 0 for row in session.scalars(select(QuotaBucket).where(QuotaBucket.period == "active")))


def test_fifty_targets_are_bounded_and_resume_in_new_runner(pg: sessionmaker[Session], test_settings: Settings) -> None:
    service, identifier = batch(pg, test_settings, [public_id("job", uuid4()) for _ in range(50)])
    assert advance_job_cancellations(pg, limit=20) == 20
    assert sum(t["outcome"] == "pending" for t in service.get(identifier)["targets"]) == 30
    assert advance_job_cancellations(pg, limit=20) == 20
    assert advance_job_cancellations(pg, limit=20) == 10
    assert service.get(identifier)["status"] == "failed"
    assert all(t["error_code"] == "not_found" for t in service.get(identifier)["targets"])


def test_multi_runner_skip_locked_and_no_duplicate_target_results(pg: sessionmaker[Session], test_settings: Settings) -> None:
    service, identifier = batch(pg, test_settings, [public_id("job", uuid4()) for _ in range(12)])
    with pg.begin() as held:
        held.scalar(select(ManagementOperation).where(ManagementOperation.id == identifier).with_for_update())
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(advance_job_cancellations, pg).result(timeout=5) == 0
    with ThreadPoolExecutor(5) as pool:
        list(pool.map(lambda _: advance_job_cancellations(pg), range(5)))
    assert service.get(identifier)["processing_complete"]
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "job.cancellation_processed")) == 12


def test_missing_target_is_partial_failure_and_other_targets_continue(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    service, identifier = batch(pg, test_settings, [public_id("job", uuid4()), job(pg, catalog)])
    assert advance_job_cancellations(pg) == 2
    result = service.get(identifier)
    assert result["status"] == "partial_failed"
    assert [t["outcome"] for t in result["targets"]] == ["failed", "cancelled"]


@pytest.mark.parametrize("crash", ["before_commit", "after_commit"])
def test_native_process_crash_preserves_atomic_progress(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, crash: str) -> None:
    service, identifier = batch(pg, test_settings, [job(pg, catalog)])
    database = test_settings.database_url.get_secret_value()
    assert make_url(database).database == "render_control_test"
    code = '''
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from generation_infrastructure.models import ManagementOperation
from generation_infrastructure.job_cancellations import COMMAND, advance_job_cancellations
sessions=sessionmaker(create_engine(os.environ['CANCELLATION_TEST_DATABASE']), expire_on_commit=False)
def crash(session):
    if session.in_nested_transaction(): return
    if any(isinstance(row,ManagementOperation) and row.command==COMMAND and row.state!='pending' for row in session.identity_map.values()): os._exit(47)
event.listen(Session,os.environ['CANCELLATION_TEST_CRASH'],crash)
advance_job_cancellations(sessions)
'''
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "CANCELLATION_TEST_DATABASE": database, "CANCELLATION_TEST_CRASH": crash}, capture_output=True, timeout=30)  # noqa: S603 -- fixed interpreter and owned fault injector, no shell
    assert result.returncode == 47, result.stderr.decode(errors="replace")
    view = service.get(identifier)
    assert view["processing_complete"] is (crash == "after_commit")
    assert advance_job_cancellations(pg) == (1 if crash == "before_commit" else 0)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "job.cancellation_processed")) == 1


def test_database_wait_rolls_back_then_fails_after_five_bounded_attempts(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    selected = job(pg, catalog)
    service, identifier = batch(pg, test_settings, [selected])
    with pg.begin() as held:
        held.scalar(select(Job).where(Job.id == parse_id("job", selected)).with_for_update())
        for expected in range(1, 6):
            with ThreadPoolExecutor(1) as pool:
                assert pool.submit(advance_job_cancellations, pg).result(timeout=5) == 0
            assert service.get(identifier)["targets"][0]["transient_attempts"] == expected
    result = service.get(identifier)
    assert result["status"] == "failed" and result["targets"][0]["error_code"] == "database_busy"
    assert result["targets"][0]["current_status"] == "queued"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "job.cancel_requested")) == 0


def test_claimed_unknown_gpu_and_fence_remain_held(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    selected = job(pg, catalog)
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    with pg.begin() as session:
        session.get(Attempt, parse_id("att", claim["attempt_id"])).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert Maintenance(pg).tick()["lease_reconciling"] == 1
    with pg() as session:
        before = (session.get(Gpu, catalog["gpu_id"]).fence, session.get(GpuOccupancy, catalog["gpu_id"]).attempt_id)
    service, identifier = batch(pg, test_settings, [selected])
    assert advance_job_cancellations(pg) == 1
    assert service.get(identifier)["targets"][0]["current_status"] == "cancel_requested"
    with pg() as session:
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        assert occupancy.uncertain and (session.get(Gpu, catalog["gpu_id"]).fence, occupancy.attempt_id) == before
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_audio_quota_is_released_by_existing_terminal_accounting(pg: sessionmaker[Session], audio_catalog: dict[str, Any], test_settings: Settings) -> None:
    service, identifier = batch(pg, test_settings, [job(pg, audio_catalog, audio=True)])
    assert advance_job_cancellations(pg) == 1
    assert service.get(identifier)["targets"][0]["outcome"] == "cancelled"
    with pg() as session:
        assert all(row.daily_audio_milliseconds == 0 for row in session.scalars(select(QuotaBucket)))
        usage = session.scalar(select(UsageLedger))
        assert usage.audio_milliseconds == 0 and usage.outcome == "cancelled"


def test_video_cancellation_refunds_only_selected_reservation(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = catalog["identity"]
    with pg.begin() as session:
        workflow = Workflow(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Synthetic cancellation video", task_type="text_to_video", draft={})
        session.add(workflow)
        session.flush()
        manifest = {**session.get(WorkflowRelease, catalog["release_id"]).manifest, "task_type": "text_to_video"}
        release = WorkflowRelease(tenant_id=identity.tenant_id, project_id=identity.project_id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        for channel in catalog["channel_ids"]:
            session.add(WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest, observed_at=utcnow()))
        session.add(PublicModel(tenant_id=identity.tenant_id, project_id=identity.project_id, alias="synthetic-cancellation-video", task_type="text_to_video", release_id=release.id, channel_ids=[public_id("ch", c) for c in catalog["channel_ids"]], defaults={"size": "1280x720", "seconds": 2, "quality": "standard"}, published=True))
    jobs = JobRepository(pg)
    body = {"model": "synthetic-cancellation-video", "task_type": "text_to_video", "input": {"prompt": "Synthetic clip"}}
    selected, unrelated = [jobs.create(identity, body, str(uuid4()), "test")["id"] for _ in range(2)]
    service, identifier = batch(pg, test_settings, [selected])
    assert advance_job_cancellations(pg) == 1
    assert service.get(identifier)["targets"][0]["outcome"] == "cancelled"
    with pg() as session:
        assert session.get(Job, parse_id("job", unrelated)).state == "queued"
        assert all(row.daily_video_milliseconds == 2000 and row.daily_image_count == row.daily_audio_milliseconds == 0 for row in session.scalars(select(QuotaBucket).where(QuotaBucket.period != "active")))
        usage = session.scalar(select(UsageLedger))
        assert usage.video_milliseconds == usage.audio_milliseconds == usage.image_count == 0


def test_completion_between_acceptance_and_execution_preserves_verified_audio(pg: sessionmaker[Session], audio_catalog: dict[str, Any], test_settings: Settings, audio_wav: Path) -> None:
    selected = job(pg, audio_catalog, audio=True)
    service, identifier = batch(pg, test_settings, [selected])
    outputs, claim, spec = collected(pg, audio_catalog, test_settings, audio_wav)
    aid = parse_id("att", claim["attempt_id"])
    auth = {"protocol_version": "0.4.0", "boot_id": claim["boot_id"], "fencing_token": claim["fencing_token"]}
    plan = outputs.authorize(audio_catalog["node_id"], aid, UploadAuthorization(**auth, outputs=[spec]))
    outputs.accept(audio_catalog["node_id"], parse_id("upl", plan["uploads"][0]["id"]), audio_catalog["boot_id"], claim["fencing_token"], audio_wav)
    completed = outputs.complete(audio_catalog["node_id"], aid, Complete(**auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"]))
    assert len(completed["artifacts"]) == 1
    assert advance_job_cancellations(pg) == 1
    target = service.get(identifier)["targets"][0]
    assert target["outcome"] == "unchanged" and target["current_status"] == "succeeded"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.scalar(select(UsageLedger)).audio_milliseconds == 500
        assert not session.get(Job, parse_id("job", selected)).cancellation_requested


def test_domain_rejection_rolls_back_partial_item_but_continues_batch(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    selected, other = job(pg, catalog), job(pg, catalog)
    service, identifier = batch(pg, test_settings, [selected, other])
    original = JobRepository.cancel

    def reject(self: JobRepository, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(self, *args, **kwargs)
        if args[1] == parse_id("job", selected):
            raise DomainError("private_error", "PRIVATE-FAILURE-BODY")
        return result

    monkeypatch.setattr(JobRepository, "cancel", reject)
    assert advance_job_cancellations(pg) == 2
    result = service.get(identifier)
    assert result["status"] == "partial_failed" and result["targets"][0]["error_code"] == "cancellation_rejected"
    assert "PRIVATE" not in str(result)
    with pg() as session:
        assert session.get(Job, parse_id("job", selected)).state == "queued"
        assert session.get(Job, parse_id("job", other)).state == "cancelled"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1


def test_gateway_pending_cancel_has_one_settlement_and_no_confirmation(gateway_client: Any, pg: sessionmaker[Session], test_settings: Settings) -> None:
    client, headers, _ = gateway_client
    response = client.post("/gateway/v1/admissions", json=admission_body(), headers=headers)
    assert response.status_code == 202, response.text
    selected = response.json()["job_id"]
    service, identifier = batch(pg, test_settings, [selected])
    assert advance_job_cancellations(pg) == 1
    assert service.get(identifier)["targets"][0]["outcome"] == "cancelled"
    assert advance_job_cancellations(pg) == 0
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.kind == "gateway.settlement")) == 1


def test_corrupt_internal_document_cannot_execute_or_echo(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    selected = job(pg, catalog)
    service, identifier = batch(pg, test_settings, [selected])
    with pg.begin() as session:
        operation = session.get(ManagementOperation, identifier)
        operation.summary = {DOCUMENT: {"private": "PRIVATE-CANCELLATION-SENTINEL"}}
    assert advance_job_cancellations(pg) == 0
    with pytest.raises(DomainError) as failure:
        service.get(identifier)
    assert "PRIVATE" not in str(failure.value)
    assert "PRIVATE" not in str(AdminQueries(pg, test_settings).operations())
    with pg() as session:
        assert session.get(Job, parse_id("job", selected)).state == "queued"
        assert session.get(ManagementOperation, identifier).state == "failed"


@pytest.mark.parametrize("role,write_status", [("administrator", 202), ("operator", 202), ("workflow_maintainer", 403), ("observer", 403)])
def test_admin_roles_csrf_idempotency_and_read_only_snapshot(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str, write_status: int) -> None:
    actor, _, _ = admin_context(pg, test_settings, role=role)
    service = JobCancellations(pg, test_settings)
    body = JobCancellationBatch(job_ids=[job(pg, catalog)], reason="key_access_changed")
    # Acceptance for read-only role is test setup through the service, never an HTTP bypass.
    accepted = service.create(actor, body, "setup-role-cancellation", "test")
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD})
        assert login.status_code == 200
        path = "/admin/api/v1/job-cancellations"
        assert client.post(path, json=body.model_dump(), headers={"Idempotency-Key": "wrong-csrf-cancellation"}).status_code == 403
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.post(path, json=body.model_dump(), headers={"Idempotency-Key": "http-role-cancellation"}).status_code == write_status
        assert client.get(path + "/" + accepted["operation_id"]).status_code == (403 if role == "workflow_maintainer" else 200)
    assert service.get(parse_id("op", accepted["operation_id"]))["targets"][0]["current_status"] == "queued"


@pytest.mark.parametrize("prefix", ["sk-gen", "worker", "gateway"])
def test_non_admin_credentials_cannot_use_batches(pg: sessionmaker[Session], test_settings: Settings, prefix: str) -> None:
    from generation_infrastructure.security import issue_secret
    _, secret = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Authorization"] = "Bearer " + secret
        path = "/admin/api/v1/job-cancellations"
        assert client.get(path + "/op_" + "a" * 32).status_code == 401
        assert client.post(path, json={"job_ids": ["job_" + "a" * 32], "reason": "maintenance"}).status_code == 401
