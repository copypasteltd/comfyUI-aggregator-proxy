from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.admin import ChannelPatch
from generation_contracts.catalog import ApproveRuntime, BindRelease, ValidationJob, WorkflowSync
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.admin_channels import ChannelAdministration
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminUser,
    ApiKey,
    Artifact,
    Attempt,
    AuditEvent,
    Channel,
    Gpu,
    Job,
    ManagementOperation,
    Node,
    ObjectRecord,
    PublicModel,
    WorkerCommand,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from generation_infrastructure.validation_jobs import ValidationJobs
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "synthetic validation sample"}}


@pytest.fixture
def actor(pg: sessionmaker[Session]) -> AdminIdentity:
    with pg.begin() as session:
        user = AdminUser(username="workflow-test-admin", password_hash="!", role="administrator")
        session.add(user)
        session.flush()
        return AdminIdentity(user.id, user.username, user.role, uuid4())


def batch(catalog: dict[str, Any], **overrides: Any) -> WorkflowSync:
    return WorkflowSync.model_validate({"direction": "push", "release_id": public_id("wfr", catalog["release_id"]), "release_digest": catalog["release_digest"], "targets": [{"channel_id": public_id("ch", identifier), "expected_revision": 1} for identifier in catalog["channel_ids"]], **overrides})


@pytest.mark.parametrize("receipt_success", [True, False])
def test_same_release_reverification_preserves_approval_but_requires_fresh_environment(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings, receipt_success: bool) -> None:
    identifier = catalog["channel_ids"][0]
    approval = {"profile_id": "synthetic-existing-profile", "evidence_id": "synthetic-historical-evidence"}
    with pg.begin() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier))
        binding.applied_digest = binding.desired_digest = catalog["release_digest"]
        binding.runtime_approval = approval
        binding.verified_digest = None
    data = BindRelease(expected_revision=1, release_id=public_id("wfr", catalog["release_id"]), verify_only=True)
    service = CatalogAdministration(pg, test_settings)
    result = service.bind(actor, identifier, data, "reverify-same-release", "synthetic-test")
    assert service.bind(actor, identifier, data, "reverify-same-release", "replay") == result
    with pg() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier))
        assert binding.runtime_approval == approval and binding.verified_digest is None
        assert session.get(Channel, identifier).draining
    commands = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    issued = commands.claim(catalog["node_id"], catalog["boot_id"])
    assert issued and issued["payload"]["release_digest"] == catalog["release_digest"]
    receipt = {"success": True, "applied_revision": 1, "environment_verified": True, "release_digest": catalog["release_digest"]} if receipt_success else {"success": False, "error_code": "model_digest_mismatch"}
    commands.receipt(catalog["node_id"], parse_id("cmd", issued["command_id"]), catalog["boot_id"], issued["command_digest"], receipt)
    with pg() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier))
        assert binding.runtime_approval == approval
        assert binding.verified_digest == (catalog["release_digest"] if receipt_success else None)


@pytest.mark.parametrize("drift", ["no_approval", "no_applied_digest", "different_desired", "channel_changed"])
def test_reverification_cannot_approve_new_or_changed_environment(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings, drift: str) -> None:
    identifier = catalog["channel_ids"][0]
    with pg.begin() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier))
        binding.applied_digest = binding.desired_digest = catalog["release_digest"]
        binding.runtime_approval = {"synthetic_existing_approval": True}
        if drift == "no_approval":
            binding.runtime_approval = None
        elif drift == "no_applied_digest":
            binding.applied_digest = None
        elif drift == "different_desired":
            binding.desired_digest = "0" * 64
        else:
            session.get(Channel, identifier).desired_revision += 1
    with pytest.raises(DomainError) as failure:
        CatalogAdministration(pg, test_settings).bind(actor, identifier, BindRelease(expected_revision=1, release_id=public_id("wfr", catalog["release_id"]), verify_only=True), "synthetic-reverify-rejected", "synthetic-test")
    assert failure.value.code == "workflow_reverification_required"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 0


def test_bulk_sync_idempotency_draining_and_parallel_receipts(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings) -> None:
    with pg.begin() as session:
        original = session.get(Channel, catalog["channel_ids"][0])
        node = Node(name="Second protocol node", credential_identifier=uuid4().hex, credential_verifier="0" * 64, current_boot_id=uuid4(), observed_at=utcnow())
        session.add(node)
        session.flush()
        gpu = Gpu(node_id=node.id, hardware_id="SYNTHETIC-SECOND", name="Second protocol GPU")
        session.add(gpu)
        session.flush()
        channel = Channel(tenant_id=original.tenant_id, project_id=original.project_id, node_id=node.id, gpu_id=gpu.id, name="Second target", base_url="http://127.0.0.1:18388", enabled=True, applied_revision=1, verified_revision=1)
        session.add(channel)
        session.flush()
        second_id, second_node, second_boot = channel.id, node.id, node.current_boot_id
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == original.id))
        binding.runtime_approval = {"synthetic_old_approval": True}
    targets = [{"channel_id": public_id("ch", identifier), "expected_revision": 1} for identifier in [catalog["channel_ids"][0], second_id]]
    service = CatalogAdministration(pg, test_settings)
    with ThreadPoolExecutor(6) as executor:
        results = list(executor.map(lambda _: service.synchronize(actor, batch(catalog, targets=targets), "same-batch", "test"), range(6)))
    assert len({r["operation_id"] for r in results}) == 1
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 2
        assert session.get(Channel, second_id).draining
        assert session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == catalog["channel_ids"][0])).runtime_approval is None
    commands = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    claimed = [(catalog["node_id"], catalog["boot_id"], commands.claim(catalog["node_id"], catalog["boot_id"])), (second_node, second_boot, commands.claim(second_node, second_boot))]
    barrier = Barrier(2)

    def receipt(item: tuple[int, tuple[Any, Any, Any]]) -> None:
        index, (node_id, boot_id, command) = item
        assert command["payload"]["manifest"] and command["requires_drain"]
        result = {"success": False, "error_code": "synthetic_dependency_missing"} if index else {"success": True, "applied_revision": 1, "environment_verified": True, "release_digest": catalog["release_digest"]}
        result["private_payload"] = "synthetic-never-expose-in-admin"
        barrier.wait(timeout=10)
        commands.receipt(node_id, parse_id("cmd", command["command_id"]), boot_id, command["command_digest"], result)

    with ThreadPoolExecutor(2) as executor:
        list(executor.map(receipt, enumerate(claimed)))
    with pg() as session:
        assert session.get(ManagementOperation, parse_id("op", results[0]["operation_id"])).state == "partial_failed"
    detail = AdminQueries(pg, test_settings).operation(parse_id("op", results[0]["operation_id"]))
    assert detail["status"] == "partial_failed" and len(detail["targets"]) == 2 and detail["has_more"] is False
    assert "synthetic-never-expose-in-admin" not in str(detail)
    assert any(target["receipt"].get("error_code") == "synthetic_dependency_missing" for target in detail["targets"])


def test_bulk_sync_stale_target_rolls_back_entire_batch(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings) -> None:
    data = batch(catalog)
    data.targets[1].expected_revision = 999
    with pytest.raises(DomainError) as rejected:
        CatalogAdministration(pg, test_settings).synchronize(actor, data, "stale-batch", "test")
    assert rejected.value.code == "config_revision_conflict"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 0
        assert session.scalar(select(func.count()).select_from(ManagementOperation)) == 0
        assert all(not channel.draining and channel.revision == 1 for channel in session.scalars(select(Channel)))


def test_validation_concurrent_replay_and_unpublished_model(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings) -> None:
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        model.published = False
        model_id = model.id
    service = ValidationJobs(pg, test_settings)
    body = ValidationJob.model_validate({"channel_id": public_id("ch", catalog["channel_ids"][0]), "request": REQUEST})
    with ThreadPoolExecutor(6) as executor:
        results = list(executor.map(lambda _: service.create(actor, model_id, body, "same-validation", "test", release_id=catalog["release_id"], expected_model_revision=1, expected_channel_revision=1), range(6)))
    assert len({result["id"] for result in results}) == 1 and "input" not in results[0]
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        job = session.scalar(select(Job))
        assert job.is_validation and job.release_id == catalog["release_id"]
        assert session.get(ApiKey, job.key_id).issuer == "internal_validation"
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    assert leases.claim(catalog["node_id"], catalog["boot_id"])["job_id"] == results[0]["id"]


def test_validation_rejects_alias_changed_after_preparation(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings) -> None:
    with pg() as session:
        model_id = session.scalar(select(PublicModel.id))

    class RacingValidation(ValidationJobs):
        def _prepare(self, *args: Any, **kwargs: Any) -> Any:
            prepared = super()._prepare(*args, **kwargs)
            with pg.begin() as session:
                original = session.get(WorkflowRelease, catalog["release_id"])
                release = WorkflowRelease(tenant_id=original.tenant_id, project_id=original.project_id, workflow_id=original.workflow_id, version=2, digest="b" * 64, manifest=original.manifest, validated=True, validation={"test_only": True})
                session.add(release)
                session.flush()
                model = session.get(PublicModel, model_id)
                model.release_id, model.revision = release.id, 2
            return prepared

    body = ValidationJob.model_validate({"channel_id": public_id("ch", catalog["channel_ids"][0]), "request": REQUEST})
    with pytest.raises(DomainError) as changed:
        RacingValidation(pg, test_settings).create(actor, model_id, body, "racing-validation", "test", release_id=catalog["release_id"], expected_model_revision=1, expected_channel_revision=1)
    assert changed.value.code == "validation_target_changed"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_channel_runtime_change_clears_approval_but_label_preserves_runtime_revision(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings) -> None:
    identifier = catalog["channel_ids"][0]
    with pg.begin() as session:
        session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier)).runtime_approval = {"synthetic_old_approval": True}
    service = ChannelAdministration(pg, test_settings)
    changed = service.patch_channel(actor, identifier, ChannelPatch(expected_revision=1, name="New display label"), "test")["channel"]
    assert changed["revision"] == 2 and changed["desired_revision"] == changed["applied_revision"] == changed["verified_revision"] == 1
    with pg() as session:
        assert session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier)).runtime_approval
    service.patch_channel(actor, identifier, ChannelPatch(expected_revision=2, base_url="http://127.0.0.1:18488"), "test")
    with pg() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier))
        assert binding.runtime_approval is None and binding.verified_digest is None


def test_runtime_approval_requires_same_channel_configuration_and_live_artifact(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings) -> None:
    """Synthetic DB evidence exercises approval rules, not GPU generation or file validity."""
    with pg.begin() as session:
        original = session.get(WorkflowRelease, catalog["release_id"])
        manifest = {**original.manifest, "runtime_profiles": [{"id": "synthetic-profile", "gpu_hardware_id": "SYNTHETIC-GPU-1", "execution_timeout_seconds": 60}]}
        release = WorkflowRelease(tenant_id=original.tenant_id, project_id=original.project_id, workflow_id=original.workflow_id, version=2, manifest=manifest, digest=fingerprint(manifest), validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        for channel_id in catalog["channel_ids"]:
            session.add(WorkflowBinding(tenant_id=release.tenant_id, project_id=release.project_id, channel_id=channel_id, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest))
        model = session.scalar(select(PublicModel))
        model.release_id = release.id
        model_id, catalog["release_id"] = model.id, release.id
    body = ValidationJob.model_validate({"channel_id": public_id("ch", catalog["channel_ids"][0]), "request": REQUEST})
    created = ValidationJobs(pg, test_settings).create(actor, model_id, body, "approval-validation", "test")
    claim = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True).claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == created["id"]
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", created["id"]))
        job.state, job.terminal_at = "succeeded", utcnow()
        attempt = session.get(Attempt, job.current_attempt_id)
        attempt.generation_released_at = attempt.closed_at = utcnow()
        obj = ObjectRecord(tenant_id=job.tenant_id, project_id=job.project_id, object_key="synthetic-validation/" + uuid4().hex, version_id="v1", bytes=42, sha256="a" * 64, media_type="image/png", verified_at=utcnow(), expires_at=utcnow() + timedelta(hours=1))
        session.add(obj)
        session.flush()
        artifact = Artifact(tenant_id=job.tenant_id, project_id=job.project_id, key_id=job.key_id, job_id=job.id, object_id=obj.id, position=0)
        session.add(artifact)
        session.flush()
        artifact_id, object_id = artifact.id, obj.id
    service = CatalogAdministration(pg, test_settings)
    data = ApproveRuntime(expected_revision=1, release_id=public_id("wfr", catalog["release_id"]), profile_id="synthetic-profile", evidence_id=public_id("content", artifact_id))
    with pytest.raises(DomainError) as wrong_channel:
        service.approve_runtime(actor, catalog["channel_ids"][1], data, "test", "wrong-channel")
    assert wrong_channel.value.code == "runtime_evidence_required"
    with pg.begin() as session:
        session.get(Attempt, parse_id("att", claim["attempt_id"])).channel_snapshot = {**claim["channel"], "revision": 99}
    with pytest.raises(DomainError) as old_config:
        service.approve_runtime(actor, catalog["channel_ids"][0], data, "test", "old-configuration")
    assert old_config.value.code == "runtime_evidence_required"
    with pg.begin() as session:
        session.get(Attempt, parse_id("att", claim["attempt_id"])).channel_snapshot = {**claim["channel"], "revision": 1}
    with ThreadPoolExecutor(4) as executor:
        approved = list(executor.map(lambda _: service.approve_runtime(actor, catalog["channel_ids"][0], data, "test", "same-approval"), range(4)))
    assert all(result == {"approved": True, "revision": 2} for result in approved)
    with pg() as session:
        audits = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "workflow.runtime_approved")))
        assert len(audits) == 1
        assert audits[0].resource == str(catalog["channel_ids"][0])
        assert audits[0].detail == {"release_id": data.release_id, "revision_before": 1, "revision_after": 2}
    for field, value in [("expires_at", utcnow() - timedelta(seconds=1)), ("delete_requested_at", utcnow()), ("verified_at", None)]:
        with pg.begin() as session:
            obj = session.get(ObjectRecord, object_id)
            original = getattr(obj, field)
            setattr(obj, field, value)
        assert service.approve_runtime(actor, catalog["channel_ids"][0], data, "test", "same-approval") == approved[0]
        with pytest.raises(DomainError) as unavailable:
            service.approve_runtime(actor, catalog["channel_ids"][0], data.model_copy(update={"expected_revision": 2}), "test", "new-" + field)
        assert unavailable.value.code == "runtime_evidence_required"
        with pg.begin() as session:
            setattr(session.get(ObjectRecord, object_id), field, original)


def test_http_workflow_sync_validation_csrf_and_explicit_versions(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, test_settings: Settings) -> None:
    password = "Synthetic-workflow-http-password"
    with pg.begin() as session:
        session.get(AdminUser, actor.user_id).password_hash = password_hash(password)
        model_id = session.scalar(select(PublicModel.id))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": actor.username, "password": password})
        assert login.status_code == 200
        sync = batch(catalog).model_dump(mode="json")
        assert client.post("/admin/api/v1/workflow-syncs", json=sync, headers={"Idempotency-Key": "workflow-csrf-test"}).status_code == 403
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        path = "/admin/api/v1/workflow-releases/" + sync["release_id"] + "/validate"
        validation = {"model_id": public_id("mdl", model_id), "expected_model_revision": 1, "channel_id": public_id("ch", catalog["channel_ids"][0]), "expected_channel_revision": 999, "sample_budget": 1, "request": REQUEST}
        assert client.post(path, json=validation, headers={"Idempotency-Key": "stale-validation-token"}).json()["error"]["code"] == "validation_target_changed"
        validation["expected_channel_revision"] = 1
        response = client.post(path, json=validation, headers={"Idempotency-Key": "valid-validation-token"})
        assert response.status_code == 202 and response.json()["object"] == "generation.job" and "input" not in response.json()
        response = client.post("/admin/api/v1/workflow-syncs", json=sync, headers={"Idempotency-Key": "valid-sync-test-token"})
        assert response.status_code == 202
        detail = client.get("/admin/api/v1/operations/" + response.json()["operation_id"])
        assert detail.status_code == 200 and len(detail.json()["targets"]) == 2
        assert client.get("/admin/api/v1/workflow-releases/" + sync["release_id"]).json()["digest"] == catalog["release_digest"]
