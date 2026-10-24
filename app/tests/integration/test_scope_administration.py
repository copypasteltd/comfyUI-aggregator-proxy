from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.admin import KeyCreate
from generation_contracts.defaults import default_key_policy, default_limits
from generation_contracts.scope_administration import (
    ProjectCreate,
    ScopePatch,
    ScopeQuery,
    TenantCreate,
)
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id
from generation_infrastructure import admin_scopes as scope_module
from generation_infrastructure.admin_keys import KeyAdministration
from generation_infrastructure.admin_scopes import ScopeAdministration
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminMutation,
    AdminUser,
    ApiKey,
    AuditEvent,
    GpuOccupancy,
    Project,
    QuotaBucket,
    RateState,
    Tenant,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_http_auth import client as client
from test_http_auth import key_body, login

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Scope protocol fixture"}}


def actor(pg: sessionmaker[Session]) -> AdminIdentity:
    with pg.begin() as session:
        user = AdminUser(username=uuid4().hex, password_hash="unused synthetic fixture", role="administrator")
        session.add(user)
        session.flush()
        return AdminIdentity(user.id, user.username, user.role, uuid4())


def local_key(pg: sessionmaker[Session], settings: Settings, who: AdminIdentity, tenant_id: UUID, project_id: UUID) -> str:
    policy = default_key_policy()
    policy["limits"]["rpm"]["value"] = 100
    policy["limits"]["burst"]["value"] = 100
    return KeyAdministration(pg, settings).create_key(who, KeyCreate(name="Synthetic scoped key", tenant_id=public_id("ten", tenant_id), project_id=public_id("prj", project_id), policy=policy), str(uuid4()), "scope-test")["secret"]


def rates(pg: sessionmaker[Session]) -> dict[str, Any]:
    with pg() as session:
        return {row.identity: (row.tokens, row.updated_at, row.recent) for row in session.scalars(select(RateState).where(~RateState.identity.startswith("admin")))}


def test_scope_concurrent_idempotency_revision_noop_and_rollback(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, who = ScopeAdministration(pg, test_settings), actor(pg)
    body = TenantCreate(name="  New tenant  ", limits=default_limits())
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: admin.create_tenant(who, body, "same-create", "scope-test"), range(6)))
    assert len({result["id"] for result in results}) == 1
    identifier = parse_id("ten", results[0]["id"])
    with pytest.raises(DomainError, match="不同请求"):
        admin.create_tenant(who, TenantCreate(name="Changed", limits=default_limits()), "same-create", "scope-test")
    unchanged = admin.patch(who, identifier, ScopePatch(expected_revision=1, name="New tenant"), "noop", "scope-test", project=False)
    assert unchanged["revision"] == 1

    def update(name: str) -> str:
        try:
            return str(admin.patch(who, identifier, ScopePatch(expected_revision=1, name=name), str(uuid4()), "scope-test", project=False)["revision"])
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(update, ["First", "Second"])) == ["2", "config_revision_conflict"]
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 3
    def failed_change(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected post-write failure")
    monkeypatch.setattr("generation_infrastructure.admin_scopes.record_change", failed_change)
    with pytest.raises(RuntimeError):
        admin.patch(who, identifier, ScopePatch(expected_revision=2, enabled=False), "failed-change", "scope-test", project=False)
    with pg() as session:
        assert session.get(Tenant, identifier).revision == 2 and session.get(Tenant, identifier).enabled
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 3


def test_paged_directory_options_search_disabled_and_immutable_parent(pg: sessionmaker[Session], test_settings: Settings) -> None:
    admin, who = ScopeAdministration(pg, test_settings), actor(pg)
    created = admin.create_tenant(who, TenantCreate(name="Tenant %_ literal", limits=default_limits()), "tenant", "scope-test")
    tenant_id = parse_id("ten", created["id"])
    with pg.begin() as session:
        session.add_all([Project(tenant_id=tenant_id, name=f"Project {index:03}", limits=default_limits(), enabled=index != 110) for index in range(112)])
    pages = [admin.options(ScopeQuery(page=index)) for index in (1, 2, 3)]
    assert [len(page["data"]) for page in pages] == [50, 50, 11]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert len({row["project_id"] for page in pages for row in page["data"]}) == 111
    beyond = admin.options(ScopeQuery(q="Project 111"))["data"][0]
    assert admin.options(ScopeQuery(q=beyond["project_id"]))["data"] == [beyond]
    disabled = admin.options(ScopeQuery(enabled=False))["data"]
    assert len(disabled) == 1 and not disabled[0]["project_enabled"]
    assert admin.options(ScopeQuery(project_id=disabled[0]["project_id"]))["data"] == disabled
    assert len(admin.tenants(ScopeQuery(q="%_"))["data"]) == 1
    assert not admin.tenants(ScopeQuery(q="%x"))["data"]
    assert len(admin.projects(ScopeQuery(tenant_id=created["id"], page=3))["data"]) == 12
    admin.patch(who, tenant_id, ScopePatch(expected_revision=1, enabled=False), "disable-parent", "scope-test", project=False)
    assert not admin.options(ScopeQuery())["data"]
    assert len(admin.options(ScopeQuery(enabled=False))["data"]) == 50
    assert admin.get(parse_id("prj", beyond["project_id"]), project=True)["tenant_enabled"] is False
    with pytest.raises(DomainError) as missing:
        admin.create_project(who, ProjectCreate(name="Orphan", tenant_id=public_id("ten", uuid4()), limits=default_limits()), "orphan", "scope-test")
    assert missing.value.code == "scope_not_found"


def test_usage_snapshot_timezone_and_limits_edits_preserve_buckets(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 10, 23, 18, tzinfo=UTC)
    monkeypatch.setattr("generation_infrastructure.admin_scopes.utcnow", lambda: fixed)
    identity = catalog["identity"]
    with pg.begin() as session:
        session.add_all([QuotaBucket(scope_kind="1_tenant", scope_id=identity.tenant_id, period="active", unfinished_jobs=4, generating_jobs=1, dispatch_virtual_milliseconds=12345, dispatch_sequence=7), QuotaBucket(scope_kind="1_tenant", scope_id=identity.tenant_id, period="2026-10-24", daily_jobs=11, daily_image_count=12, daily_video_milliseconds=13000), QuotaBucket(scope_kind="1_tenant", scope_id=identity.tenant_id, period="2026-10-23", daily_jobs=99)])
    admin, who = ScopeAdministration(pg, test_settings), actor(pg)
    snapshot = admin.get(identity.tenant_id, project=False)
    assert snapshot["usage"] == {"period": "2026-10-24", "unfinished_jobs": 4, "generating_jobs": 1, "daily_jobs": 11, "daily_image_count": 12, "daily_video_milliseconds": 13000, "daily_audio_milliseconds": 0}
    limits = deepcopy(snapshot["limits"])
    limits["unfinished_jobs"]["value"] = 0
    for revision, enabled in [(1, False), (2, True)]:
        result = admin.patch(who, identity.tenant_id, ScopePatch(expected_revision=revision, enabled=enabled, limits=limits), "change-" + str(revision), "scope-test", project=False)
        assert result["usage"] == snapshot["usage"]
    with pg() as session:
        active = session.scalar(select(QuotaBucket).where(QuotaBucket.scope_kind == "1_tenant", QuotaBucket.period == "active"))
        assert (active.dispatch_virtual_milliseconds, active.dispatch_sequence) == (12345, 7)


@pytest.mark.parametrize("scope", ["tenant", "project", "key"])
def test_parent_and_key_rate_rejection_rolls_back_all_levels(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, scope: str) -> None:
    identity, who = catalog["identity"], actor(pg)
    token = local_key(pg, test_settings, who, identity.tenant_id, identity.project_id)
    auth = AuthRepository(pg, test_settings)
    with pg.begin() as session:
        row = session.get(Tenant, identity.tenant_id) if scope == "tenant" else session.get(Project, identity.project_id) if scope == "project" else session.scalar(select(ApiKey).where(ApiKey.id != identity.key_id))
        if scope == "key":
            policy = deepcopy(row.policy)
            policy["limits"]["rpm"]["value"] = 1
            policy["limits"]["burst"]["value"] = 1
            row.policy = policy
        else:
            limits = deepcopy(row.limits)
            limits["rpm"]["value"] = 1
            limits["burst"]["value"] = 1
            row.limits = limits
    auth.business(token, "127.0.0.1")
    before = rates(pg)
    assert len(before) == 3
    with pytest.raises(DomainError) as rejected:
        auth.business(token, "127.0.0.1")
    assert rejected.value.code == "rate_limited" and rates(pg) == before


@pytest.mark.parametrize("scope", ["tenant", "project"])
def test_parallel_keys_share_parent_budget_and_other_tenant_is_independent(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, scope: str) -> None:
    identity, who = catalog["identity"], actor(pg)
    with pg.begin() as session:
        row = session.get(Tenant, identity.tenant_id) if scope == "tenant" else session.get(Project, identity.project_id)
        limits = deepcopy(row.limits)
        limits["rpm"]["value"] = 3
        limits["burst"]["value"] = 3
        row.limits = limits
        other = Tenant(name="Other tenant", limits=default_limits())
        session.add(other)
        session.flush()
        project = Project(tenant_id=other.id, name="Other project", limits=default_limits())
        session.add(project)
        session.flush()
        other_ids = (other.id, project.id)
    tokens = [local_key(pg, test_settings, who, identity.tenant_id, identity.project_id) for _ in range(8)]
    auth = AuthRepository(pg, test_settings)
    def request(token: str) -> str:
        try:
            auth.business(token, "127.0.0.1")
            return "accepted"
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(request, tokens))
    assert results.count("accepted") == 3 and results.count("rate_limited") == 5
    assert request(local_key(pg, test_settings, who, *other_ids)) == "accepted"
    admin = ScopeAdministration(pg, test_settings)
    identifier = identity.tenant_id if scope == "tenant" else identity.project_id
    before = rates(pg)
    for revision, enabled in [(1, False), (2, True)]:
        admin.patch(who, identifier, ScopePatch(expected_revision=revision, enabled=enabled), "toggle-" + str(revision), "scope-test", project=scope == "project")
    assert rates(pg) == before and request(tokens[0]) == "rate_limited"


@pytest.mark.parametrize("scope", ["tenant", "project"])
def test_disabling_scope_blocks_new_calls_but_preserves_accepted_job_and_gpu(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, scope: str) -> None:
    identity, who = catalog["identity"], actor(pg)
    token = local_key(pg, test_settings, who, identity.tenant_id, identity.project_id)
    job = JobRepository(pg).create(identity, REQUEST, "before-disable", "scope-test")
    ScopeAdministration(pg, test_settings).patch(who, identity.tenant_id if scope == "tenant" else identity.project_id, ScopePatch(expected_revision=1, enabled=False), "disable", "scope-test", project=scope == "project")
    with pytest.raises(DomainError) as denied:
        AuthRepository(pg, test_settings).business(token, "127.0.0.1")
    assert denied.value.status == 401
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == job["id"]
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is not None


def test_scope_http_create_permissions_csrf_revision_and_pagination(client: TestClient, pg: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [datetime(2026, 10, 24, 4, tzinfo=UTC)]
    monkeypatch.setattr(scope_module, "utcnow", lambda: clock[0])
    login(client)
    body = {"name": "Tenant through HTTP", "limits": default_limits()}
    assert client.post("/admin/api/v1/tenants", json=body).status_code == 400
    assert client.post("/admin/api/v1/tenants", json=body, headers={"Idempotency-Key": "csrf", "X-CSRF-Token": "wrong"}).status_code == 403
    response = client.post("/admin/api/v1/tenants", json=body, headers={"Idempotency-Key": "scopes-http-create-tenant"})
    assert response.status_code == 201, response.text
    tenant = response.json()
    assert tenant["observed_at"] == int(clock[0].timestamp())
    clock[0] += timedelta(seconds=1)
    # Mutation replay preserves its original response; fresh reads have a new snapshot.
    assert client.post("/admin/api/v1/tenants", json=body, headers={"Idempotency-Key": "scopes-http-create-tenant"}).json() == tenant
    path = "/admin/api/v1/tenants/" + tenant["id"]
    patch = {"expected_revision": 1, "enabled": False}
    assert client.patch(path, json=patch, headers={"Idempotency-Key": "scopes-http-patch-tenant"}).status_code == 428
    assert client.patch(path, json=patch, headers={"Idempotency-Key": "scopes-http-patch-tenant", "If-Match": '"2"'}).status_code in (409, 412)
    assert client.patch(path, json=patch, headers={"Idempotency-Key": "scopes-http-patch-tenant", "If-Match": '"1"'}).json()["revision"] == 2
    created = client.post("/admin/api/v1/projects", json={**body, "name": "Project through HTTP", "tenant_id": tenant["id"]}, headers={"Idempotency-Key": "scopes-http-create-project"})
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["tenant_enabled"] is False
    assert project["observed_at"] == int(clock[0].timestamp())
    clock[0] += timedelta(seconds=1)
    assert client.get("/admin/api/v1/projects/" + project["id"]).json() == {**project, "observed_at": int(clock[0].timestamp())}
    clock[0] += timedelta(seconds=1)
    page = client.get("/admin/api/v1/projects", params={"tenant_id": tenant["id"]}).json()
    assert page["observed_at"] == int(clock[0].timestamp())
    assert page["data"] == [{**project, "observed_at": int(clock[0].timestamp())}]
    assert client.get("/admin/api/v1/tenants", params={"page": 10001}).status_code == 422
    login(client, "test-observer")
    assert client.get(path).status_code == 200
    assert client.get("/admin/api/v1/scopes/options").status_code == 200
    assert client.post("/admin/api/v1/tenants", json=body, headers={"Idempotency-Key": "forbidden"}).status_code == 403
    assert client.patch(path, json={"expected_revision": 2, "enabled": True}, headers={"Idempotency-Key": "forbidden", "If-Match": '"2"'}).status_code == 403


def test_business_key_cannot_access_any_scope_management_route(client: TestClient, catalog: dict[str, Any]) -> None:
    login(client)
    result = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": "scopes-http-create-key"}).json()
    client.cookies.clear()
    headers = {"Authorization": "Bearer " + result["secret"], "Idempotency-Key": "must-not-authorize"}
    for path in ("tenants", "projects", "scopes/options", "tenants/" + public_id("ten", catalog["identity"].tenant_id), "projects/" + public_id("prj", catalog["identity"].project_id)):
        assert client.get("/admin/api/v1/" + path, headers=headers).status_code == 401
    assert client.post("/admin/api/v1/tenants", json={"name": "Unauthorized", "limits": default_limits()}, headers=headers).status_code == 401
