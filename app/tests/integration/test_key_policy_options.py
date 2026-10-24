from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generation_contracts.admin import KeyPatch
from generation_contracts.defaults import default_key_policy, default_limits
from generation_contracts.key_policy_options import KeyPolicyOptionQuery
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from generation_infrastructure.admin_keys import KeyAdministration
from generation_infrastructure.key_policy_options import KeyPolicyDirectory
from generation_infrastructure.models import (
    AdminMutation,
    ApiKey,
    AuditEvent,
    Project,
    QuotaBucket,
    Tenant,
)
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker
from test_content_management_operations import seed_output
from test_http_auth import client as client
from test_http_auth import key_body, login
from test_scope_administration import actor

pytestmark = pytest.mark.integration
PATH = "/admin/api/v1/key-policy/options/query"


def request(catalog: dict[str, Any], **changes: Any) -> KeyPolicyOptionQuery:
    return KeyPolicyOptionQuery.model_validate({"mode": "browse", "kind": "project", "tenant_id": public_id("ten", catalog["identity"].tenant_id), **changes})


def seed_source(session: Session, tenant: Any, project: Any, name: str, **changes: Any) -> ApiKey:
    key = ApiKey(tenant_id=tenant, project_id=project, name=name, policy={**default_key_policy(), "synthetic_private_marker": "must-not-appear"}, **changes)
    session.add(key)
    session.flush()
    return key


def test_directory_pages_all_projects_and_keys_and_resolves_ordered_selection(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    directory = KeyPolicyDirectory(pg, test_settings)
    identity = catalog["identity"]
    with pg.begin() as session:
        projects = [Project(tenant_id=identity.tenant_id, name=f"Directory project {index:03}", limits=default_limits(), enabled=index != 110) for index in range(112)]
        session.add_all(projects)
        session.flush()
        source_project = projects[111]
        sources = [ApiKey(tenant_id=identity.tenant_id, project_id=source_project.id, name=f"Directory source {index:03}", policy=default_key_policy()) for index in range(127)]
        session.add_all(sources)
        session.flush()
        source_ids = [public_id("key", key.id) for key in sources]
        project_id = public_id("prj", source_project.id)
    pages = [directory.query(request(catalog, page=page)) for page in (1, 2, 3)]
    assert [len(page["data"]) for page in pages] == [50, 50, 13]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert len({row["id"] for page in pages for row in page["data"]}) == 113
    key_pages = [directory.query(request(catalog, kind="creator_key", project_ids=[project_id], page=page)) for page in (1, 2, 3)]
    assert [len(page["data"]) for page in key_pages] == [50, 50, 27]
    assert len({row["id"] for page in key_pages for row in page["data"]}) == 127
    unknown = public_id("key", uuid4())
    result = directory.query(request(catalog, mode="resolve", kind="creator_key", project_ids=[project_id], ids=[source_ids[-1], unknown, source_ids[0]]))
    assert [row["id"] for row in result["data"]] == [source_ids[-1], source_ids[0]]
    assert result["unresolved_ids"] == [unknown] and not result["has_more"]
    assert directory.query(request(catalog, kind="creator_key", project_ids=[project_id], q=source_ids[-1]))["data"] == [result["data"][0]]


def test_history_lifecycle_literal_search_and_minimal_projection(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    directory = KeyPolicyDirectory(pg, test_settings)
    identity = catalog["identity"]
    with pg.begin() as session:
        archived = seed_source(session, identity.tenant_id, identity.project_id, "Literal %_ source", enabled=False, deleted_at=utcnow(), expires_at=utcnow() - timedelta(days=1), issuer="gateway:" + uuid4().hex, external_ref="synthetic-private-external-ref")
        expired = seed_source(session, identity.tenant_id, identity.project_id, "Expired source", enabled=False, expires_at=utcnow() - timedelta(days=1))
        disabled = seed_source(session, identity.tenant_id, identity.project_id, "Disabled source", enabled=False)
        ids = [public_id("key", row.id) for row in (archived, expired, disabled)]
    result = directory.query(request(catalog, kind="creator_key", project_ids=[public_id("prj", identity.project_id)], mode="resolve", ids=ids))
    assert [row["lifecycle"] for row in result["data"]] == ["archived", "expired", "disabled"]
    assert result["data"][0]["origin"] == "gateway"
    assert set(result["data"][0]) == {"id", "name", "tenant_id", "tenant_name", "project_id", "project_name", "tenant_enabled", "project_enabled", "enabled", "lifecycle", "origin"}
    literal = directory.query(request(catalog, kind="creator_key", project_ids=[public_id("prj", identity.project_id)], q="%_"))
    assert [row["id"] for row in literal["data"]] == ids[:1]
    assert "must-not-appear" not in str(result) and "synthetic-private-external-ref" not in str(result)
    with pg.begin() as session:
        session.get(Tenant, identity.tenant_id).enabled = False
    disabled_parent = directory.query(request(catalog, q=public_id("prj", identity.project_id)))["data"][0]
    assert disabled_parent["lifecycle"] == "disabled" and not disabled_parent["tenant_enabled"]


def test_empty_and_foreign_project_filters_never_fall_back_to_tenant(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    directory = KeyPolicyDirectory(pg, test_settings)
    identity = catalog["identity"]
    with pg.begin() as session:
        other = Tenant(name="Other tenant", limits=default_limits())
        session.add(other)
        session.flush()
        foreign = Project(tenant_id=other.id, name="Must not reveal foreign name", limits=default_limits())
        session.add(foreign)
        session.flush()
        key = seed_source(session, other.id, foreign.id, "Foreign secret name")
        foreign_project, foreign_key = public_id("prj", foreign.id), public_id("key", key.id)
    assert not directory.query(request(catalog, kind="creator_key", project_ids=[]))["data"]
    own_key = public_id("key", identity.key_id)
    for project_ids in ([], [foreign_project]):
        result = directory.query(request(catalog, kind="creator_key", project_ids=project_ids, mode="resolve", ids=[own_key, foreign_key]))
        assert result["data"] == [] and result["unresolved_ids"] == [own_key, foreign_key]
        assert "Foreign secret name" not in str(result)
    assert directory.query(request(catalog, mode="resolve", ids=[foreign_project]))["unresolved_ids"] == [foreign_project]
    mixed = directory.query(request(catalog, kind="creator_key", project_ids=[foreign_project, public_id("prj", identity.project_id)]))
    assert [row["id"] for row in mixed["data"]] == [own_key]
    with pytest.raises(DomainError) as missing:
        directory.query(request(catalog, tenant_id=public_id("ten", uuid4())))
    assert missing.value.status == 404


def test_full_thousand_grants_resolve_and_save_with_constant_query_count(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity, who = catalog["identity"], actor(pg)
    with pg.begin() as session:
        projects = [Project(tenant_id=identity.tenant_id, name=f"Bulk {index:04}", limits=default_limits()) for index in range(1000)]
        session.add_all(projects)
        session.flush()
        keys = [ApiKey(tenant_id=identity.tenant_id, project_id=project.id, name=f"Bulk source {index:04}", policy=default_key_policy(), enabled=False, deleted_at=utcnow()) for index, project in enumerate(projects)]
        session.add_all(keys)
        session.flush()
        project_ids, key_ids = [public_id("prj", project.id) for project in projects], [public_id("key", key.id) for key in keys]
    directory = KeyPolicyDirectory(pg, test_settings)
    statements: list[str] = []
    engine = pg.kw["bind"]
    def capture(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        resolved = directory.query(request(catalog, kind="creator_key", project_ids=project_ids, mode="resolve", ids=key_ids))
        assert len(resolved["data"]) == 1000 and not resolved["unresolved_ids"] and len(statements) <= 6
        policy = default_key_policy()
        policy["content"] = {"mode": "shared_creator_keys", "project_ids": project_ids, "creator_key_ids": key_ids}
        statements.clear()
        with pg() as session:
            KeyAdministration(pg, test_settings)._validate_policy(session, identity.tenant_id, identity.project_id, policy)
        assert len(statements) == 2
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    admin = KeyAdministration(pg, test_settings)
    patched = admin.patch_key(who, identity.key_id, KeyPatch(expected_revision=1, policy=policy), "thousand-grants")
    assert patched["revision"] == 2 and patched["policy"]["content"] == policy["content"]
    invalid = deepcopy(policy)
    invalid["content"]["project_ids"] = project_ids[1:]
    with pytest.raises(DomainError) as rejected:
        admin.patch_key(who, identity.key_id, KeyPatch(expected_revision=2, policy=invalid), "reject-stale-creator")
    assert rejected.value.code == "invalid_content_grant"
    with pg() as session:
        assert session.get(ApiKey, identity.key_id).revision == 2


def test_query_http_requires_csrf_and_role_without_write_or_idempotency_effects(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    login(client)
    data = request(catalog).model_dump()
    assert client.post(PATH, json=data, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    with pg() as session:
        before = [session.scalar(select(func.count()).select_from(model)) for model in (AdminMutation, AuditEvent, QuotaBucket)]
    for _ in range(2):
        response = client.post(PATH, json=data)
        assert response.status_code == 200, response.text
        assert response.json()["data"][0]["tenant_id"] == data["tenant_id"]
    with pg() as session:
        assert before == [session.scalar(select(func.count()).select_from(model)) for model in (AdminMutation, AuditEvent, QuotaBucket)]
    assert client.post(PATH, json={**data, "include_secrets": True}).status_code == 422
    login(client, "test-observer")
    assert client.post(PATH, json=data).status_code == 403


@pytest.mark.parametrize("realm", ["business", "worker", "gateway"])
def test_non_admin_identity_cannot_query_policy_directory(client: TestClient, catalog: dict[str, Any], realm: str) -> None:
    login(client)
    secret = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())}).json()["secret"] if realm == "business" else realm + "_" + "a" * 32 + "." + "b" * 43
    client.cookies.clear()
    response = client.post(PATH, json=request(catalog).model_dump(), headers={"Authorization": "Bearer " + secret})
    assert response.status_code == 401


def test_resolved_historical_creator_grants_control_real_content_reads_and_revocation(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    login(client)
    identity = catalog["identity"]
    project_id, creator_id = public_id("prj", identity.project_id), public_id("key", identity.key_id)
    with pg.begin() as session:
        output = seed_output(session, catalog, LocalDevelopmentStore(test_settings.local_storage_root))
        content_id = public_id("content", output.id)
        session.get(ApiKey, identity.key_id).deleted_at = utcnow()
    project = client.post(PATH, json=request(catalog, mode="resolve", ids=[project_id]).model_dump())
    creator = client.post(PATH, json=request(catalog, mode="resolve", kind="creator_key", project_ids=[project_id], ids=[creator_id]).model_dump())
    assert project.status_code == creator.status_code == 200
    assert creator.json()["data"][0]["lifecycle"] == "archived"
    body = key_body(catalog)
    created = client.post("/admin/api/v1/api-keys", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert created.status_code == 201, created.text
    consumer = created.json()
    authorization = {"Authorization": "Bearer " + consumer["secret"]}
    path = "/v1/content/" + content_id
    assert client.get(path, headers=authorization).status_code == 404
    assert client.get("/v1/content", headers=authorization).json()["data"] == []
    policy = deepcopy(body["policy"])
    policy["content"] = {"mode": "shared_creator_keys", "project_ids": [item["id"] for item in project.json()["data"]], "creator_key_ids": [item["id"] for item in creator.json()["data"]]}
    key_path = "/admin/api/v1/api-keys/" + consumer["key"]["id"]
    shared = client.patch(key_path, json={"expected_revision": 1, "policy": policy}, headers={"If-Match": '"1"', "Idempotency-Key": str(uuid4())})
    assert shared.status_code == 200, shared.text
    assert client.get(path, headers=authorization).status_code == 200
    assert [row["id"] for row in client.get("/v1/content", headers=authorization).json()["data"]] == [content_id]
    image = client.get(path + "/download", headers=authorization)
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG\r\n\x1a\n")
    revoked = client.patch(key_path, json={"expected_revision": 2, "policy": body["policy"]}, headers={"If-Match": '"2"', "Idempotency-Key": str(uuid4())})
    assert revoked.status_code == 200, revoked.text
    assert client.get(path, headers=authorization).status_code == 404
    assert client.get(path + "/download", headers=authorization).status_code == 404
    assert client.get("/v1/content", headers=authorization).json()["data"] == []
