from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.admin import KeyCreate, KeyPatch
from generation_contracts.defaults import default_key_policy, default_limits
from generation_contracts.key_generation_options import KeyGenerationOptions, KeyGenerationQuery
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.admin_keys import KeyAdministration
from generation_infrastructure.key_generation_options import KeyGenerationDirectory
from generation_infrastructure.models import (
    AdminMutation,
    Attempt,
    AuditEvent,
    Channel,
    GpuOccupancy,
    Job,
    Project,
    PublicModel,
    QuotaBucket,
    UsageLedger,
    WorkerCommand,
    Workflow,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, admin_context
from tests.integration.test_audio import audio_catalog

pytestmark = pytest.mark.integration
ENDPOINT = "/admin/api/v1/key-generation-options/query"


def query(catalog: dict[str, Any], kind: str, **values: Any) -> KeyGenerationQuery:
    return KeyGenerationQuery.model_validate(
        {
            "kind": kind,
            "mode": "browse",
            "project_id": public_id("prj", catalog["identity"].project_id),
            **values,
        }
    )


def resources(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    kind: str,
    count: int = 53,
    prefix: str = "Directory",
) -> list[str]:
    identity = catalog["identity"]
    with pg.begin() as session:
        source = session.get(WorkflowRelease, catalog["release_id"])
        channel = session.get(Channel, catalog["channel_ids"][0])
        rows: list[Any] = []
        if kind == "models":
            rows = [
                PublicModel(
                    tenant_id=identity.tenant_id,
                    project_id=identity.project_id,
                    alias=f"{prefix}-{i:04}",
                    task_type=source.manifest["task_type"],
                    release_id=source.id,
                    channel_ids=[public_id("ch", channel.id)],
                    defaults={"quality": "standard"},
                    published=i % 2 == 0,
                )
                for i in range(count)
            ]
        elif kind == "channels":
            rows = [
                Channel(
                    tenant_id=identity.tenant_id,
                    project_id=identity.project_id,
                    node_id=channel.node_id,
                    gpu_id=channel.gpu_id,
                    name=f"{prefix}-{i:04}",
                    base_url="https://PRIVATE-GENERATION.invalid",
                    encrypted_auth="PRIVATE-GENERATION-CREDENTIAL",
                    enabled=i % 2 == 0,
                    draining=i % 3 == 0,
                    deleted_at=utcnow() if i % 5 == 0 else None,
                )
                for i in range(count)
            ]
        else:
            workflow = Workflow(
                tenant_id=identity.tenant_id,
                project_id=identity.project_id,
                name=prefix,
                task_type=source.manifest["task_type"],
                draft={"PRIVATE-GENERATION-GRAPH": "private"},
                archived=True,
            )
            session.add(workflow)
            session.flush()
            rows = [
                WorkflowRelease(
                    tenant_id=identity.tenant_id,
                    project_id=identity.project_id,
                    workflow_id=workflow.id,
                    version=i + 1,
                    digest=source.digest,
                    manifest=source.manifest,
                    validated=i % 2 == 0,
                    validation={"synthetic": True},
                )
                for i in range(count)
            ]
        session.add_all(rows)
        session.flush()
        return [
            row.alias
            if kind == "models"
            else public_id("ch" if kind == "channels" else "wfr", row.id)
            for row in rows
        ]


@pytest.mark.parametrize("kind", ["models", "workflow_releases", "channels"])
def test_later_pages_exact_values_and_private_sql_projection(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str
) -> None:
    identifiers = resources(pg, catalog, kind)
    statements: list[str] = []

    def capture(_c: Any, _cu: Any, statement: str, _p: Any, _cx: Any, _many: Any) -> None:
        statements.append(statement)

    engine = pg.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        service = KeyGenerationDirectory(pg, test_settings)
        pages = [service.query(query(catalog, kind, q="Directory", page=page)) for page in [1, 2]]
        exact = service.query(query(catalog, kind, mode="resolve", ids=identifiers[-2:]))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [len(page["data"]) for page in pages] == [50, 3]
    assert (
        all(page["total"] == 53 for page in pages)
        and pages[0]["has_more"]
        and not pages[1]["has_more"]
    )
    assert {row["value"] for page in pages for row in page["data"]} == set(identifiers)
    assert [row["value"] for row in exact["data"]] == identifiers[-2:] and not exact[
        "unresolved_ids"
    ]
    assert "PRIVATE-GENERATION" not in str(pages + [exact])
    assert not any(
        field in statement
        for field in [
            "workflows.draft",
            "workflow_releases.manifest",
            "channels.base_url",
            "channels.encrypted_auth",
            "api_keys.policy",
            "api_credentials.verifier",
            "nodes.inventory",
        ]
        for statement in statements
    )
    assert any("READ ONLY" in statement.upper() for statement in statements)


@pytest.mark.parametrize("kind", ["models", "workflow_releases", "channels"])
def test_1000_resolve_in_requested_order_empty_resolve_is_deny_list(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str
) -> None:
    identifiers = resources(pg, catalog, kind, 1000, "Bulk")
    service = KeyGenerationDirectory(pg, test_settings)
    result = service.query(query(catalog, kind, mode="resolve", ids=identifiers[::-1]))
    assert result["total"] == 1000 and not result["has_more"] and not result["unresolved_ids"]
    assert [row["value"] for row in result["data"]] == identifiers[::-1]
    empty = service.query(query(catalog, kind, mode="resolve"))
    assert empty["total"] == 0 and empty["data"] == [] and empty["unresolved_ids"] == []


@pytest.mark.parametrize("kind", ["models", "workflow_releases", "channels"])
def test_literal_search_does_not_expand_sql_wildcards(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str
) -> None:
    resources(pg, catalog, kind, 2, "literal%_\\ target")
    service = KeyGenerationDirectory(pg, test_settings)
    assert service.query(query(catalog, kind, q="%_\\"))["total"] == 2
    assert service.query(query(catalog, kind, q="missing%_\\"))["total"] == 0


@pytest.mark.parametrize("kind", ["models", "workflow_releases", "channels"])
def test_other_project_values_are_unresolved_without_existence_leak(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str
) -> None:
    with pg.begin() as session:
        project = Project(
            tenant_id=catalog["identity"].tenant_id, name="Other project", limits=default_limits()
        )
        session.add(project)
        session.flush()
        identifier = project.id
    from generation_application.ports import BusinessIdentity

    identity = catalog["identity"]
    other = {
        **catalog,
        "identity": BusinessIdentity(
            identity.tenant_id, identifier, identity.key_id, identity.policy
        ),
    }
    # Releases/models reference a same-project parent, preserving every FK.
    with pg.begin() as session:
        source = session.get(WorkflowRelease, catalog["release_id"])
        workflow = Workflow(
            tenant_id=identity.tenant_id,
            project_id=identifier,
            name="Other workflow",
            task_type=source.manifest["task_type"],
            draft={},
        )
        session.add(workflow)
        session.flush()
        release = WorkflowRelease(
            tenant_id=identity.tenant_id,
            project_id=identifier,
            workflow_id=workflow.id,
            version=1,
            digest=source.digest,
            manifest=source.manifest,
            validated=True,
            validation={},
        )
        session.add(release)
        session.flush()
        other["release_id"] = release.id
    values = resources(pg, other, kind, 1, "Other")
    service = KeyGenerationDirectory(pg, test_settings)
    result = service.query(query(catalog, kind, mode="resolve", ids=values))
    assert result["data"] == [] and result["unresolved_ids"] == values and result["total"] == 0
    with pytest.raises(DomainError) as error:
        service.query(query(catalog, kind, project_id=public_id("prj", uuid4())))
    assert error.value.status == 404


def test_metadata_states_are_visible_without_granting_runtime_approval(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    service = KeyGenerationDirectory(pg, test_settings)
    for kind in ["models", "workflow_releases", "channels"]:
        resources(pg, catalog, kind, 2)
    models = service.query(query(catalog, "models", q="Directory"))["data"]
    releases = service.query(query(catalog, "workflow_releases", q="Directory"))["data"]
    channels = service.query(query(catalog, "channels", q="Directory"))["data"]
    assert {row["published"] for row in models} == {True, False}
    assert {row["validated"] for row in releases} == {True, False} and all(
        row["archived"] for row in releases
    )
    assert channels[0]["archived"] and channels[0]["draining"] and not channels[1]["enabled"]
    assert all("runtime_approval" not in str(row) for row in models + releases + channels)


@pytest.mark.parametrize(
    "kind,table",
    [
        ("models", "public_models"),
        ("workflow_releases", "workflow_releases"),
        ("channels", "channels"),
    ],
)
def test_actual_database_lock_budget_is_safe_and_recovers(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    kind: str,
    table: str,
) -> None:
    engine = pg.kw["bind"]
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("LOCK TABLE " + table + " IN ACCESS EXCLUSIVE MODE"))
        try:
            with pytest.raises(DomainError) as error:
                KeyGenerationDirectory(pg, test_settings).query(query(catalog, kind))
            assert (
                error.value.status == 503
                and error.value.code == "key_generation_directory_unavailable"
            )
            assert table not in error.value.message
        finally:
            transaction.rollback()
    assert KeyGenerationDirectory(pg, test_settings).query(query(catalog, kind))["total"] > 0


def test_count_and_page_keep_readonly_repeatable_snapshot(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    values = resources(pg, catalog, "models", 1, "Snapshot")
    counted, proceed = threading.Event(), threading.Event()
    engine = pg.kw["bind"]

    def after_count(_c: Any, _cu: Any, statement: str, _p: Any, _cx: Any, _many: Any) -> None:
        if "SELECT count(*)" in statement and "public_models" in statement and not counted.is_set():
            counted.set()
            assert proceed.wait(5)

    event.listen(engine, "after_cursor_execute", after_count)
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                KeyGenerationDirectory(pg, test_settings).query,
                query(catalog, "models", q="Snapshot"),
            )
            assert counted.wait(5)
            try:
                with pg.begin() as session:
                    session.scalar(
                        select(PublicModel).where(PublicModel.alias == values[0])
                    ).alias = "Renamed"
            finally:
                proceed.set()
            result = future.result(5)
    finally:
        proceed.set()
        event.remove(engine, "after_cursor_execute", after_count)
    assert result["total"] == 1 and result["data"][0]["value"] == values[0]
    assert (
        KeyGenerationDirectory(pg, test_settings).query(query(catalog, "models", q="Snapshot"))[
            "total"
        ]
        == 0
    )


def test_reads_leave_jobs_quota_audit_and_gpu_unchanged_and_bad_projection_fails_closed(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    tables = [
        AdminMutation,
        Attempt,
        AuditEvent,
        Job,
        QuotaBucket,
        GpuOccupancy,
        UsageLedger,
        WorkerCommand,
    ]

    def snapshot() -> list[int]:
        with pg() as session:
            return [session.scalar(select(func.count()).select_from(table)) for table in tables]

    before = snapshot()
    service = KeyGenerationDirectory(pg, test_settings)
    for kind in ["models", "workflow_releases", "channels"]:
        service.query(query(catalog, kind))
    assert snapshot() == before
    with pg.begin() as session:
        session.get(Channel, catalog["channel_ids"][0]).name = ""
    with pytest.raises(DomainError) as error:
        service.query(query(catalog, "channels"))
    assert error.value.status == 503 and "PRIVATE" not in error.value.message


@pytest.mark.parametrize("role", ["administrator", "operator", "observer"])
def test_http_permission_csrf_and_strict_shape(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str
) -> None:
    admin_context(pg, test_settings, role=role)
    body = query(catalog, "channels").model_dump()
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.post(ENDPOINT, json=body).status_code == 401
        login = client.post(
            "/admin/api/v1/session",
            json={"username": "test-admin", "password": PASSWORD},
            headers={"Origin": test_settings.public_origin},
        )
        assert login.status_code == 200
        assert (
            client.post(
                ENDPOINT, json=body, headers={"Origin": test_settings.public_origin}
            ).status_code
            == 403
        )
        headers = {
            "Origin": test_settings.public_origin,
            "X-CSRF-Token": login.json()["csrf_token"],
        }
        result = client.post(ENDPOINT, json=body, headers=headers)
        if role != "administrator":
            assert result.status_code == 403
        else:
            assert (
                result.status_code == 200
                and KeyGenerationOptions.model_validate(result.json()).total == 2
            )
            for bad in [
                {**body, "private": "DO-NOT-ECHO"},
                {**body, "page": True},
                {**body, "mode": "resolve", "ids": ["mdl_" + "a" * 32]},
                {**body, "mode": "resolve", "q": "audio"},
            ]:
                response = client.post(ENDPOINT, json=bad, headers=headers)
                assert response.status_code == 422 and "DO-NOT-ECHO" not in response.text


@pytest.mark.parametrize("prefix", ["sk-gen", "worker", "gateway"])
def test_business_worker_gateway_credentials_cannot_read_directory(
    pg: sessionmaker[Session], test_settings: Settings, prefix: str
) -> None:
    _, secret = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert (
            client.post(
                ENDPOINT,
                json={"kind": "models", "mode": "browse", "project_id": public_id("prj", uuid4())},
                headers={
                    "Authorization": "Bearer " + secret,
                    "Origin": test_settings.public_origin,
                },
            ).status_code
            == 401
        )


def test_selected_directory_values_enforce_audio_model_version_channel_and_empty_policy(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    audio_manifest: dict[str, Any],
    test_settings: Settings,
) -> None:
    catalog = audio_catalog.__wrapped__(pg, catalog, audio_manifest)
    actor, _, _ = admin_context(pg, test_settings)
    service = KeyGenerationDirectory(pg, test_settings)
    policy = default_key_policy()
    policy["scopes"].append("audio:create")
    policy["spec_limits"]["audio_max_milliseconds"] = 2000
    policy["limits"]["daily_audio_milliseconds"] = {"mode": "bounded", "value": 5000}
    wanted = {
        "models": ["synthetic-audio"],
        "workflow_releases": [public_id("wfr", catalog["release_id"])],
        "channels": [public_id("ch", catalog["channel_ids"][0])],
    }
    for kind, values in wanted.items():
        result = service.query(query(catalog, kind, mode="resolve", ids=values))
        policy[kind]["selected_ids"] = [row["value"] for row in result["data"]]
    administration = KeyAdministration(pg, test_settings)
    created = administration.create_key(
        actor,
        KeyCreate.model_validate(
            {
                "tenant_id": public_id("ten", catalog["identity"].tenant_id),
                "project_id": public_id("prj", catalog["identity"].project_id),
                "name": "Audio directory key",
                "policy": policy,
            }
        ),
        "generation-key",
        "test",
    )
    headers = {
        "Authorization": "Bearer " + created["secret"],
        "Idempotency-Key": "directory-audio-job",
    }
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        response = client.post(
            "/v1/jobs",
            json={
                "model": "synthetic-audio",
                "task_type": "text_to_audio",
                "input": {"prompt": "Synthetic audio"},
            },
            headers=headers,
        )
        assert response.status_code == 202
        with pg() as session:
            job = session.get(Job, parse_id("job", response.json()["id"]))
            assert (
                job.eligible_channels == wanted["channels"]
                and job.release_id == catalog["release_id"]
                and job.task_type == "text_to_audio"
            )
        denied = client.post(
            "/v1/jobs",
            json={
                "model": "synthetic-image",
                "task_type": "text_to_image",
                "input": {"prompt": "Synthetic"},
            },
            headers={**headers, "Idempotency-Key": "directory-other-model"},
        )
        assert denied.status_code == 403
        no_version = deepcopy(policy)
        no_version["workflow_releases"]["selected_ids"] = []
        administration.patch_key(
            actor,
            parse_id("key", created["key"]["id"]),
            KeyPatch.model_validate({"expected_revision": 1, "policy": no_version}),
            "test",
        )
        denied = client.post(
            "/v1/jobs",
            json={
                "model": "synthetic-audio",
                "task_type": "text_to_audio",
                "input": {"prompt": "Synthetic"},
            },
            headers={**headers, "Idempotency-Key": "directory-no-version"},
        )
        assert denied.status_code == 403
