from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.catalog import ModelCreate, ModelPatch
from generation_contracts.model_catalog import (
    ModelCatalogQuery,
    ModelMappingOptions,
    ModelMappingQuery,
)
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, public_id
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.model_mapping_options import ModelMappingReads
from generation_infrastructure.models import (
    AuditEvent,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    Project,
    PublicModel,
    UsageLedger,
    Workflow,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, admin_context

pytestmark = pytest.mark.integration


def synthetic_release(pg: sessionmaker[Session], catalog: dict[str, Any], manifest: dict[str, Any]) -> str:
    with pg.begin() as session:
        source = session.get(WorkflowRelease, catalog["release_id"])
        workflow = Workflow(tenant_id=source.tenant_id, project_id=source.project_id, name="Synthetic catalog release", task_type=manifest["task_type"], draft={})
        session.add(workflow)
        session.flush()
        release = WorkflowRelease(tenant_id=source.tenant_id, project_id=source.project_id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"synthetic_fixture": True})
        session.add(release)
        session.flush()
        return public_id("wfr", release.id)


def query(catalog: dict[str, Any], kind: str, **fields: Any) -> ModelMappingQuery:
    return ModelMappingQuery.model_validate(
        {"kind": kind, "project_id": public_id("prj", catalog["identity"].project_id), **fields}
    )


def more_directories(
    pg: sessionmaker[Session], catalog: dict[str, Any]
) -> tuple[list[str], list[str]]:
    identity = catalog["identity"]
    with pg.begin() as session:
        template = session.get(Channel, catalog["channel_ids"][0])
        channels, workflows = [], []
        for i in range(53):
            channel = Channel(
                tenant_id=identity.tenant_id,
                project_id=identity.project_id,
                name=f"Catalog channel {i:03}",
                node_id=template.node_id,
                gpu_id=template.gpu_id,
                base_url="https://PRIVATE-CATALOG.invalid",
                encrypted_auth="PRIVATE-CATALOG-CREDENTIAL",
                enabled=i != 1,
                draining=i == 2,
            )
            workflow = Workflow(
                tenant_id=identity.tenant_id,
                project_id=identity.project_id,
                name=f"Catalog workflow {i:03}",
                task_type="text_to_audio",
                draft={"PRIVATE-CATALOG-GRAPH": "private"},
            )
            session.add_all([channel, workflow])
            session.flush()
            channels.append(public_id("ch", channel.id))
            workflows.append(public_id("wf", workflow.id))
        return channels, workflows


@pytest.mark.parametrize("kind", ["workflow", "channel"])
def test_options_browse_after_first_50_and_resolve_selected_ids_without_private_columns(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str
) -> None:
    channel_ids, workflow_ids = more_directories(pg, catalog)
    statements: list[str] = []
    engine = pg.kw["bind"]

    def capture(_c: Any, _cu: Any, statement: str, _p: Any, _cx: Any, _m: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        reads = ModelMappingReads(pg, test_settings)
        a, b = [
            reads.options(query(catalog, kind, q="Catalog " + kind, page=page)) for page in [1, 2]
        ]
        selected = reads.options(
            query(catalog, kind, ids=(channel_ids if kind == "channel" else workflow_ids)[-2:])
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert a["total"] == b["total"] == 53 and len(a["data"]) == 50 and a["has_more"]
    assert (
        len(b["data"]) == 3
        and not b["has_more"]
        and len({item["id"] for item in a["data"] + b["data"]}) == 53
    )
    assert len(selected["data"]) == 2 and selected["total"] == 2
    assert "PRIVATE-CATALOG" not in str([a, b, selected])
    assert not any(
        field in statement
        for statement in statements
        for field in [
            "workflows.draft",
            "channels.base_url",
            "channels.encrypted_auth",
            "channels.observation",
            "nodes.inventory",
        ]
    )


@pytest.mark.parametrize("kind", ["workflow", "channel"])
def test_option_search_uses_literal_percent_underscore_and_escape(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str
) -> None:
    channels, workflows = more_directories(pg, catalog)
    from generation_domain.values import parse_id

    table, identifier = (
        (Channel, parse_id("ch", channels[0]))
        if kind == "channel"
        else (Workflow, parse_id("wf", workflows[0]))
    )
    with pg.begin() as session:
        session.get(table, identifier).name = "literal%_\\ target"
    reads = ModelMappingReads(pg, test_settings)
    assert reads.options(query(catalog, kind, q="%_\\"))["total"] == 1
    assert reads.options(query(catalog, kind, q="literal%_\\ target"))["total"] == 1


def test_release_browsing_reaches_beyond_100_and_exact_resolves_parent(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    with pg.begin() as session:
        source = session.get(WorkflowRelease, catalog["release_id"])
        workflow_id = public_id("wf", source.workflow_id)
        for version in range(2, 104):
            session.add(
                WorkflowRelease(
                    tenant_id=source.tenant_id,
                    project_id=source.project_id,
                    workflow_id=source.workflow_id,
                    version=version,
                    digest=fingerprint({"version": version}),
                    manifest={"PRIVATE-CATALOG-GRAPH": True},
                    validated=True,
                    validation={"PRIVATE-CATALOG-VALIDATION": True},
                )
            )
    reads = ModelMappingReads(pg, test_settings)
    pages = [
        reads.options(query(catalog, "release", workflow_id=workflow_id, page=n)) for n in [1, 2, 3]
    ]
    assert all(page["total"] == 103 for page in pages) and [
        len(page["data"]) for page in pages
    ] == [50, 50, 3]
    assert pages[-1]["data"][-1]["id"] == public_id("wfr", catalog["release_id"])
    exact = reads.options(query(catalog, "release", ids=[public_id("wfr", catalog["release_id"])]))
    assert exact["data"][0]["workflow_id"] == workflow_id and "PRIVATE-CATALOG" not in str(pages)


@pytest.mark.parametrize(
    "task",
    [
        "text_to_image",
        "image_to_image",
        "references_to_image",
        "text_to_video",
        "references_to_video",
        "text_to_audio",
    ],
)
def test_all_six_media_types_offer_safe_frozen_presets_that_existing_writes_accept(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, task: str
) -> None:
    with pg() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        manifest = deepcopy(release.manifest)
        manifest["task_type"] = task
        preset: dict[str, Any] = {
            "quality": "standard",
            "values": {"private": "PRIVATE-CATALOG-PRESET"},
        }
        if task == "text_to_audio":
            preset.update(seconds=2, audio_format="wav", sample_rate=24000, channels=1)
        else:
            preset["size"] = "1280x720" if "video" in task else "1024x1024"
        if "video" in task:
            preset["seconds"] = 2
        manifest["presets"] = [preset]
    release_id = synthetic_release(pg, catalog, manifest)
    reads = ModelMappingReads(pg, test_settings)
    result = reads.options(
        query(catalog, "preset", release_id=release_id)
    )
    assert (
        result["total"] == 1
        and result["data"][0]["task_type"] == task
        and "PRIVATE-CATALOG" not in str(result)
    )
    option = result["data"][0]
    assert option["usable"] and ("audio" in option) == (task == "text_to_audio")
    actor, _, _ = admin_context(pg, test_settings)
    service = CatalogAdministration(pg, test_settings)
    body = {
        "tenant_id": public_id("ten", catalog["identity"].tenant_id),
        "project_id": public_id("prj", catalog["identity"].project_id),
        "alias": "catalog-frozen-" + task,
        "release_id": option["release_id"],
        "channel_ids": [public_id("ch", catalog["channel_ids"][0])],
        "defaults": option["defaults"],
    }
    created = service.create_model(
        actor, ModelCreate.model_validate(body), str(uuid4()), "synthetic-catalog"
    )
    assert (
        created["defaults"] == option["defaults"]
        and created["task_type"] == task
        and not created["published"]
    )
    from generation_domain.values import parse_id

    patched = service.patch_model(
        actor,
        parse_id("mdl", created["id"]),
        ModelPatch(
            expected_revision=created["revision"],
            release_id=body["release_id"],
            channel_ids=body["channel_ids"],
            defaults=option["defaults"],
        ),
        "synthetic-catalog-patch",
    )
    assert patched["defaults"] == option["defaults"] and not patched["published"]


def test_preset_projection_paginates_and_marks_all_duplicate_specs_unusable(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    with pg() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        manifest = deepcopy(release.manifest)
        presets = [
            {
                "quality": "standard",
                "size": str(128 + i) + "x128",
                "values": {"private": "PRIVATE-CATALOG-PRESET"},
            }
            for i in range(53)
        ]
        presets += [deepcopy(presets[0])]
        manifest["presets"] = presets
    release_id = synthetic_release(pg, catalog, manifest)
    reads = ModelMappingReads(pg, test_settings)
    a, b = [
        reads.options(
            query(catalog, "preset", release_id=release_id, page=n)
        )
        for n in [1, 2]
    ]
    assert a["total"] == b["total"] == 54 and len(a["data"]) == 50 and len(b["data"]) == 4
    assert (
        not a["data"][0]["usable"]
        and not b["data"][-1]["usable"]
        and all(item["usable"] for item in a["data"][1:] + b["data"][:-1])
    )
    assert "PRIVATE-CATALOG" not in str([a, b])


def test_cross_project_parents_exact_ids_and_model_channel_filter_do_not_leak(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    with pg.begin() as session:
        other = Project(
            tenant_id=catalog["identity"].tenant_id, name="Other catalog project", limits={}
        )
        session.add(other)
        session.flush()
        other_id = public_id("prj", other.id)
        source = session.get(WorkflowRelease, catalog["release_id"])
        workflow_id = public_id("wf", source.workflow_id)
        model = session.scalar(select(PublicModel))
        model_id = public_id("mdl", model.id)
        model.channel_ids = [public_id("ch", catalog["channel_ids"][0])]
    reads = ModelMappingReads(pg, test_settings)
    assert reads.options(query(catalog, "channel", model_id=model_id))["total"] == 1
    for kind, ids in [
        ("channel", [public_id("ch", catalog["channel_ids"][0])]),
        ("workflow", [workflow_id]),
        ("release", [public_id("wfr", catalog["release_id"])]),
    ]:
        assert (
            reads.options(ModelMappingQuery(kind=kind, project_id=other_id, ids=ids))["total"] == 0
        )
    for fields in [
        {"kind": "release", "workflow_id": workflow_id},
        {"kind": "preset", "release_id": public_id("wfr", catalog["release_id"])},
        {"kind": "channel", "model_id": model_id},
    ]:
        with pytest.raises(DomainError) as error:
            reads.options(ModelMappingQuery(project_id=other_id, **fields))
        assert error.value.status == 404


def test_archived_and_unvalidated_items_are_explicit_when_resolved(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    with pg.begin() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        release.validated = False
        workflow = session.get(Workflow, release.workflow_id)
        workflow.archived = True
        workflow_id = public_id("wf", workflow.id)
    reads = ModelMappingReads(pg, test_settings)
    assert reads.options(query(catalog, "workflow"))["total"] == 0
    assert reads.options(query(catalog, "workflow", ids=[workflow_id]))["data"][0]["archived"]
    assert reads.options(query(catalog, "release", workflow_id=workflow_id))["total"] == 0
    assert not reads.options(
        query(catalog, "release", ids=[public_id("wfr", catalog["release_id"])])
    )["data"][0]["validated"]
    with pytest.raises(DomainError) as error:
        reads.options(query(catalog, "preset", release_id=public_id("wfr", catalog["release_id"])))
    assert error.value.status == 404


def test_model_catalog_filters_pagination_and_business_state_are_read_only(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    def counts() -> list[Any]:
        with pg() as session:
            return [
                session.scalar(select(func.count()).select_from(table))
                for table in [Job, AuditEvent, UsageLedger, GpuOccupancy]
            ] + [session.get(Gpu, catalog["gpu_id"]).fence]

    with pg.begin() as session:
        source = session.scalar(select(PublicModel))
        for i in range(53):
            session.add(
                PublicModel(
                    tenant_id=source.tenant_id,
                    project_id=source.project_id,
                    alias=f"catalog-{i:03}",
                    task_type=source.task_type,
                    release_id=source.release_id,
                    channel_ids=source.channel_ids,
                    defaults=source.defaults,
                    published=i % 2 == 0,
                )
            )
    before = counts()
    service = CatalogAdministration(pg, test_settings)
    a, b = [service.models(query=ModelCatalogQuery(q="catalog-", page=n)) for n in [1, 2]]
    assert a["total"] == b["total"] == 53 and len(a["data"]) == 50 and len(b["data"]) == 3
    filtered = service.models(
        query=ModelCatalogQuery(
            q="catalog-",
            status="published",
            project_id=public_id("prj", catalog["identity"].project_id),
            task_type="text_to_image",
        )
    )
    assert filtered["total"] == 27
    assert service.models(query=ModelCatalogQuery(model_id=a["data"][0]["id"]))["total"] == 1
    ModelMappingReads(pg, test_settings).options(query(catalog, "workflow"))
    assert counts() == before


@pytest.mark.parametrize("role", ["administrator", "operator", "workflow_maintainer", "observer"])
def test_http_roles_strict_queries_and_repeated_ids(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str
) -> None:
    admin_context(pg, test_settings, role=role)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert (
            client.get(
                "/admin/api/v1/model-mapping-options",
                params={
                    "kind": "channel",
                    "project_id": public_id("prj", catalog["identity"].project_id),
                },
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/admin/api/v1/session",
                json={"username": "test-admin", "password": PASSWORD},
                headers={"Origin": test_settings.public_origin},
            ).status_code
            == 200
        )
        options = client.get(
            "/admin/api/v1/model-mapping-options",
            params=[
                ("kind", "channel"),
                ("project_id", public_id("prj", catalog["identity"].project_id)),
                *(("ids", public_id("ch", value)) for value in catalog["channel_ids"]),
            ],
        )
        assert (
            options.status_code == 200
            and ModelMappingOptions.model_validate(options.json()).total == 2
        )
        assert (
            client.get("/admin/api/v1/public-models", params={"status": "published"}).status_code
            == 200
        )
        for endpoint, params in [
            ("public-models", {"page": 1001}),
            ("public-models", {"q": " padded "}),
            ("public-models", {"private": "DO-NOT-ECHO"}),
            (
                "model-mapping-options",
                {"kind": "release", "project_id": public_id("prj", catalog["identity"].project_id)},
            ),
            (
                "model-mapping-options",
                {
                    "kind": "channel",
                    "project_id": public_id("prj", catalog["identity"].project_id),
                    "private": "DO-NOT-ECHO",
                },
            ),
        ]:
            response = client.get("/admin/api/v1/" + endpoint, params=params)
            assert response.status_code == 422 and "DO-NOT-ECHO" not in response.text


@pytest.mark.parametrize("prefix", ["sk-gen", "worker", "gateway"])
def test_non_admin_identities_cannot_access_model_mapping(
    pg: sessionmaker[Session], test_settings: Settings, prefix: str
) -> None:
    _, secret = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert (
            client.get(
                "/admin/api/v1/model-mapping-options",
                params={"kind": "workflow", "project_id": public_id("prj", uuid4())},
                headers={"Authorization": "Bearer " + secret},
            ).status_code
            == 401
        )


@pytest.mark.parametrize("kind,table", [("workflow", "workflows"), ("model", "public_models")])
def test_real_lock_budget_returns_safe_503_then_recovers(
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
                if kind == "model":
                    CatalogAdministration(pg, test_settings).models()
                else:
                    ModelMappingReads(pg, test_settings).options(query(catalog, kind))
            assert (
                error.value.status == 503
                and error.value.code == "model_catalog_unavailable"
                and "SQL" not in error.value.message
            )
        finally:
            transaction.rollback()
    assert ModelMappingReads(pg, test_settings).options(query(catalog, "workflow"))["total"] == 1


def test_count_and_rows_use_same_read_only_snapshot(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    counted, proceed = threading.Event(), threading.Event()
    engine = pg.kw["bind"]
    with pg() as session:
        workflow_id = session.get(WorkflowRelease, catalog["release_id"]).workflow_id

    def after_count(_c: Any, _cu: Any, statement: str, _p: Any, _cx: Any, _many: Any) -> None:
        if "SELECT count(*)" in statement and "workflows" in statement and not counted.is_set():
            counted.set()
            assert proceed.wait(5)

    event.listen(engine, "after_cursor_execute", after_count)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                ModelMappingReads(pg, test_settings).options,
                query(catalog, "workflow", q="Synthetic workflow"),
            )
            assert counted.wait(5)
            with pg.begin() as session:
                session.get(Workflow, workflow_id).name = "Updated catalog name"
            proceed.set()
            result = future.result(timeout=10)
    finally:
        proceed.set()
        event.remove(engine, "after_cursor_execute", after_count)
    assert result["total"] == 1 and result["data"][0]["name"] == "Synthetic workflow"
    assert (
        ModelMappingReads(pg, test_settings).options(
            query(catalog, "workflow", q="Synthetic workflow")
        )["total"]
        == 0
    )


@pytest.mark.parametrize(
    "presets",
    [
        None,
        {},
        [{"quality": "standard", "size": "1024x1024", "seconds": "PRIVATE-CATALOG", "values": {}}],
    ],
)
def test_corrupt_preset_metadata_fails_without_private_echo(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, presets: Any
) -> None:
    with pg() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        manifest = {**release.manifest, "presets": presets}
    release_id = synthetic_release(pg, catalog, manifest)
    with pytest.raises(DomainError) as error:
        ModelMappingReads(pg, test_settings).options(
            query(catalog, "preset", release_id=release_id)
        )
    assert error.value.status == 503 and "PRIVATE-CATALOG" not in error.value.message
