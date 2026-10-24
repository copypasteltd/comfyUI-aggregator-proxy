from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.admin import WorkflowPatch
from generation_contracts.catalog import BindRelease, ModelCreate, WorkflowSync
from generation_contracts.workflow_lifecycle import WorkflowLifecycle
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.admin_workflows import WorkflowAdministration
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import (
    AdminMutation,
    ApiCredential,
    AuditEvent,
    ManagementOperation,
    PublicModel,
    WorkerCommand,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.validation_jobs import ValidationJobs
from sqlalchemy import event, func, select, text

from tests.integration.test_admin_controls import PASSWORD, REQUEST, admin_context
from tests.integration.test_dispatch_transactions import leases, refresh_node
from tests.integration.test_workflow_directory import state
from tests.integration.test_workflow_restoration import KINDS, restoration_source

pytestmark = pytest.mark.integration


def base_parent(pg, catalog):
    with pg.begin() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        model = session.scalar(select(PublicModel).where(PublicModel.release_id == release.id))
        model.published = False
        return release.workflow_id, model.id


def invoke(admin, actor, identifier, archived=True, revision=1, key="workflow-lifecycle-test"):
    return admin.lifecycle(
        actor,
        identifier,
        WorkflowLifecycle(expected_revision=revision, archived=archived),
        key,
        "lifecycle-request",
    )


@pytest.mark.parametrize("kind", KINDS)
def test_six_capabilities_archive_and_unarchive_exactly_once_without_loading_graph(
    pg, catalog, audio_manifest, test_settings, kind
):
    actor, _, _ = admin_context(pg, test_settings)
    identifier, _, manifest = restoration_source(pg, catalog, audio_manifest, kind)
    admin, before, statements = WorkflowAdministration(pg, test_settings), state(pg), []

    def capture(connection, cursor, statement, *args):
        statements.append(statement)

    event.listen(pg.kw["bind"], "before_cursor_execute", capture)
    try:
        archived = invoke(admin, actor, identifier)
        assert invoke(admin, actor, identifier) == archived
        restored = invoke(admin, actor, identifier, False, 2, "lifecycle-unarchive")
        assert invoke(admin, actor, identifier) == archived
    finally:
        event.remove(pg.kw["bind"], "before_cursor_execute", capture)
    assert archived == {
        "workflow_id": public_id("wf", identifier),
        "archived": True,
        "previous_revision": 1,
        "revision": 2,
    }
    assert restored["revision"] == 3 and restored["archived"] is False
    assert not any("workflows.draft" in s or "workflow_releases.manifest" in s for s in statements)
    assert state(pg) == [before[0] + 2, *before[1:]]
    with pg() as session:
        parent = session.get(Workflow, identifier)
        assert (
            parent.revision == 3
            and not parent.archived
            and parent.draft == {"old": "PRIVATE-DRAFT"}
        )
        assert (
            session.scalar(
                select(WorkflowRelease.manifest).where(WorkflowRelease.workflow_id == identifier)
            )
            == manifest
        )
        actions = list(
            session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.action.like("workflow.%"))
                .order_by(AuditEvent.created_at)
            )
        )
        assert actions == ["workflow.archived", "workflow.unarchived"]
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 2


@pytest.mark.parametrize(
    "scenario,status",
    [("missing", 404), ("stale", 409), ("same_state", 409), ("same_key_different_body", 409)],
)
def test_rejected_lifecycle_changes_leave_no_new_audit_or_idempotency(
    pg, catalog, audio_manifest, test_settings, scenario, status
):
    actor, _, _ = admin_context(pg, test_settings)
    identifier, _, _ = restoration_source(pg, catalog, audio_manifest)
    admin = WorkflowAdministration(pg, test_settings)
    revision, archived = 1, True
    if scenario == "missing":
        identifier = uuid4()
    elif scenario == "stale":
        revision = 2
    elif scenario == "same_state":
        archived = False
    else:
        invoke(admin, actor, identifier)
        revision, archived = 2, False
    before = state(pg)
    with pytest.raises(DomainError) as caught:
        invoke(admin, actor, identifier, archived, revision)
    assert caught.value.status == status and state(pg) == before
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == (
            1 if scenario == "same_key_different_body" else 0
        )


def test_published_model_blocks_archive_until_explicit_unpublish(pg, catalog, test_settings):
    actor, _, _ = admin_context(pg, test_settings)
    with pg() as session:
        identifier = session.get(WorkflowRelease, catalog["release_id"]).workflow_id
        model = session.scalar(
            select(PublicModel).where(PublicModel.release_id == catalog["release_id"])
        )
        model_id, revision = model.id, model.revision
    admin, before = WorkflowAdministration(pg, test_settings), state(pg)
    with pytest.raises(DomainError, match="公开模型"):
        invoke(admin, actor, identifier)
    assert state(pg) == before
    CatalogAdministration(pg, test_settings).model_action(
        actor, model_id, revision, False, "unpublish"
    )
    assert invoke(admin, actor, identifier)["archived"] is True


@pytest.mark.parametrize(
    "command_state,blocked",
    [
        ("pending", True),
        ("running", True),
        ("reconciling", True),
        ("succeeded", False),
        ("failed", False),
    ],
)
def test_pending_sync_blocks_archive_terminal_history_is_retained(
    pg, catalog, test_settings, command_state, blocked
):
    actor, _, _ = admin_context(pg, test_settings)
    identifier, _ = base_parent(pg, catalog)
    with pg.begin() as session:
        operation = ManagementOperation(actor_id=actor.user_id, command="apply_workflow_release")
        session.add(operation)
        session.flush()
        session.add(
            WorkerCommand(
                operation_id=operation.id,
                node_id=catalog["node_id"],
                channel_id=catalog["channel_ids"][0],
                command="apply_workflow_release",
                state=command_state,
                payload={
                    "release_id": public_id("wfr", catalog["release_id"]),
                    "PRIVATE": "not-returned",
                },
            )
        )
    before = state(pg)
    if blocked:
        with pytest.raises(DomainError, match="同步"):
            invoke(WorkflowAdministration(pg, test_settings), actor, identifier)
        assert state(pg) == before
    else:
        assert invoke(WorkflowAdministration(pg, test_settings), actor, identifier)["archived"]
    with pg() as session:
        assert session.scalar(select(WorkerCommand.state)) == command_state


@pytest.mark.parametrize(
    "action", ["patch", "freeze", "restore", "model_create", "publish", "sync", "bind", "admission"]
)
def test_archived_workflow_blocks_all_new_mutations_without_partial_effects(
    pg, catalog, audio_manifest, test_settings, action
):
    actor, _, _ = admin_context(pg, test_settings)
    identifier, model_id = base_parent(pg, catalog)
    _, source, _ = restoration_source(
        pg, catalog, audio_manifest, "text_to_image", parent_id=identifier
    )
    admin, cat = WorkflowAdministration(pg, test_settings), CatalogAdministration(pg, test_settings)
    internal_identity, _, _ = ValidationJobs(pg, test_settings)._prepare(
        actor,
        model_id,
        public_id("ch", catalog["channel_ids"][0]),
        REQUEST.copy(),
        None,
        None,
        None,
    )
    invoke(admin, actor, identifier)
    before = state(pg)
    with pg() as session:
        model = session.get(PublicModel, model_id)
        data = ModelCreate(
            tenant_id=public_id("ten", model.tenant_id),
            project_id=public_id("prj", model.project_id),
            alias="new-archived",
            release_id=public_id("wfr", catalog["release_id"]),
            channel_ids=model.channel_ids,
            defaults=model.defaults,
        )
    with pytest.raises(DomainError) as caught:
        if action == "patch":
            admin.patch_workflow(
                actor, identifier, WorkflowPatch(expected_revision=2, name="no-change"), "test"
            )
        elif action == "freeze":
            admin.release(actor, identifier, 2, "archive-freeze", "test")
        elif action == "restore":
            admin.restore_draft(
                actor,
                identifier,
                source.model_copy(update={"expected_revision": 2}),
                "archive-restore",
                "test",
            )
        elif action == "model_create":
            cat.create_model(actor, data, "archive-model", "test")
        elif action == "publish":
            cat.model_action(actor, model_id, 1, True, "test")
        elif action == "sync":
            cat.synchronize(
                actor,
                WorkflowSync(
                    direction="push",
                    release_id=source.source_release_id,
                    release_digest=source.source_digest,
                    targets=[
                        {
                            "channel_id": public_id("ch", catalog["channel_ids"][0]),
                            "expected_revision": 1,
                        }
                    ],
                ),
                "archive-sync",
                "test",
            )
        elif action == "bind":
            cat.bind(
                actor,
                catalog["channel_ids"][0],
                BindRelease(
                    expected_revision=1, release_id=public_id("wfr", catalog["release_id"])
                ),
                "archive-bind",
                "test",
            )
        else:
            JobRepository(pg).create(
                internal_identity,
                REQUEST,
                "new-validation",
                "test",
                validation_channel=catalog["channel_ids"][0],
            )
    assert caught.value.status == 409 and caught.value.code == "workflow_archived"
    assert state(pg) == before
    with pg() as session:
        assert session.get(Workflow, identifier).name != "no-change"


def test_already_accepted_task_and_original_admission_replay_survive_archive(
    pg, catalog, test_settings
):
    actor, _, _ = admin_context(pg, test_settings)
    job = JobRepository(pg).create(catalog["identity"], REQUEST, "before-archive-job", "test")
    identifier, _ = base_parent(pg, catalog)
    before = JobRepository(pg).get(catalog["identity"], parse_id("job", job["id"]))
    with pg() as session:
        bindings = [
            (b.id, b.desired_digest, b.applied_digest, b.verified_digest, b.runtime_approval)
            for b in session.scalars(select(WorkflowBinding))
        ]
    invoke(WorkflowAdministration(pg, test_settings), actor, identifier)
    assert JobRepository(pg).get(catalog["identity"], parse_id("job", job["id"])) == before
    assert (
        JobRepository(pg).create(catalog["identity"], REQUEST, "before-archive-job", "replay")["id"]
        == job["id"]
    )
    with pg() as session:
        assert [
            (b.id, b.desired_digest, b.applied_digest, b.verified_digest, b.runtime_approval)
            for b in session.scalars(select(WorkflowBinding))
        ] == bindings
    refresh_node(pg, catalog)
    claim = leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == job["id"]


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_archives_commit_one_change_and_one_original_result(
    pg, catalog, audio_manifest, test_settings, same_key
):
    actor, _, _ = admin_context(pg, test_settings)
    identifier, _, _ = restoration_source(pg, catalog, audio_manifest)
    barrier = threading.Barrier(2)

    def run(index):
        barrier.wait()
        try:
            return invoke(
                WorkflowAdministration(pg, test_settings),
                actor,
                identifier,
                key="concurrent-key-" + ("same" if same_key else str(index)),
            )
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, range(2)))
    if same_key:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(r, dict) for r in results) == 1 and 409 in results
    with pg() as session:
        assert session.get(Workflow, identifier).revision == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "workflow.archived")
            )
            == 1
        )


@pytest.mark.parametrize("kind", ["publish", "sync", "admission"])
@pytest.mark.parametrize("archive_first", [True, False])
def test_shared_gates_serialize_actual_publishing_sync_and_admission(
    pg, catalog, audio_manifest, test_settings, kind, archive_first
):
    actor, _, _ = admin_context(pg, test_settings)
    identifier, model_id = base_parent(pg, catalog)
    _, source, _ = restoration_source(
        pg, catalog, audio_manifest, "text_to_image", parent_id=identifier
    )
    with pg.begin() as session:
        for binding in session.scalars(
            select(WorkflowBinding).where(WorkflowBinding.release_id == catalog["release_id"])
        ):
            binding.runtime_approval = {"synthetic": True}
    internal_identity, _, _ = ValidationJobs(pg, test_settings)._prepare(
        actor,
        model_id,
        public_id("ch", catalog["channel_ids"][0]),
        REQUEST.copy(),
        None,
        None,
        None,
    )
    ready, proceed = threading.Event(), threading.Event()

    def hold(connection, cursor, statement, *args):
        target = (
            "UPDATE workflows SET" in statement and "archived" in statement
            if archive_first
            else statement.startswith("SELECT workflows.archived") and "FOR SHARE" in statement
        )
        if target and not ready.is_set():
            ready.set()
            assert proceed.wait(4)

    event.listen(pg.kw["bind"], "after_cursor_execute", hold)

    def archive():
        try:
            return invoke(
                WorkflowAdministration(pg, test_settings),
                actor,
                identifier,
                key="concurrent-lifecycle",
            )
        except DomainError as error:
            return error.code

    def other():
        try:
            if kind == "publish":
                return CatalogAdministration(pg, test_settings).model_action(
                    actor, model_id, 1, True, "test"
                )
            if kind == "sync":
                return CatalogAdministration(pg, test_settings).synchronize(
                    actor,
                    WorkflowSync(
                        direction="push",
                        release_id=source.source_release_id,
                        release_digest=source.source_digest,
                        targets=[
                            {
                                "channel_id": public_id("ch", catalog["channel_ids"][0]),
                                "expected_revision": 1,
                            }
                        ],
                    ),
                    "concurrent-sync",
                    "test",
                )
            return JobRepository(pg).create(
                internal_identity,
                REQUEST,
                "concurrent-validation",
                "test",
                validation_channel=catalog["channel_ids"][0],
            )
        except DomainError as error:
            return error.code

    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(archive if archive_first else other)
            assert ready.wait(4)
            second = pool.submit(other if archive_first else archive)
            time.sleep(0.15)
            proceed.set()
            first_result, second_result = first.result(8), second.result(8)
        assert isinstance(first_result, dict)
        if archive_first:
            assert second_result == "workflow_archived"
        elif kind == "publish":
            assert second_result == "workflow_published"
        elif kind == "sync":
            assert second_result == "workflow_sync_active"
        else:
            assert isinstance(second_result, dict) and second_result["archived"]
    finally:
        proceed.set()
        event.remove(pg.kw["bind"], "after_cursor_execute", hold)


@pytest.mark.parametrize("lock_table", ["workflows", "admin_mutations"])
def test_lifecycle_lock_budget_rolls_back_and_original_request_can_retry(
    pg, catalog, audio_manifest, test_settings, lock_table
):
    actor, _, _ = admin_context(pg, test_settings)
    identifier, _, _ = restoration_source(pg, catalog, audio_manifest)
    with pg.begin() as blocker:
        blocker.execute(text("LOCK TABLE " + lock_table + " IN ACCESS EXCLUSIVE MODE"))
        start = time.monotonic()
        with pytest.raises(DomainError) as caught:
            invoke(WorkflowAdministration(pg, test_settings), actor, identifier)
        assert caught.value.status == 503 and time.monotonic() - start < 4
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 0
    assert invoke(WorkflowAdministration(pg, test_settings), actor, identifier)["revision"] == 2


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("administrator", True),
        ("workflow_maintainer", True),
        ("operator", False),
        ("observer", False),
    ],
)
def test_http_roles_csrf_revision_original_replay_and_valid_business_key_isolation(
    pg, catalog, audio_manifest, test_settings, role, allowed
):
    _, _, _ = admin_context(pg, test_settings, role=role)
    identifier, _, _ = restoration_source(pg, catalog, audio_manifest)
    path = "/admin/api/v1/workflows/" + public_id("wf", identifier) + "/lifecycle"
    body = {"expected_revision": 1, "archived": True}
    credential, token = issue_secret("sk-gen")
    with pg.begin() as session:
        session.add(
            ApiCredential(
                key_id=catalog["identity"].key_id,
                identifier=credential,
                verifier=verifier(token, test_settings.credential_pepper.get_secret_value()),
            )
        )
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert (
            client.get("/v1/models", headers={"Authorization": "Bearer " + token}).status_code
            == 200
        )
        assert (
            client.post(path, json=body, headers={"Authorization": "Bearer " + token}).status_code
            == 401
        )
        login = client.post(
            "/admin/api/v1/session",
            json={"username": "test-admin", "password": PASSWORD},
            headers={"Origin": test_settings.public_origin},
        ).json()
        headers = {
            "Origin": test_settings.public_origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "lifecycle-http-original",
            "If-Match": '"1"',
        }
        if not allowed:
            assert client.post(path, json=body, headers=headers).status_code == 403
            return
        assert (
            client.post(
                path, json=body, headers={k: v for k, v in headers.items() if k != "X-CSRF-Token"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                path, json=body, headers={k: v for k, v in headers.items() if k != "If-Match"}
            ).status_code
            == 428
        )
        assert (
            client.post(path, json=body, headers={**headers, "If-Match": '"2"'}).status_code == 409
        )
        assert client.post(path, json={**body, "archived": 1}, headers=headers).status_code == 422
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 200 and result.json()["revision"] == 2
        assert client.post(path, json=body, headers=headers).json() == result.json()


def test_archived_workflow_history_and_exact_references_remain_readable_but_new_selectors_refuse_it(
    pg, catalog, audio_manifest, test_settings
):
    from generation_contracts.model_catalog import ModelMappingQuery
    from generation_contracts.workflow_directory import (
        WorkflowDirectoryQuery,
        WorkflowSyncOptionsQuery,
    )
    from generation_infrastructure.model_mapping_options import ModelMappingReads
    from generation_infrastructure.workflow_directory import WorkflowDirectory

    actor, _, _ = admin_context(pg, test_settings)
    identifier, source, _ = restoration_source(pg, catalog, audio_manifest)
    invoke(WorkflowAdministration(pg, test_settings), actor, identifier)
    project = public_id("prj", catalog["identity"].project_id)
    directory = WorkflowDirectory(pg, test_settings)
    assert any(
        row["id"] == public_id("wf", identifier)
        for row in directory.catalog(WorkflowDirectoryQuery(status="archived"))["data"]
    )
    with pytest.raises(DomainError) as caught:
        directory.options(
            WorkflowSyncOptionsQuery(
                kind="release", project_id=project, workflow_id=public_id("wf", identifier)
            )
        )
    assert caught.value.code == "workflow_archived"
    reads = ModelMappingReads(pg, test_settings)
    assert (
        reads.options(
            ModelMappingQuery(
                kind="release", project_id=project, workflow_id=public_id("wf", identifier)
            )
        )["data"]
        == []
    )
    assert (
        reads.options(
            ModelMappingQuery(kind="release", project_id=project, ids=[source.source_release_id])
        )["data"][0]["id"]
        == source.source_release_id
    )
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert (
            client.post(
                "/admin/api/v1/session",
                json={"username": "test-admin", "password": PASSWORD},
                headers={"Origin": test_settings.public_origin},
            ).status_code
            == 200
        )
        path = "/admin/api/v1/workflows/" + public_id("wf", identifier)
        assert client.get(path).status_code == 200
        assert client.get(path + "/releases").json()["total"] == 1
        assert (
            client.get("/admin/api/v1/workflow-releases/" + source.source_release_id).status_code
            == 200
        )


def test_archived_validation_request_does_not_create_internal_key_or_job(
    pg, catalog, test_settings
):
    from generation_contracts.catalog import ValidationJob
    from generation_infrastructure.models import ApiKey

    actor, _, _ = admin_context(pg, test_settings)
    identifier, model_id = base_parent(pg, catalog)
    invoke(WorkflowAdministration(pg, test_settings), actor, identifier)
    with pg() as session:
        keys = session.scalar(select(func.count()).select_from(ApiKey))
    before = state(pg)
    with pytest.raises(DomainError) as caught:
        ValidationJobs(pg, test_settings).create(
            actor,
            model_id,
            ValidationJob(channel_id=public_id("ch", catalog["channel_ids"][0]), request=REQUEST),
            "archive-validation-job",
            "test",
        )
    assert caught.value.code == "workflow_archived" and state(pg) == before
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(ApiKey)) == keys


def test_archived_workflow_blocks_retry_of_failed_sync_without_changing_lineage(
    pg, catalog, test_settings
):
    from generation_infrastructure.admin_workflow_retries import WorkflowSyncRetries

    from tests.integration.test_workflow_retries import completed_batch, retry_body

    actor, operation, _ = completed_batch(pg, catalog, test_settings, all_failed=True)
    identifier, _ = base_parent(pg, catalog)
    retries = WorkflowSyncRetries(pg, test_settings)
    request = retry_body(retries.options(operation), all_targets=True)
    invoke(WorkflowAdministration(pg, test_settings), actor, identifier)
    before = state(pg)
    with pg() as session:
        summary = session.get(ManagementOperation, operation).summary
    with pytest.raises(DomainError) as caught:
        retries.retry(actor, operation, request, "archive-failed-sync", "test")
    assert caught.value.code == "workflow_archived" and state(pg) == before
    with pg() as session:
        assert session.get(ManagementOperation, operation).summary == summary
