from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.admin import AdminUserCreate, AdminUserPatch, JobPriority
from generation_contracts.defaults import default_limits
from generation_contracts.model_artifacts import ModelArtifactCreate, ModelArtifactPatch
from generation_contracts.queue_queries import QueueQuery
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.admin_jobs import JobAdministration
from generation_infrastructure.admin_model_artifacts import ModelArtifactAdministration
from generation_infrastructure.admin_users import UserAdministration
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminMutation,
    AdminUser,
    ApiKey,
    Artifact,
    Attempt,
    GpuOccupancy,
    Job,
    ModelArtifact,
    ObjectRecord,
    Project,
    PublicModel,
    QuotaBucket,
    WorkflowRelease,
)
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-admin-controls-test-password"
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "private synthetic prompt"}}


def admin_context(pg: sessionmaker[Session], settings: Settings, name: str = "test-admin", role: str = "administrator") -> tuple[AdminIdentity, AuthRepository, dict[str, Any]]:
    with pg.begin() as session:
        session.add(AdminUser(username=name, password_hash=password_hash(PASSWORD), role=role))
    auth = AuthRepository(pg, settings)
    login = auth.login(name, PASSWORD, "127.0.0.1", "test")
    return auth.admin(login["token"], write=False, origin=None, csrf=None), auth, login


def test_admin_retry_concurrent_once_preserves_seed_and_rechecks_quota(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    jobs, admin = JobRepository(pg), JobAdministration(pg, test_settings)
    identifier = parse_id("job", jobs.create(catalog["identity"], REQUEST, str(uuid4()), "test")["id"])
    admin.cancel(actor, identifier, str(uuid4()), "test")
    with ThreadPoolExecutor(8) as executor:
        results = list(executor.map(lambda _: admin.retry(actor, identifier, "same-admin-retry", "test"), range(8)))
    assert len({result["id"] for result in results}) == 1
    assert results[0]["retry_of"] == public_id("job", identifier)
    assert "input" not in results[0]
    with pg.begin() as session:
        rows = list(session.scalars(select(Job).order_by(Job.created_at)))
        assert len(rows) == 2 and rows[0].state == "cancelled" and rows[1].state == "queued"
        assert rows[0].execution["parameters"]["seed"] == rows[1].execution["parameters"]["seed"]
        assert session.scalar(select(QuotaBucket.daily_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.period != "active")) == 2
        assert session.scalar(select(QuotaBucket.unfinished_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.period == "active")) == 1
        key = session.get(ApiKey, catalog["identity"].key_id)
        policy = {**key.policy, "limits": {**key.policy["limits"], "daily_jobs": {"mode": "bounded", "value": 2}}}
        key.policy = policy
    with pytest.raises(DomainError) as rejected:
        admin.retry(actor, identifier, "new-retry-over-limit", "test")
    assert rejected.value.code == "quota_exceeded"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
        assert session.scalar(select(func.count()).select_from(AdminMutation).where(AdminMutation.token_digest.is_not(None))) == 2


def test_admin_cancel_keeps_gpu_and_retry_refuses_uncertain_execution(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    admin = JobAdministration(pg, test_settings)
    identifier = parse_id("job", JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "test")["id"])
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    token = str(uuid4())
    result = admin.cancel(actor, identifier, token, "test")
    assert result["status"] == "cancel_requested" and admin.cancel(actor, identifier, token, "test") == result
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is not None
    with pytest.raises(DomainError) as rejected:
        admin.retry(actor, identifier, "unsafe-retry", "test")
    assert rejected.value.code == "retry_not_safe"
    detail = admin.detail(actor, identifier, "test")
    assert "input" not in detail and detail["attempts"][0]["gpu_occupied"] is True


def test_retry_uses_current_policy_and_rejects_gateway_origin(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    admin = JobAdministration(pg, test_settings)
    identifier = parse_id("job", JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "test")["id"])
    admin.cancel(actor, identifier, "cancel-for-retry", "test")
    for changed, expected in [("disabled", "key_disabled"), ("model", "model_not_available"), ("gateway", "retry_requires_origin")]:
        with pg.begin() as session:
            key = session.get(ApiKey, catalog["identity"].key_id)
            key.enabled = changed != "disabled"
            key.issuer = "gateway:" + uuid4().hex if changed == "gateway" else "local"
            model = session.scalar(select(PublicModel))
            model.published = changed != "model"
        with pytest.raises(DomainError) as rejected:
            admin.retry(actor, identifier, "retry-" + changed, "test")
        assert rejected.value.code == expected


def test_priority_revision_and_dispatch_order(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    jobs, admin = JobRepository(pg), JobAdministration(pg, test_settings)
    first = jobs.create(catalog["identity"], REQUEST, "first-job", "test")
    second = jobs.create(catalog["identity"], REQUEST, "second-job", "test")
    identifier = parse_id("job", second["id"])
    body = JobPriority(expected_revision=1, priority=50)
    result = admin.priority(actor, identifier, body, "same-priority", "test")
    assert result["priority"] == 50 and result["updated_revision"] == 2
    assert admin.priority(actor, identifier, body, "same-priority", "test") == result
    with pytest.raises(DomainError) as stale:
        admin.priority(actor, identifier, body, "new-priority", "test")
    assert stale.value.code == "config_revision_conflict"
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    assert leases.claim(catalog["node_id"], catalog["boot_id"])["job_id"] == second["id"]
    assert admin.list(QueueQuery(state="queued"))["data"][0]["id"] == first["id"]


def test_admin_users_replay_reset_password_and_revoke_sessions(pg: sessionmaker[Session], test_settings: Settings) -> None:
    actor, auth, _ = admin_context(pg, test_settings)
    users = UserAdministration(pg, test_settings)
    body = AdminUserCreate(username="  Observer-NEW  ", password=PASSWORD, role="observer")
    first = users.create(actor, body, "same-user", "test")
    assert first["username"] == "observer-new" and users.create(actor, body, "same-user", "test") == first
    login = auth.login("OBSERVER-NEW", PASSWORD, "127.0.0.2", "test")
    identifier = parse_id("usr", first["id"])
    changed = users.patch(actor, identifier, AdminUserPatch(expected_revision=1, password=PASSWORD + "-new", role="operator"), "reset-user", "test")
    assert changed["revision"] == 2 and changed["role"] == "operator"
    with pytest.raises(DomainError) as expired:
        auth.admin(login["token"], write=False, origin=None, csrf=None)
    assert expired.value.status == 401
    with pytest.raises(DomainError):
        auth.login("observer-new", PASSWORD, "127.0.0.3", "test")
    assert auth.login("observer-new", PASSWORD + "-new", "127.0.0.3", "test")["user"]["role"] == "operator"
    assert PASSWORD not in str(users.list())
    with pg() as session:
        assert PASSWORD not in str(session.execute(select(AdminMutation.result, AdminMutation.request_digest)).all())


def test_concurrent_last_admin_protection(pg: sessionmaker[Session], test_settings: Settings) -> None:
    first, _, _ = admin_context(pg, test_settings, "first-admin")
    second, _, _ = admin_context(pg, test_settings, "second-admin")
    users = UserAdministration(pg, test_settings)

    def disable(actor: AdminIdentity) -> str:
        try:
            users.patch(actor, actor.user_id, AdminUserPatch(expected_revision=1, enabled=False), "disable-self", "test")
            return "disabled"
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        outcomes = list(executor.map(disable, [first, second]))
    assert sorted(outcomes) == ["disabled", "last_administrator"]
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AdminUser).where(AdminUser.enabled.is_(True), AdminUser.role == "administrator")) == 1


def test_database_rejects_cross_job_current_attempt(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    jobs = JobRepository(pg)
    first = parse_id("job", jobs.create(catalog["identity"], REQUEST, "first", "test")["id"])
    second = parse_id("job", jobs.create(catalog["identity"], REQUEST, "second", "test")["id"])
    with pg.begin() as session:
        attempt = Attempt(job_id=first, channel_id=catalog["channel_ids"][0], channel_snapshot={}, node_id=catalog["node_id"], boot_id=catalog["boot_id"], gpu_id=catalog["gpu_id"], fence=1, lease_expires_at=utcnow() + timedelta(seconds=60))
        session.add(attempt)
        session.flush()
        identifier = attempt.id
    with pytest.raises(IntegrityError) as invalid, pg.begin() as session:
        session.get(Job, second).current_attempt_id = identifier
    assert invalid.value.orig.diag.constraint_name == "fk_jobs_current_attempt_owner"
    identity = catalog["identity"]
    with pg.begin() as session:
        other = ApiKey(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Wrong artifact owner", policy=identity.policy)
        obj = ObjectRecord(tenant_id=identity.tenant_id, project_id=identity.project_id, object_key="synthetic/" + uuid4().hex, version_id="v1", bytes=42, sha256="a" * 64, media_type="image/png", verified_at=utcnow(), expires_at=utcnow() + timedelta(hours=1))
        session.add_all([other, obj])
        session.flush()
        other_key, object_id = other.id, obj.id
    with pytest.raises(IntegrityError) as invalid_output, pg.begin() as session:
        session.add(Artifact(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=other_key, job_id=first, object_id=object_id, position=0))
    assert invalid_output.value.orig.diag.constraint_name == "fk_artifacts_job_owner"


def test_http_admin_jobs_users_csrf_permissions_and_sensitive_reads(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    admin_context(pg, test_settings, "test-observer", "observer")
    job = JobRepository(pg).create(catalog["identity"], REQUEST, "http-job", "test")
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD}).json()
        path = "/admin/api/v1/jobs/" + job["id"]
        assert client.post(path + "/cancel", headers={"Idempotency-Key": "csrf-test"}).status_code == 403
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        assert client.get(path).status_code == 200 and "input" not in client.get(path).json()
        assert client.get(path, params={"include_input": True}).json()["input"] == REQUEST["input"]
        priority = {"expected_revision": 1, "priority": 20}
        assert client.patch(path + "/priority", json=priority, headers={"Idempotency-Key": "test-priority-token"}).status_code == 428
        assert client.patch(path + "/priority", json=priority, headers={"Idempotency-Key": "test-priority-token", "If-Match": '"1"'}).status_code == 200
        assert client.post(path + "/cancel").status_code == 400
        assert client.post(path + "/cancel", headers={"Idempotency-Key": "test-cancel-token"}).status_code == 202
        assert client.post(path + "/retry", headers={"Idempotency-Key": "test-retry-token"}).status_code == 202
        users = client.get("/admin/api/v1/admin-users")
        assert users.status_code == 200 and "password" not in users.text
        assert client.patch("/admin/api/v1/admin-users/" + public_id("usr", actor.user_id), json={"expected_revision": 1, "enabled": False}, headers={"Idempotency-Key": "test-disable-last-token", "If-Match": '"1"'}).json()["error"]["code"] == "last_administrator"
        client.post("/admin/api/v1/session", json={"username": "test-observer", "password": PASSWORD})
        assert client.get("/admin/api/v1/admin-users").status_code == 403
        assert client.get(path, params={"include_input": True}).status_code == 403


def artifact_request(catalog: dict[str, Any], **overrides: Any) -> ModelArtifactCreate:
    identity = catalog["identity"]
    return ModelArtifactCreate.model_validate({"tenant_id": public_id("ten", identity.tenant_id), "project_id": public_id("prj", identity.project_id), "name": "Synthetic file registration", "kind": "checkpoint", "sha256": "a" * 64, "bytes": 42, "relative_path": "checkpoints/synthetic.safetensors", **overrides})


def test_model_artifact_idempotency_version_archive_and_distinct_create_race(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    service = ModelArtifactAdministration(pg, test_settings)
    body = artifact_request(catalog)
    with ThreadPoolExecutor(8) as executor:
        results = list(executor.map(lambda _: service.create(actor, body, "same-artifact", "test"), range(8)))
    assert len({row["id"] for row in results}) == 1
    identifier = parse_id("ma", results[0]["id"])
    changed = service.patch(actor, identifier, ModelArtifactPatch(expected_revision=1, name="Renamed", enabled=False), "edit-artifact", "test")
    assert changed["revision"] == 2 and changed["enabled"] is False
    with pytest.raises(DomainError) as stale:
        service.delete(actor, identifier, 1, "stale-delete", "test")
    assert stale.value.code == "config_revision_conflict"
    assert service.delete(actor, identifier, 2, "delete-artifact", "test") == service.delete(actor, identifier, 2, "delete-artifact", "test")
    assert not service.list()["data"] and service.list(include_archived=True)["data"][0]["deleted_at"]
    recreated = service.create(actor, body, "new-registration", "test")
    assert recreated["id"] != results[0]["id"]
    with pytest.raises(IntegrityError):
        service.create(actor, artifact_request(catalog, relative_path="CHECKPOINTS/SYNTHETIC.safetensors"), "case-duplicate", "test")
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(ModelArtifact).where(ModelArtifact.deleted_at.is_(None))) == 1

    def distinct_create(_: int) -> str:
        try:
            service.create(actor, artifact_request(catalog, relative_path="checkpoints/concurrent.safetensors"), str(uuid4()), "test")
            return "created"
        except IntegrityError:
            return "duplicate"

    with ThreadPoolExecutor(8) as executor:
        outcomes = list(executor.map(distinct_create, range(8)))
    assert outcomes.count("created") == 1 and outcomes.count("duplicate") == 7


def test_model_artifact_frozen_release_reference_blocks_archive(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    service = ModelArtifactAdministration(pg, test_settings)
    created = service.create(actor, artifact_request(catalog), "file", "test")
    with pg.begin() as session:
        original = session.get(WorkflowRelease, catalog["release_id"])
        manifest = {**original.manifest, "dependency_lock": {"models": [{"path": "checkpoints/another-name.safetensors", "sha256": "a" * 64, "bytes": 42}]}}
        session.add(WorkflowRelease(tenant_id=original.tenant_id, project_id=original.project_id, workflow_id=original.workflow_id, version=2, digest="c" * 64, manifest=manifest, validated=True, validation={"test_only": True}))
    assert service.list()["data"][0]["referenced_release_count"] == 1
    with pytest.raises(DomainError) as referenced:
        service.delete(actor, parse_id("ma", created["id"]), 1, "delete-referenced", "test")
    assert referenced.value.code == "model_artifact_referenced"
    with pg.begin() as session:
        project = Project(tenant_id=catalog["identity"].tenant_id, name="Isolated file catalog", limits=default_limits())
        session.add(project)
        session.flush()
        project_id = project.id
    isolated = service.create(actor, artifact_request(catalog, project_id=public_id("prj", project_id)), "isolated-file", "test")
    assert isolated["referenced_release_count"] == 0
    assert service.delete(actor, parse_id("ma", isolated["id"]), 1, "delete-isolated", "test")["status"] == "archived"


def test_model_artifact_path_rejection_and_http_immutable_content(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    admin_context(pg, test_settings)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD}).json()
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        for path in ["../secret", "C:/secret", "models\\secret", "weights/CON.safetensors", "weights/file. ", "weights/file\x00.safetensors", "//server/share"]:
            bad = client.post("/admin/api/v1/model-artifacts", json=artifact_request(catalog, relative_path=path).model_dump(mode="json"), headers={"Idempotency-Key": str(uuid4())})
            assert bad.status_code == 422, bad.text
        created = client.post("/admin/api/v1/model-artifacts", json=artifact_request(catalog).model_dump(mode="json"), headers={"Idempotency-Key": "create-file-token-for-test"})
        assert created.status_code == 201, created.text
        path = "/admin/api/v1/model-artifacts/" + created.json()["id"]
        assert client.patch(path, json={"expected_revision": 1, "sha256": "b" * 64}, headers={"Idempotency-Key": "mutate-file", "If-Match": '"1"'}).status_code == 422
        assert client.delete(path, params={"revision": 1}, headers={"Idempotency-Key": "delete-file-token-for-test"}).status_code == 428
        assert client.delete(path, params={"revision": 1}, headers={"Idempotency-Key": "delete-file-token-for-test", "If-Match": '"1"'}).status_code == 200
