from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.workflow_history import WorkflowDiffQuery, WorkflowHistoryQuery
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id
from generation_infrastructure.models import ApiCredential, Workflow, WorkflowRelease
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.settings import Settings
from generation_infrastructure.workflow_history import WorkflowHistory, difference_page
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, admin_context
from tests.integration.test_workflow_directory import seed_directory, state

pytestmark = pytest.mark.integration


def pair(pg: sessionmaker[Session], catalog: dict[str, Any], before: Any, after: Any) -> tuple[Any, str, str]:
    with pg.begin() as session:
        source = session.get(WorkflowRelease, catalog["release_id"])
        workflow = Workflow(tenant_id=source.tenant_id, project_id=source.project_id, name="Synthetic history audio", task_type="text_to_audio", draft=after)
        session.add(workflow)
        session.flush()
        releases = [WorkflowRelease(tenant_id=source.tenant_id, project_id=source.project_id, workflow_id=workflow.id, version=i, digest=fingerprint(value), manifest=value, validated=True, validation={"private": "PRIVATE-HISTORY"}) for i, value in enumerate([before, after], 1)]
        session.add_all(releases)
        session.flush()
        return workflow.id, public_id("wfr", releases[0].id), public_id("wfr", releases[1].id)


def test_history_beyond_100_versions_safe_projection_and_read_only(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings) -> None:
    data = seed_directory(pg, catalog, audio_manifest)
    reads, before, sql = WorkflowHistory(pg, test_settings), state(pg), []

    def capture(_connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        sql.append(statement)

    engine = pg.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        pages = [reads.history(parse_id("wf", data["workflow"]), WorkflowHistoryQuery(page=n)) for n in [1, 2, 3, 4]]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [len(p["data"]) for p in pages] == [50, 50, 4, 0]
    assert pages[0]["total"] == 104 and pages[0]["data"][0]["version"] == 104
    assert not pages[0]["data"][0]["validated"] and not pages[2]["has_more"]
    assert [r["version"] for p in pages for r in p["data"]] == list(range(104, 0, -1))
    assert all(all(token not in statement for token in [".manifest", ".draft", ".validation", ".base_url"]) for statement in sql)
    assert "PRIVATE-HISTORY" not in str(pages) and "PRIVATE-DIRECTORY" not in str(pages)
    assert state(pg) == before


def test_audio_parameters_graph_dependencies_and_exact_zero(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any], test_settings: Settings) -> None:
    changed = copy.deepcopy(audio_manifest)
    changed["workflow_api"]["1"]["inputs"]["seed"] = 42
    changed["presets"][0]["sample_rate"] = 48000
    changed["dependency_lock"]["comfy_commit"] = "b" * 40
    wid, base, target = pair(pg, catalog, audio_manifest, changed)
    reads, before = WorkflowHistory(pg, test_settings), state(pg)
    result = reads.compare(wid, WorkflowDiffQuery(base_release_id=base, target_release_id=target))
    assert result["total"] == 3 and result["counts"] == {"added": 0, "removed": 0, "changed": 3}
    assert {r["path"]: (r["before_preview"], r["after_preview"]) for r in result["data"]}["/presets/0/sample_rate"] == ("24000", "48000")
    assert result["target"]["id"] == target
    draft = reads.compare(wid, WorkflowDiffQuery(base_release_id=base))
    assert draft["data"] == result["data"] and draft["target"] == {"kind": "draft", "revision": 1}
    zero = reads.compare(wid, WorkflowDiffQuery(base_release_id=target))
    assert zero["total"] == 0 and zero["data"] == [] and not zero["has_more"]
    assert state(pg) == before


def test_json_semantics_paging_and_value_escaping(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    before = {"removed": None, "bool": True, "same_number": 1, "list": [1, 2], "object": {"b": 2, "a": 1}, **{f"k{i:03d}": i for i in range(60)}}
    after = {"added": None, "bool": 1, "same_number": 1.0, "list": [1, 3, None], "object": {"a": 1, "b": 2}, **{f"k{i:03d}": i + 1 for i in range(60)}}
    key = "long/~/\n\u0081" + "字" * 520
    after[key] = "<script>window.PREVIEW_EXECUTED=true</script>" + "x" * 3000
    wid, base, target = pair(pg, catalog, before, after)
    reads = WorkflowHistory(pg, test_settings)
    pages = [reads.compare(wid, WorkflowDiffQuery(base_release_id=base, target_release_id=target, page=n)) for n in [1, 2, 3]]
    assert [len(p["data"]) for p in pages] == [50, 16, 0]
    assert pages[0]["total"] == 66 and pages[0]["counts"] == {"added": 3, "removed": 1, "changed": 62}
    rows = {r["path"]: r for p in pages for r in p["data"]}
    assert rows["/added"]["before_type"] is None and rows["/added"]["after_preview"] == "null"
    assert rows["/removed"]["before_preview"] == "null" and rows["/removed"]["after_type"] is None
    assert rows["/bool"]["before_type"] == "boolean" and rows["/bool"]["after_type"] == "number"
    long = next(row for row in rows.values() if row["path_truncated"])
    assert len(long["path"]) == 512 and "~1" in long["path"] and "~0" in long["path"]
    assert "\\u0081" in long["path"] and "\\u000a" in long["path"]
    assert long["after_truncated"] and len(long["after_preview"]) == 2048
    assert reads.compare(wid, WorkflowDiffQuery(base_release_id=base, target_release_id=target, page=2))["data"] == pages[1]["data"]


@pytest.mark.parametrize("which", ["base", "target", "draft"])
def test_sql_size_rejected_before_loading_documents(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, which: str) -> None:
    large = {"private": "PRIVATE-HISTORY" + "x" * (10 * 1024 * 1024)}
    wid, base, target = pair(pg, catalog, large if which == "base" else {}, large if which in ["target", "draft"] else {})
    statements: list[str] = []
    engine = pg.kw["bind"]

    def capture(_connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(DomainError) as error:
            WorkflowHistory(pg, test_settings).compare(wid, WorkflowDiffQuery(base_release_id=base, target_release_id=None if which == "draft" else target))
        assert error.value.status == 413 and "PRIVATE-HISTORY" not in error.value.message
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any(s.startswith("SELECT workflow_releases.manifest") or s.startswith("SELECT workflows.draft") for s in statements)


@pytest.mark.parametrize("kind", ["depth", "nodes", "combined_paths"])
def test_structure_and_comparison_budgets_are_explicit(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str) -> None:
    if kind == "depth":
        before: Any = {}
        for _ in range(66):
            before = {"branch": before}
        after: Any = {}
    elif kind == "nodes":
        before, after = {"items": [None] * 100001}, {}
    else:
        before, after = ({f"a{i}": 0 for i in range(51000)}, {f"b{i}": 0 for i in range(51000)})
    wid, base, _ = pair(pg, catalog, before, after)
    with pytest.raises(DomainError) as error:
        WorkflowHistory(pg, test_settings).compare(wid, WorkflowDiffQuery(base_release_id=base))
    assert error.value.status == 413 and error.value.code == "workflow_comparison_limit"


@pytest.mark.parametrize("scenario", ["missing_parent", "missing_base", "other_base", "other_target"])
def test_missing_or_cross_parent_resources_are_404(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, scenario: str) -> None:
    wid, base, target = pair(pg, catalog, {}, {"a": 1})
    _, foreign, _ = pair(pg, catalog, {}, {})
    query = WorkflowDiffQuery(base_release_id=foreign if scenario == "other_base" else public_id("wfr", uuid4()) if scenario == "missing_base" else base, target_release_id=foreign if scenario == "other_target" else target)
    with pytest.raises(DomainError) as error:
        WorkflowHistory(pg, test_settings).compare(uuid4() if scenario == "missing_parent" else wid, query)
    assert error.value.status == 404


@pytest.mark.parametrize("table", ["workflows", "workflow_releases"])
def test_real_lock_budget_is_safe_and_recovers(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, table: str) -> None:
    wid, base, _ = pair(pg, catalog, {}, {})
    reads, query = WorkflowHistory(pg, test_settings), WorkflowDiffQuery(base_release_id=base)
    with pg.kw["bind"].connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
        start = time.monotonic()
        try:
            for action in [lambda: reads.history(wid, WorkflowHistoryQuery()), lambda: reads.compare(wid, query)]:
                with pytest.raises(DomainError) as error:
                    action()
                assert error.value.status == 503 and "SQL" not in error.value.message
        finally:
            tx.rollback()
        assert time.monotonic() - start < 7
    assert reads.compare(wid, query)["total"] == 0


@pytest.mark.parametrize("kind", ["history", "draft"])
def test_read_only_repeatable_snapshot(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str) -> None:
    wid, base, _ = pair(pg, catalog, {}, {"old": 1})
    counted, proceed = threading.Event(), threading.Event()
    engine = pg.kw["bind"]
    before = state(pg)

    def pause(connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        marker = "SELECT count(*)" if kind == "history" else "SELECT workflows.id, workflows.project_id, workflows.revision"
        if marker in statement and not counted.is_set():
            assert connection.execute(text("SHOW transaction_read_only")).scalar() == "on"
            assert connection.execute(text("SHOW transaction_isolation")).scalar() == "repeatable read"
            assert connection.execute(text("SHOW lock_timeout")).scalar() == "1s"
            assert connection.execute(text("SHOW statement_timeout")).scalar() == "5s"
            counted.set()
            assert proceed.wait(5)

    event.listen(engine, "after_cursor_execute", pause)
    reads = WorkflowHistory(pg, test_settings)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(reads.history, wid, WorkflowHistoryQuery()) if kind == "history" else pool.submit(reads.compare, wid, WorkflowDiffQuery(base_release_id=base))
            assert counted.wait(5)
            with pg.begin() as session:
                workflow = session.get(Workflow, wid)
                if kind == "history":
                    session.add(WorkflowRelease(tenant_id=workflow.tenant_id, project_id=workflow.project_id, workflow_id=wid, version=3, digest=fingerprint({}), manifest={}, validated=True, validation={}))
                else:
                    workflow.draft, workflow.revision = {"new": 2}, 2
            proceed.set()
            result = future.result(timeout=10)
    finally:
        proceed.set()
        event.remove(engine, "after_cursor_execute", pause)
    if kind == "history":
        assert result["total"] == len(result["data"]) == 2
        assert reads.history(wid, WorkflowHistoryQuery())["total"] == 3
    else:
        assert result["target"]["revision"] == 1 and result["data"][0]["path"] == "/old"
        assert reads.compare(wid, WorkflowDiffQuery(base_release_id=base))["target"]["revision"] == 2
    assert state(pg) == before


def test_corrupt_projection_uses_fixed_safe_503(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    wid, _, _ = pair(pg, catalog, {}, {})
    with pg.begin() as session:
        workflow = session.get(Workflow, wid)
        session.add(WorkflowRelease(tenant_id=workflow.tenant_id, project_id=workflow.project_id, workflow_id=wid, version=3, digest="PRIVATE-HISTORY", manifest={}, validated=True, validation={}))
    with pytest.raises(DomainError) as error:
        WorkflowHistory(pg, test_settings).history(wid, WorkflowHistoryQuery())
    assert error.value.status == 503 and "PRIVATE-HISTORY" not in error.value.message


@pytest.mark.parametrize("role", ["administrator", "workflow_maintainer", "operator", "observer"])
def test_http_roles_strict_queries_and_no_session_denial(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str) -> None:
    wid, base, _ = pair(pg, catalog, {}, {})
    admin_context(pg, test_settings, role=role)
    url = f"/admin/api/v1/workflows/{public_id('wf', wid)}"
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get(url + "/releases").status_code == 401
        assert client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD}, headers={"Origin": test_settings.public_origin}).status_code == 200
        before = state(pg)
        assert client.get(url + "/releases").status_code == 200
        assert client.get(url + "/release-diff", params={"base_release_id": base}).status_code == 200
        for fields in [{"page": 0}, {"page": 1001}, {"page": "true"}, {"page": 1.5}, {"extra": "PRIVATE-HISTORY"}]:
            assert client.get(url + "/releases", params=fields).status_code == 422
            response = client.get(url + "/release-diff", params={"base_release_id": base, **fields})
            assert response.status_code == 422 and "PRIVATE-HISTORY" not in response.text
        assert client.get(url + "/release-diff").status_code == 422
        assert client.get(url + "/release-diff", params={"base_release_id": "PRIVATE-HISTORY"}).status_code == 422
        assert state(pg) == before


def test_valid_business_key_cannot_use_management_history(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    wid, base, _ = pair(pg, catalog, {}, {})
    identifier, token = issue_secret("sk-gen")
    with pg.begin() as session:
        session.add(ApiCredential(key_id=catalog["identity"].key_id, identifier=identifier, verifier=verifier(token, test_settings.credential_pepper.get_secret_value())))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/v1/models", headers=headers).status_code == 200
        url = f"/admin/api/v1/workflows/{public_id('wf', wid)}"
        assert client.get(url + "/releases", headers=headers).status_code == 401
        assert client.get(url + "/release-diff", params={"base_release_id": base}, headers=headers).status_code == 401


def test_scalar_root_and_added_branch_are_one_change() -> None:
    counts, rows = difference_page({"a": {"deep": [1, None]}}, {}, 1)
    assert counts == {"added": 0, "removed": 1, "changed": 0} and len(rows) == 1
    counts, rows = difference_page(None, 1, 1)
    assert counts["changed"] == 1 and rows[0]["path"] == "/" and rows[0]["before_type"] == "null"
