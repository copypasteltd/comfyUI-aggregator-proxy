from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.admin import WorkflowPatch
from generation_contracts.workflow_restoration import WorkflowDraftRestore
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id
from generation_infrastructure.admin_workflows import WorkflowAdministration
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import (
    AdminMutation,
    ApiCredential,
    AuditEvent,
    Gpu,
    PublicModel,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, REQUEST, admin_context
from tests.integration.test_workflow_directory import state

pytestmark = pytest.mark.integration
UNSET = object()
KINDS = ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"]


def restoration_source(pg: sessionmaker[Session], catalog: dict[str, Any], audio: dict[str, Any], kind: str = "text_to_audio", *, manifest_override: Any = UNSET, forced_digest: str | None = None, parent_id: UUID | None = None) -> tuple[UUID, WorkflowDraftRestore, dict[str, Any]]:
    manifest = copy.deepcopy(audio)
    manifest["task_type"] = kind
    manifest["workflow_api"]["1"]["inputs"]["seed"] = 18446744073709551615
    manifest["workflow_api"]["1"]["_meta"] = {"retained": "PRIVATE-RESTORATION", "integer": 9007199254740993}
    if kind != "text_to_audio":
        video = "video" in kind
        manifest["format_version"] = "1.0"
        manifest["output"].update(adapter="video-files-v1" if video else "save-image-v1", history_field="videos" if video else "images", mime_types=["video/mp4" if video else "image/png"])
        manifest["presets"] = [{"quality": "standard", "size": "1024x1024", **({"seconds": 2} if video else {}), "values": {}}]
        for source in ["source_image"] if kind == "image_to_image" else ["reference_0", "reference_1"] if kind == "references_to_image" else ["reference_0"] if kind == "references_to_video" else []:
            manifest["workflow_api"]["1"]["inputs"][source] = ""
            manifest["bindings"].append({"source": source, "node_id": "1", "input_name": source})
    if manifest_override is not UNSET:
        manifest = manifest_override
    with pg.begin() as session:
        workflow = session.get(Workflow, parent_id) if parent_id else Workflow(tenant_id=catalog["identity"].tenant_id, project_id=catalog["identity"].project_id, name="Synthetic restoration", task_type=kind, draft={"old": "PRIVATE-DRAFT"})
        session.add(workflow)
        session.flush()
        release = WorkflowRelease(tenant_id=workflow.tenant_id, project_id=workflow.project_id, workflow_id=workflow.id, version=(session.scalar(select(func.max(WorkflowRelease.version)).where(WorkflowRelease.workflow_id == workflow.id)) or 0) + 1, digest=forced_digest or fingerprint(manifest), manifest=manifest, validated=True, validation={"synthetic": True})
        session.add(release)
        session.flush()
        return workflow.id, WorkflowDraftRestore(expected_revision=1, source_release_id=public_id("wfr", release.id), source_digest=release.digest), manifest


@pytest.mark.parametrize("kind", KINDS)
def test_six_types_restore_exact_source_once_with_safe_metadata_and_audit(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings, kind: str) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    identifier, request, manifest = restoration_source(pg, catalog, audio_manifest, kind)
    admin, before = WorkflowAdministration(pg, test_settings), state(pg)
    result = admin.restore_draft(actor, identifier, request, "restore-six-types", "restore-request")
    assert result == {"workflow_id": public_id("wf", identifier), "source_release_id": request.source_release_id, "source_digest": request.source_digest, "previous_revision": 1, "revision": 2}
    assert admin.restore_draft(actor, identifier, request, "restore-six-types", "replay") == result
    with pg() as session:
        row = session.get(Workflow, identifier)
        assert row.draft == manifest and row.revision == 2 and row.name == "Synthetic restoration" and row.task_type == kind
        assert session.get(WorkflowRelease, parse_id("wfr", request.source_release_id)).manifest == manifest
        assert session.scalar(select(func.count()).select_from(WorkflowRelease).where(WorkflowRelease.workflow_id == identifier)) == 1
        audits = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "workflow.draft_restored")))
        assert len(audits) == 1 and audits[0].detail == {"source_release_id": request.source_release_id, "source_digest": request.source_digest, "revision_before": 1, "revision_after": 2}
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 1
    assert state(pg) == [before[0] + 1, *before[1:]]
    assert "PRIVATE" not in str(result) and "PRIVATE" not in str(audits[0].detail)


def test_replay_after_later_edit_returns_original_result_without_replacing_again(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    identifier, request, _ = restoration_source(pg, catalog, audio_manifest)
    admin = WorkflowAdministration(pg, test_settings)
    first = admin.restore_draft(actor, identifier, request, "restore-original-key", "test")
    admin.patch_workflow(actor, identifier, WorkflowPatch(expected_revision=2, draft={"later": 18446744073709551614}), "test")
    assert admin.restore_draft(actor, identifier, request, "restore-original-key", "replay") == first
    with pg() as session:
        assert session.get(Workflow, identifier).draft == {"later": 18446744073709551614}
        assert session.get(Workflow, identifier).revision == 3
    for key, data, code in [("different-key", request, "config_revision_conflict"), ("restore-original-key", request.model_copy(update={"expected_revision": 3}), "idempotency_conflict")]:
        with pytest.raises(DomainError) as caught:
            admin.restore_draft(actor, identifier, data, key, "test")
        assert caught.value.code == code


@pytest.mark.parametrize("scenario,status", [("missing_parent", 404), ("missing_source", 404), ("cross_parent", 404), ("archived", 409), ("revision", 409), ("digest", 409), ("unvalidated", 422), ("corrupt_digest", 422), ("wrong_type", 422), ("invalid_manifest", 422), ("depth", 413), ("nodes", 413), ("size", 413), ("scalar", 422)])
def test_failed_restore_rolls_back_all_writes_and_never_loads_old_draft(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings, scenario: str, status: int) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    override: Any = UNSET
    if scenario == "wrong_type":
        override = {**copy.deepcopy(audio_manifest), "task_type": "text_to_image"}
    elif scenario == "invalid_manifest":
        override = {"private": "PRIVATE-RESTORATION"}
    elif scenario == "depth":
        override = {}
        for _ in range(66):
            override = {"nested": override}
    elif scenario == "nodes":
        override = {"nodes": [None] * 100001}
    elif scenario == "size":
        override = {"private": "PRIVATE-RESTORATION" * 600000}
    elif scenario == "scalar":
        override = None
    identifier, request, _ = restoration_source(pg, catalog, audio_manifest, manifest_override=override, forced_digest="0" * 64 if scenario == "corrupt_digest" else None)
    with pg.begin() as session:
        if scenario == "archived":
            session.get(Workflow, identifier).archived = True
        elif scenario == "unvalidated":
            session.get(WorkflowRelease, parse_id("wfr", request.source_release_id)).validated = False
    if scenario == "missing_parent":
        identifier = uuid4()
    elif scenario == "missing_source":
        request = request.model_copy(update={"source_release_id": public_id("wfr", uuid4())})
    elif scenario == "cross_parent":
        _, foreign, _ = restoration_source(pg, catalog, audio_manifest)
        request = request.model_copy(update={"source_release_id": foreign.source_release_id})
    elif scenario == "revision":
        request = request.model_copy(update={"expected_revision": 9})
    elif scenario == "digest":
        request = request.model_copy(update={"source_digest": "0" * 64})
    statements: list[str] = []
    before = state(pg)
    engine = pg.kw["bind"]

    def capture(_connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(DomainError) as caught:
            WorkflowAdministration(pg, test_settings).restore_draft(actor, identifier, request, "restore-fails-atomically", "test")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert caught.value.status == status and "PRIVATE" not in str(caught.value)
    assert not any("workflows.draft" in statement for statement in statements)
    if scenario == "size":
        assert not any(statement.startswith("SELECT workflow_releases.manifest") for statement in statements)
    assert state(pg) == before
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 0


def test_restore_leaves_existing_frozen_job_models_bindings_and_gpu_identity_untouched(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    job = JobRepository(pg).create(catalog["identity"], REQUEST, "existing-frozen-job", "test")
    with pg() as session:
        parent_id = session.get(WorkflowRelease, catalog["release_id"]).workflow_id
    identifier, request, _ = restoration_source(pg, catalog, audio_manifest, "text_to_image", parent_id=parent_id)
    before = JobRepository(pg).get(catalog["identity"], parse_id("job", job["id"]))
    with pg() as session:
        models = [(r.id, r.release_id, r.published, r.revision, r.defaults, r.channel_ids) for r in session.scalars(select(PublicModel))]
        bindings = [(r.id, r.release_id, r.desired_digest, r.verified_digest, r.runtime_approval) for r in session.scalars(select(WorkflowBinding))]
        fences = list(session.execute(select(Gpu.id, Gpu.fence)))
    WorkflowAdministration(pg, test_settings).restore_draft(actor, identifier, request, "restore-with-existing-job", "test")
    assert JobRepository(pg).get(catalog["identity"], parse_id("job", job["id"])) == before
    with pg() as session:
        assert [(r.id, r.release_id, r.published, r.revision, r.defaults, r.channel_ids) for r in session.scalars(select(PublicModel))] == models
        assert [(r.id, r.release_id, r.desired_digest, r.verified_digest, r.runtime_approval) for r in session.scalars(select(WorkflowBinding))] == bindings
        assert list(session.execute(select(Gpu.id, Gpu.fence))) == fences


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_restorations_are_one_cas_transition(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings, same_key: bool) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    identifier, request, _ = restoration_source(pg, catalog, audio_manifest)
    admin, barrier = WorkflowAdministration(pg, test_settings), threading.Barrier(2)

    def run(index: int) -> Any:
        barrier.wait(timeout=10)
        try:
            return admin.restore_draft(actor, identifier, request, "concurrent-original" if same_key else "concurrent-" + str(index), "test")
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(run, range(2)))
    assert sum(isinstance(item, dict) for item in results) == (2 if same_key else 1)
    if same_key:
        assert results[0] == results[1]
    else:
        assert "config_revision_conflict" in results
    with pg() as session:
        assert session.get(Workflow, identifier).revision == 2
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "workflow.draft_restored")) == 1


def test_concurrent_patch_or_restoration_never_silently_overwrites_the_winner(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    identifier, request, manifest = restoration_source(pg, catalog, audio_manifest)
    admin, barrier = WorkflowAdministration(pg, test_settings), threading.Barrier(2)

    def run(restore: bool) -> Any:
        barrier.wait(timeout=10)
        try:
            return admin.restore_draft(actor, identifier, request, "racing-patch", "test") if restore else admin.patch_workflow(actor, identifier, WorkflowPatch(expected_revision=1, draft={"winner": "patch"}), "test")
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(run, [True, False]))
    assert sum(isinstance(item, dict) for item in results) == 1 and "config_revision_conflict" in results
    with pg() as session:
        assert session.get(Workflow, identifier).draft == (manifest if isinstance(results[0], dict) else {"winner": "patch"})
        assert session.get(Workflow, identifier).revision == 2


@pytest.mark.parametrize("table", ["workflows", "workflow_releases", "admin_mutations"])
def test_database_lock_budget_rolls_back_and_original_request_recovers(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings, table: str) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    identifier, request, _ = restoration_source(pg, catalog, audio_manifest)
    admin = WorkflowAdministration(pg, test_settings)
    with pg.kw["bind"].connect() as blocker:
        tx = blocker.begin()
        blocker.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
        start = time.monotonic()
        try:
            with pytest.raises(DomainError) as caught:
                admin.restore_draft(actor, identifier, request, "lock-budget-original", "test")
            assert caught.value.code == "workflow_restoration_unavailable" and caught.value.status == 503
            assert time.monotonic() - start < 6
        finally:
            tx.rollback()
    assert admin.restore_draft(actor, identifier, request, "lock-budget-original", "test")["revision"] == 2


@pytest.mark.parametrize("role,allowed", [("administrator", True), ("workflow_maintainer", True), ("operator", False), ("observer", False)])
def test_http_authentication_csrf_revision_and_role_gate(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings, role: str, allowed: bool) -> None:
    admin_context(pg, test_settings, role=role)
    identifier, request, _ = restoration_source(pg, catalog, audio_manifest)
    path = f"/admin/api/v1/workflows/{public_id('wf', identifier)}/draft-restorations"
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.post(path, json=request.model_dump()).status_code == 401
        login = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD}, headers={"Origin": test_settings.public_origin}).json()
        headers = {"Origin": test_settings.public_origin, "X-CSRF-Token": login["csrf_token"], "Idempotency-Key": "restore-http-original", "If-Match": '"1"'}
        before = state(pg)
        assert client.post(path, json=request.model_dump(), headers={k: v for k, v in headers.items() if k != "X-CSRF-Token"}).status_code == 403
        if allowed:
            for missing, status in [("Origin", 403), ("Idempotency-Key", 400), ("If-Match", 428)]:
                assert client.post(path, json=request.model_dump(), headers={k: v for k, v in headers.items() if k != missing}).status_code == status
            assert client.post(path, json=request.model_dump(), headers={**headers, "If-Match": '"2"'}).status_code == 409
            assert client.post(path, json={**request.model_dump(), "draft": {}}, headers=headers).status_code == 422
            assert client.post(path, json={**request.model_dump(), "expected_revision": True}, headers=headers).status_code == 422
            result = client.post(path, json=request.model_dump(), headers=headers)
            assert result.status_code == 200 and result.json()["revision"] == 2
            assert client.post(path, json=request.model_dump(), headers=headers).json() == result.json()
            assert state(pg) == [before[0] + 1, *before[1:]]
        else:
            assert client.post(path, json=request.model_dump(), headers=headers).status_code == 403 and state(pg) == before


def test_business_worker_and_gateway_bearers_cannot_restore_drafts(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings) -> None:
    identifier, request, _ = restoration_source(pg, catalog, audio_manifest)
    credential_id, business_token = issue_secret("sk-gen")
    with pg.begin() as session:
        session.add(ApiCredential(key_id=catalog["identity"].key_id, identifier=credential_id, verifier=verifier(business_token, test_settings.credential_pepper.get_secret_value())))
    before = state(pg)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        business_headers = {"Authorization": "Bearer " + business_token}
        assert client.get("/v1/models", headers=business_headers).status_code == 200
        assert client.post(f"/admin/api/v1/workflows/{public_id('wf', identifier)}/draft-restorations", json=request.model_dump(), headers=business_headers).status_code == 401
        for kind in ["sk-gen", "worker", "gateway"]:
            _, token = issue_secret(kind)
            assert client.post(f"/admin/api/v1/workflows/{public_id('wf', identifier)}/draft-restorations", json=request.model_dump(), headers={"Authorization": "Bearer " + token}).status_code == 401
    assert state(pg) == before
