from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.admin import WorkflowPatch
from generation_contracts.workflow_imports import WorkflowImportPreview, WorkflowPull
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.admin_workflow_imports import WorkflowImportAdministration
from generation_infrastructure.admin_workflows import WorkflowAdministration
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.models import (
    AdminUser,
    AuditEvent,
    Channel,
    Gpu,
    Project,
    WorkerCommand,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.models import (
    WorkflowImportPreview as StoredPreview,
)
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
GRAPH = {"1": {"class_type": "SyntheticOutput", "inputs": {"text": "synthetic", "seed": 0, "filename_prefix": ""}, "_meta": {"title": "lossless"}}}


@pytest.fixture
def import_actor(pg: sessionmaker[Session]) -> AdminIdentity:
    with pg.begin() as session:
        user = AdminUser(username="import-owner", password_hash="!", role="workflow_maintainer")
        session.add(user)
        session.flush()
        return AdminIdentity(user.id, user.username, user.role, uuid4())


def pull_request(catalog: dict[str, Any], **values: Any) -> WorkflowPull:
    return WorkflowPull.model_validate({"channel_id": public_id("ch", catalog["channel_ids"][0]), "expected_revision": 1, "relative_path": "synthetic.json", **values})


def source(pg: sessionmaker[Session], catalog: dict[str, Any], actor: AdminIdentity, settings: Settings, document: dict[str, Any] | None = None) -> tuple[WorkflowImportAdministration, dict[str, Any], CommandRepository, dict[str, Any]]:
    document = document or GRAPH
    service = WorkflowImportAdministration(pg, settings)
    operation = service.pull(actor, pull_request(catalog), uuid4().hex, "test-pull")
    commands = CommandRepository(pg, SecretVault(settings.encryption_key.get_secret_value()))
    claimed = commands.claim(catalog["node_id"], catalog["boot_id"])
    assert claimed and claimed["operation_id"] == operation["operation_id"] and not claimed["requires_drain"]
    result = {"success": True, "applied_revision": 1, "source_document": document, "source_digest": fingerprint(document), "export_format": "manifest-v1" if document.get("format_version") == "1.0" else "comfy-api", "source_bytes": len(canonical_json(document).encode())}
    commands.receipt(catalog["node_id"], parse_id("cmd", claimed["command_id"]), catalog["boot_id"], claimed["command_digest"], result)
    return service, {"source_operation_id": operation["operation_id"], "source_digest": result["source_digest"]}, commands, claimed


def preview(service: WorkflowImportAdministration, actor: AdminIdentity, source_data: dict[str, Any], target: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
    return service.preview(actor, WorkflowImportPreview.model_validate({**source_data, "target": target or {"mode": "create", "name": "Imported synthetic", "task_type": "text_to_image"}}), token or uuid4().hex, "test-preview")


def test_concurrent_pull_is_idempotent_and_does_not_change_runtime(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    service = WorkflowImportAdministration(pg, test_settings)
    with pg.begin() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == catalog["channel_ids"][0]))
        binding.runtime_approval = {"synthetic": True}
    with ThreadPoolExecutor(6) as executor:
        results = list(executor.map(lambda _: service.pull(import_actor, pull_request(catalog), "same-pull", "test"), range(6)))
    assert len({r["operation_id"] for r in results}) == 1
    with pytest.raises(DomainError) as pending:
        service.pull(import_actor, pull_request(catalog), "second-pull", "test")
    assert pending.value.code == "workflow_pull_pending"
    with pg() as session:
        channel = session.get(Channel, catalog["channel_ids"][0])
        assert not channel.draining and channel.revision == 1
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is None
        assert session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id)).runtime_approval == {"synthetic": True}
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 1


def test_source_receipts_validate_before_persisting_and_hide_graph(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    service = WorkflowImportAdministration(pg, test_settings)
    service.pull(import_actor, pull_request(catalog), "receipt-test", "test")
    commands = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    claimed = commands.claim(catalog["node_id"], catalog["boot_id"])
    assert claimed
    result = {"success": True, "applied_revision": 1, "source_document": GRAPH, "source_digest": fingerprint(GRAPH), "export_format": "comfy-api", "source_bytes": 100}
    with pytest.raises(DomainError):
        commands.receipt(catalog["node_id"], parse_id("cmd", claimed["command_id"]), catalog["boot_id"], claimed["command_digest"], {**result, "source_digest": "0" * 64})
    with pg() as session:
        assert session.get(WorkerCommand, parse_id("cmd", claimed["command_id"])).receipt is None
    for _ in range(2):
        assert commands.receipt(catalog["node_id"], parse_id("cmd", claimed["command_id"]), catalog["boot_id"], claimed["command_digest"], result)["status"] == "succeeded"
    detail = AdminQueries(pg, test_settings).operation(parse_id("op", claimed["operation_id"]))
    assert detail["targets"][0]["receipt"]["source_digest"] == fingerprint(GRAPH)
    assert "source_document" not in canonical_json(detail) and "lossless" not in canonical_json(detail)
    with pytest.raises(DomainError) as conflict:
        commands.receipt(catalog["node_id"], parse_id("cmd", claimed["command_id"]), catalog["boot_id"], claimed["command_digest"], {**result, "source_bytes": 101})
    assert conflict.value.code == "receipt_conflict"


def test_concurrent_preview_and_commit_create_one_draft_with_one_audit(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    service, data, _, _ = source(pg, catalog, import_actor, test_settings)
    with ThreadPoolExecutor(6) as executor:
        previews = list(executor.map(lambda _: preview(service, import_actor, data, token="same-preview"), range(6)))
    selected = previews[0]
    assert len({r["id"] for r in previews}) == 1 and selected["diff"]["nodes"]["added"]["count"] == 1
    assert not selected["validation"]["publishable"]
    with ThreadPoolExecutor(6) as executor:
        results = list(executor.map(lambda i: service.commit(import_actor, parse_id("wip", selected["id"]), selected["candidate_digest"], "commit-" + str(i), "test"), range(6)))
    assert len({r["workflow"]["id"] for r in results}) == 1
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Workflow)) == 2
        imported = session.get(Workflow, parse_id("wf", results[0]["workflow"]["id"]))
        assert imported.draft["workflow_api"] == GRAPH and "bindings" not in imported.draft
        assert session.scalar(select(func.count()).select_from(WorkflowRelease)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "workflow.imported")) == 1
        assert session.get(StoredPreview, parse_id("wip", selected["id"])).candidate is None
    assert preview(service, import_actor, data, token="same-preview")["status"] == "committed"
    with pytest.raises(DomainError):
        service.commit(import_actor, parse_id("wip", selected["id"]), "0" * 64, "changed-digest", "test")
    with pytest.raises(DomainError):
        WorkflowAdministration(pg, test_settings).release(import_actor, parse_id("wf", results[0]["workflow"]["id"]), 1, "cannot-freeze-incomplete", "test")


def test_update_conflict_and_noop_preserve_releases_and_metadata(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    service, data, _, _ = source(pg, catalog, import_actor, test_settings)
    with pg.begin() as session:
        workflow = session.scalar(select(Workflow))
        identifier = workflow.id
        workflow.draft = {"bindings": [{"synthetic": "preserve"}], "workflow_api": {"old": {"class_type": "Old", "inputs": {}}}}
    target = {"mode": "update", "workflow_id": public_id("wf", identifier), "expected_revision": 1}
    selected = preview(service, import_actor, data, target)
    WorkflowAdministration(pg, test_settings).patch_workflow(import_actor, identifier, WorkflowPatch(expected_revision=1, name="Concurrent edit"), "test")
    with pytest.raises(DomainError) as stale:
        service.commit(import_actor, parse_id("wip", selected["id"]), selected["candidate_digest"], "stale-commit", "test")
    assert stale.value.code == "config_revision_conflict"
    selected = preview(service, import_actor, data, {**target, "expected_revision": 2})
    imported = service.commit(import_actor, parse_id("wip", selected["id"]), selected["candidate_digest"], "update-commit", "test")
    assert imported["changed"] and imported["workflow"]["revision"] == 3
    selected = preview(service, import_actor, data, {**target, "expected_revision": 3})
    imported = service.commit(import_actor, parse_id("wip", selected["id"]), selected["candidate_digest"], "noop-commit", "test")
    assert not imported["changed"] and imported["workflow"]["revision"] == 3
    with pg() as session:
        assert session.get(Workflow, identifier).draft["bindings"] == [{"synthetic": "preserve"}]
        assert session.get(WorkflowRelease, catalog["release_id"]).digest == catalog["release_digest"]


def test_preview_and_commit_reject_cross_actor_scope_staleness_and_expiry(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    service, data, _, _ = source(pg, catalog, import_actor, test_settings)
    selected = preview(service, import_actor, data)
    identifier = parse_id("wip", selected["id"])
    with pg.begin() as session:
        other = AdminUser(username="other-admin", password_hash="!", role="administrator")
        session.add(other)
        session.flush()
        actor = AdminIdentity(other.id, other.username, other.role, uuid4())
        project = Project(tenant_id=catalog["identity"].tenant_id, name="Other project", limits={})
        session.add(project)
        session.flush()
        workflow = Workflow(tenant_id=project.tenant_id, project_id=project.id, name="Other", task_type="text_to_image", draft={})
        session.add(workflow)
        session.flush()
        other_id = workflow.id
    with pytest.raises(DomainError):
        preview(service, actor, data)
    with pytest.raises(DomainError):
        service.get_preview(actor, identifier)
    with pytest.raises(DomainError):
        service.commit(actor, identifier, selected["candidate_digest"], "other-commit", "test")
    with pytest.raises(DomainError):
        preview(service, import_actor, data, {"mode": "update", "workflow_id": public_id("wf", other_id), "expected_revision": 1})
    with pg.begin() as session:
        session.get(Channel, catalog["channel_ids"][0]).desired_revision = 2
    with pytest.raises(DomainError) as stale:
        service.commit(import_actor, identifier, selected["candidate_digest"], "source-stale", "test")
    assert stale.value.code == "workflow_source_stale"
    with pg.begin() as session:
        session.get(Channel, catalog["channel_ids"][0]).desired_revision = 1
        session.get(StoredPreview, identifier).expires_at = utcnow() - timedelta(seconds=1)
    assert service.get_preview(import_actor, identifier)["status"] == "expired"
    with pytest.raises(DomainError) as expired:
        service.commit(import_actor, identifier, selected["candidate_digest"], "expired", "test")
    assert expired.value.code == "workflow_preview_expired"
    preview(service, import_actor, data)
    with pg() as session:
        assert session.get(StoredPreview, identifier).candidate is None


def test_preview_limit_and_replay_status_are_bounded(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    service, data, _, _ = source(pg, catalog, import_actor, test_settings)
    selected = preview(service, import_actor, data, token="limit-seed")
    with pg.begin() as session:
        original = session.get(StoredPreview, parse_id("wip", selected["id"]))
        for _ in range(98):
            session.add(StoredPreview(actor_id=original.actor_id, command_id=original.command_id, channel_id=original.channel_id, source_revision=1, target=original.target, candidate=original.candidate, candidate_digest=original.candidate_digest, source_digest=original.source_digest, export_format=original.export_format, diff=original.diff, validation=original.validation, expires_at=original.expires_at))
    def racing_preview(_: int) -> str:
        try:
            return preview(service, import_actor, data)["status"]
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(racing_preview, range(2))) == ["pending", "workflow_preview_limit"]
    with pytest.raises(DomainError) as limit:
        preview(service, import_actor, data)
    assert limit.value.code == "workflow_preview_limit"
    assert preview(service, import_actor, data, token="limit-seed")["id"] == selected["id"]


def test_import_audit_failure_rolls_back_draft_preview_and_idempotency(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    service, data, _, _ = source(pg, catalog, import_actor, test_settings)
    selected = preview(service, import_actor, data)
    identifier = parse_id("wip", selected["id"])
    def fail_audit(*_: Any, **__: Any) -> None:
        raise RuntimeError("synthetic transaction failure")
    with monkeypatch.context() as scoped:
        scoped.setattr("generation_infrastructure.admin_workflow_imports.record_change", fail_audit)
        with pytest.raises(RuntimeError, match="synthetic"):
            service.commit(import_actor, identifier, selected["candidate_digest"], "atomic-import", "test")
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Workflow)) == 1
        assert session.get(StoredPreview, identifier).candidate is not None
        assert session.get(StoredPreview, identifier).result is None
    assert service.commit(import_actor, identifier, selected["candidate_digest"], "atomic-import", "test")["changed"]


def test_full_manifest_import_is_lossless_and_task_type_guarded(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    manifest = {"format_version": "1.0", "task_type": "text_to_image", "workflow_api": deepcopy(GRAPH), "bindings": [{"source": name, "node_id": "1", "input_name": field} for name, field in [("prompt", "text"), ("seed", "seed"), ("output_prefix", "filename_prefix")]], "output": {"adapter": "save-image-v1", "final_node_ids": ["1"], "expected_count": 1, "history_field": "images", "max_bytes": 1048576, "mime_types": ["image/png"]}, "presets": [{"quality": "standard", "size": "1024x1024", "values": {}}], "dependency_lock": {"comfy_commit": "a" * 40, "models": [], "custom_nodes": []}, "runtime_profiles": [], "supported_parameters": []}
    service, data, _, _ = source(pg, catalog, import_actor, test_settings, manifest)
    with pytest.raises(DomainError) as mismatch:
        preview(service, import_actor, data, {"mode": "create", "name": "Mismatch", "task_type": "text_to_video"})
    assert mismatch.value.code == "workflow_task_type_mismatch"
    selected = preview(service, import_actor, data)
    assert selected["validation"]["publishable"]
    result = service.commit(import_actor, parse_id("wip", selected["id"]), selected["candidate_digest"], "manifest-commit", "test")
    with pg() as session:
        assert session.get(Workflow, parse_id("wf", result["workflow"]["id"])).draft == manifest


def test_http_import_permissions_csrf_and_source_identity(pg: sessionmaker[Session], catalog: dict[str, Any], import_actor: AdminIdentity, test_settings: Settings) -> None:
    password = "Synthetic-import-http-password"
    with pg.begin() as session:
        session.get(AdminUser, import_actor.user_id).password_hash = password_hash(password)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        body = pull_request(catalog).model_dump()
        assert client.post("/admin/api/v1/workflow-pulls", json=body).status_code == 401
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": import_actor.username, "password": password})
        assert login.status_code == 200
        assert client.post("/admin/api/v1/workflow-pulls", json=body).status_code == 403
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        client.headers["Idempotency-Key"] = "http-import-test"
        accepted = client.post("/admin/api/v1/workflow-pulls", json=body)
        assert accepted.status_code == 202
        assert client.post("/admin/api/v1/workflow-pulls", json={**body, "relative_path": "../escape.json"}).status_code == 422
        assert client.get("/admin/api/v1/workflow-import-previews/wip_" + "0" * 32).status_code == 404
        commands = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
        claimed = commands.claim(catalog["node_id"], catalog["boot_id"])
        assert claimed
        receipt = {"success": True, "applied_revision": 1, "source_document": GRAPH, "source_digest": fingerprint(GRAPH), "export_format": "comfy-api", "source_bytes": 100}
        commands.receipt(catalog["node_id"], parse_id("cmd", claimed["command_id"]), catalog["boot_id"], claimed["command_digest"], receipt)
        response = client.post("/admin/api/v1/workflow-import-previews", json={"source_operation_id": accepted.json()["operation_id"], "source_digest": fingerprint(GRAPH), "target": {"mode": "create", "name": "HTTP imported", "task_type": "text_to_image"}})
        assert response.status_code == 201, response.json()
        selected = response.json()
        assert client.get("/admin/api/v1/workflow-import-previews/" + selected["id"]).json()["status"] == "pending"
        response = client.post("/admin/api/v1/workflow-import-previews/" + selected["id"] + "/commit", json={"candidate_digest": selected["candidate_digest"]})
        assert response.status_code == 200 and response.json()["changed"]
        with pg.begin() as session:
            session.get(AdminUser, import_actor.user_id).role = "observer"
        assert client.post("/admin/api/v1/workflow-pulls", json=body).status_code == 403
        assert client.post("/admin/api/v1/workflow-import-previews", json={"source_operation_id": accepted.json()["operation_id"], "source_digest": "0" * 64, "target": {"mode": "create", "name": "Denied", "task_type": "text_to_image"}}).status_code == 403
        assert client.post("/admin/api/v1/workflow-import-previews/wip_" + "0" * 32 + "/commit", json={"candidate_digest": "0" * 64}).status_code == 403
        assert client.get("/admin/api/v1/workflow-import-previews/wip_" + "0" * 32).status_code == 403
