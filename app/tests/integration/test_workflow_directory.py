from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.catalog import WorkflowSync
from generation_contracts.workflow_directory import WorkflowDirectoryQuery, WorkflowSyncOptionsQuery
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.models import (
    AuditEvent,
    Channel,
    GpuOccupancy,
    Job,
    ManagementOperation,
    Project,
    UsageLedger,
    WorkerCommand,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, admin_context

pytestmark = pytest.mark.integration


def context(
    pg: sessionmaker[Session], catalog: dict[str, Any], kind: str = "release", **kw: Any
) -> WorkflowSyncOptionsQuery:
    with pg() as session:
        workflow = session.get(WorkflowRelease, catalog["release_id"]).workflow_id
    return WorkflowSyncOptionsQuery.model_validate(
        {
            "kind": kind,
            "project_id": public_id("prj", catalog["identity"].project_id),
            "workflow_id": public_id("wf", workflow),
            **kw,
        }
    )


def seed_directory(
    pg: sessionmaker[Session], catalog: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    with pg.begin() as session:
        source = session.get(WorkflowRelease, catalog["release_id"])
        workflows = [
            Workflow(
                tenant_id=source.tenant_id,
                project_id=source.project_id,
                name=f"Directory {i:03d} literal%_",
                task_type="text_to_audio",
                draft={"private": "PRIVATE-DIRECTORY"},
            )
            for i in range(53)
        ]
        other = Project(tenant_id=source.tenant_id, name="Other directory", limits={})
        session.add_all([*workflows, other])
        session.flush()
        releases = [
            WorkflowRelease(
                tenant_id=source.tenant_id,
                project_id=source.project_id,
                workflow_id=workflows[0].id,
                version=i,
                digest=fingerprint(manifest),
                manifest=manifest,
                validated=i != 104,
                validation={"private": "PRIVATE-DIRECTORY"},
            )
            for i in range(1, 105)
        ]
        channels = [
            Channel(
                tenant_id=source.tenant_id,
                project_id=source.project_id,
                node_id=catalog["node_id"],
                gpu_id=catalog["gpu_id"],
                name=f"Directory channel {i:03d} literal%_",
                base_url="http://private.invalid/PRIVATE-DIRECTORY",
                enabled=False,
                draining=True,
            )
            for i in range(101)
        ]
        channels += [
            Channel(
                tenant_id=source.tenant_id,
                project_id=other.id,
                node_id=catalog["node_id"],
                gpu_id=catalog["gpu_id"],
                name="Directory foreign",
                base_url="http://private.invalid",
            ),
            Channel(
                tenant_id=source.tenant_id,
                project_id=source.project_id,
                node_id=catalog["node_id"],
                gpu_id=catalog["gpu_id"],
                name="Directory deleted",
                base_url="http://private.invalid",
                deleted_at=utcnow(),
            ),
        ]
        session.add_all([*releases, *channels])
        session.flush()
        return {
            "workflow": public_id("wf", workflows[0].id),
            "other_project": public_id("prj", other.id),
            "releases": [public_id("wfr", r.id) for r in releases],
            "channels": [public_id("ch", c.id) for c in channels],
        }


def state(pg: sessionmaker[Session]) -> list[int]:
    with pg() as session:
        return [
            session.scalar(select(func.count()).select_from(model))
            for model in [
                AuditEvent,
                Job,
                UsageLedger,
                GpuOccupancy,
                ManagementOperation,
                WorkerCommand,
            ]
        ]


def test_catalog_intersection_literal_search_paging_and_read_only(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    audio_manifest: dict[str, Any],
    test_settings: Settings,
) -> None:
    from generation_infrastructure.workflow_directory import WorkflowDirectory

    data = seed_directory(pg, catalog, audio_manifest)
    reads = WorkflowDirectory(pg, test_settings)
    before = state(pg)
    query = {
        "q": "literal%_",
        "project_id": public_id("prj", catalog["identity"].project_id),
        "task_type": "text_to_audio",
        "status": "active",
    }
    first, second = [
        reads.catalog(WorkflowDirectoryQuery.model_validate({**query, "page": p})) for p in [1, 2]
    ]
    assert (
        first["total"] == second["total"] == 53
        and len(first["data"]) == 50
        and len(second["data"]) == 3
    )
    assert (
        first["has_more"]
        and not second["has_more"]
        and len({r["id"] for r in first["data"] + second["data"]}) == 53
    )
    assert (
        first["filters"] == query
        and first["snapshot_at"] > 0
        and "PRIVATE-DIRECTORY" not in str([first, second])
    )
    assert (
        reads.catalog(
            WorkflowDirectoryQuery.model_validate({**query, "workflow_id": data["workflow"]})
        )["total"]
        == 1
    )
    assert (
        reads.catalog(WorkflowDirectoryQuery.model_validate({**query, "status": "archived"}))[
            "total"
        ]
        == 0
    )
    assert (
        reads.catalog(
            WorkflowDirectoryQuery.model_validate({**query, "project_id": data["other_project"]})
        )["total"]
        == 0
    )
    assert state(pg) == before


def test_more_than_100_releases_and_channel_pages_exact_resolution(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    audio_manifest: dict[str, Any],
    test_settings: Settings,
) -> None:
    from generation_infrastructure.workflow_directory import WorkflowDirectory

    data = seed_directory(pg, catalog, audio_manifest)
    reads = WorkflowDirectory(pg, test_settings)
    before = state(pg)
    base = {"workflow_id": data["workflow"]}
    pages = [reads.options(context(pg, catalog, page=p, **base)) for p in [1, 2, 3]]
    assert [len(p["data"]) for p in pages] == [50, 50, 3] and all(p["total"] == 103 for p in pages)
    assert pages[-1]["data"][-1]["id"] == data["releases"][0]
    invalid = reads.options(context(pg, catalog, ids=[data["releases"][-1]], **base))["data"][0]
    assert not invalid["validated"] and "manifest" not in invalid and "validation" not in invalid
    assert reads.options(context(pg, catalog, q="%_", **base))["total"] == 0
    channels = [
        reads.options(context(pg, catalog, "channel", q="literal%_", page=p, **base))
        for p in [1, 2, 3]
    ]
    assert [len(p["data"]) for p in channels] == [50, 50, 1]
    exact = [
        reads.options(context(pg, catalog, "channel", ids=data["channels"][i : i + 50], **base))
        for i in [0, 50]
    ]
    assert sum(len(p["data"]) for p in exact) == 100 and all(
        not r["enabled"] and r["draining"] for p in exact for r in p["data"]
    )
    assert (
        reads.options(context(pg, catalog, "channel", ids=data["channels"][-2:], **base))["total"]
        == 0
    )
    with pytest.raises(DomainError) as error:
        reads.options(context(pg, catalog, project_id=data["other_project"], **base))
    assert (
        error.value.status == 404
        and state(pg) == before
        and "PRIVATE-DIRECTORY" not in str(pages + channels + exact)
    )


def test_safe_directory_audio_batch_original_key_replay_and_atomic_conflict(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    audio_manifest: dict[str, Any],
    test_settings: Settings,
) -> None:
    from generation_infrastructure.workflow_directory import WorkflowDirectory

    actor, _, _ = admin_context(pg, test_settings)
    data = seed_directory(pg, catalog, audio_manifest)
    reads = WorkflowDirectory(pg, test_settings)
    admin = CatalogAdministration(pg, test_settings)
    release = reads.options(
        context(pg, catalog, workflow_id=data["workflow"], ids=data["releases"][:1])
    )["data"][0]
    targets = reads.options(context(pg, catalog, "channel", ids=data["channels"][:2]))["data"]
    with pg.begin() as session:
        for target in targets:
            session.add(
                WorkflowBinding(
                    tenant_id=catalog["identity"].tenant_id,
                    project_id=catalog["identity"].project_id,
                    channel_id=parse_id("ch", target["id"]),
                    release_id=parse_id("wfr", release["id"]),
                    desired_digest=release["digest"],
                    verified_digest=release["digest"],
                    runtime_approval={"synthetic": True},
                )
            )
    body = {
        "direction": "push",
        "release_id": release["id"],
        "release_digest": release["digest"],
        "targets": [{"channel_id": c["id"], "expected_revision": c["revision"]} for c in targets],
    }
    conflict = {
        **body,
        "targets": [*body["targets"][:1], {**body["targets"][1], "expected_revision": 999}],
    }
    before = state(pg)
    with pytest.raises(DomainError) as error:
        admin.synchronize(
            actor, WorkflowSync.model_validate(conflict), str(uuid4()), "req-conflict"
        )
    assert error.value.status == 409 and state(pg) == before
    token = str(uuid4())
    first = admin.synchronize(actor, WorkflowSync.model_validate(body), token, "req-first")
    assert admin.synchronize(actor, WorkflowSync.model_validate(body), token, "req-replay") == first
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(ManagementOperation)) == 1
        commands = list(session.scalars(select(WorkerCommand)))
        assert len(commands) == 2
        assert all(c.payload["manifest"]["task_type"] == "text_to_audio" for c in commands)
        for target in targets:
            channel = session.get(Channel, parse_id("ch", target["id"]))
            assert (
                channel.revision == target["revision"] + 1
                and channel.draining
                and not channel.enabled
            )
            binding = session.scalar(
                select(WorkflowBinding).where(
                    WorkflowBinding.channel_id == channel.id,
                    WorkflowBinding.release_id == parse_id("wfr", release["id"]),
                )
            )
            assert binding.verified_digest is None and binding.runtime_approval is None
    assert state(pg)[1:4] == before[1:4]


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("administrator", True),
        ("workflow_maintainer", True),
        ("operator", False),
        ("observer", False),
    ],
)
def test_http_role_boundaries_and_strict_query(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    role: str,
    allowed: bool,
) -> None:
    admin_context(pg, test_settings, role=role)
    query = context(pg, catalog).model_dump(exclude_none=True, exclude={"ids"})
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get("/admin/api/v1/workflow-sync-options", params=query).status_code == 401
        assert (
            client.post(
                "/admin/api/v1/session",
                json={"username": "test-admin", "password": PASSWORD},
                headers={"Origin": test_settings.public_origin},
            ).status_code
            == 200
        )
        assert client.get("/admin/api/v1/workflows", params={"page": 1}).status_code == 200
        assert client.get("/admin/api/v1/workflow-sync-options", params=query).status_code == (
            200 if allowed else 403
        )
        if allowed:
            for fields in [
                {"page": "true"},
                {"page": 1001},
                {"q": "x\n"},
                {"extra": "private"},
                {"ids": "wf_" + "a" * 32},
                {"ids": public_id("wfr", catalog["release_id"]), "page": 2},
            ]:
                response = client.get(
                    "/admin/api/v1/workflow-sync-options", params={**query, **fields}
                )
                assert response.status_code == 422 and "PRIVATE-DIRECTORY" not in response.text


@pytest.mark.parametrize(
    "table,kind", [("workflows", "list"), ("workflow_releases", "release"), ("channels", "channel")]
)
def test_real_lock_budget_sanitized_503_recovery(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    table: str,
    kind: str,
) -> None:
    from generation_infrastructure.workflow_directory import WorkflowDirectory

    reads = WorkflowDirectory(pg, test_settings)
    options_query = context(pg, catalog, kind) if kind != "list" else None
    with pg.kw["bind"].connect() as connection:
        transaction = connection.begin()
        connection.execute(text("LOCK TABLE " + table + " IN ACCESS EXCLUSIVE MODE"))
        try:
            with pytest.raises(DomainError) as error:
                if kind == "list":
                    reads.catalog(WorkflowDirectoryQuery())
                else:
                    reads.options(options_query)
            assert (
                error.value.status == 503
                and error.value.code == "workflow_directory_unavailable"
                and "SQL" not in error.value.message
            )
        finally:
            transaction.rollback()
    assert reads.catalog(WorkflowDirectoryQuery())["total"] == 1


def test_count_and_rows_share_repeatable_read_snapshot(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    from generation_infrastructure.workflow_directory import WorkflowDirectory

    counted, proceed = threading.Event(), threading.Event()
    engine = pg.kw["bind"]
    with pg() as session:
        workflow = session.get(WorkflowRelease, catalog["release_id"]).workflow_id

    def pause(
        connection: Any, _cursor: Any, statement: str, _params: Any, _context: Any, _many: Any
    ) -> None:
        if "SELECT count(*)" in statement and "workflows" in statement and not counted.is_set():
            assert connection.execute(text("SHOW transaction_read_only")).scalar() == "on"
            assert (
                connection.execute(text("SHOW transaction_isolation")).scalar() == "repeatable read"
            )
            counted.set()
            assert proceed.wait(5)

    event.listen(engine, "after_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                WorkflowDirectory(pg, test_settings).catalog,
                WorkflowDirectoryQuery(q="Synthetic workflow"),
            )
            assert counted.wait(5)
            with pg.begin() as session:
                session.get(Workflow, workflow).name = "New workflow name"
            proceed.set()
            result = future.result(timeout=10)
    finally:
        proceed.set()
        event.remove(engine, "after_cursor_execute", pause)
    assert result["total"] == 1 and result["data"][0]["name"] == "Synthetic workflow"
    assert (
        WorkflowDirectory(pg, test_settings).catalog(
            WorkflowDirectoryQuery(q="Synthetic workflow")
        )["total"]
        == 0
    )
