from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity, BusinessIdentity
from generation_contracts.admin import ROLE_PERMISSIONS
from generation_contracts.content_management import ContentOptionQuery, ContentQuery, ContentTags
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.models import (
    AdminMutation,
    AdminUser,
    ApiCredential,
    ApiKey,
    Artifact,
    Asset,
    AuditEvent,
    GatewayService,
    Job,
    JobAssetReference,
    Node,
    ObjectRecord,
    PublicModel,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret, password_hash, verifier
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-content-management-test-password"
EPOCH = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)


def picture() -> BytesIO:
    stream = BytesIO()
    Image.new("RGB", (16, 16), "teal").save(stream, format="PNG")
    stream.seek(0)
    return stream


def seed_job(session: Session, catalog: dict[str, Any], **values: Any) -> Job:
    owner = catalog["identity"]
    data = dict(tenant_id=owner.tenant_id, project_id=owner.project_id, key_id=owner.key_id, release_id=catalog["release_id"], model_alias="historical-content-model", task_type="text_to_image", state="succeeded", request={"input": {"prompt": "PRIVATE CONTENT TEST PROMPT"}}, execution={}, request_fingerprint="a" * 64, execution_fingerprint="b" * 64, eligible_channels=[], accepted_at=EPOCH, created_at=EPOCH, deadline=EPOCH + timedelta(days=3), accepted_period="2026-10-23")
    session.add(row := Job(**{**data, **values}))
    session.flush()
    return row


def seed_output(session: Session, catalog: dict[str, Any], store: LocalDevelopmentStore, *, media_type: str = "image/png", **values: Any) -> Artifact:
    job = seed_job(session, catalog)
    stored = store.put("synthetic-content/" + uuid4().hex, picture(), media_type, 1024 * 1024)
    obj = ObjectRecord(tenant_id=job.tenant_id, project_id=job.project_id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=media_type, verified_at=utcnow(), expires_at=utcnow() + timedelta(days=7))
    session.add(obj)
    session.flush()
    row = Artifact(tenant_id=job.tenant_id, project_id=job.project_id, key_id=job.key_id, job_id=job.id, object_id=obj.id, position=0, created_at=EPOCH, **values)
    session.add(row)
    session.flush()
    return row


def actor(session: Session) -> AdminIdentity:
    session.add(user := AdminUser(username="content-admin", password_hash=password_hash(PASSWORD), role="administrator"))
    session.flush()
    return AdminIdentity(user.id, user.username, user.role, uuid4())


def test_content_query_intersects_frozen_job_history_owner_tags_and_half_open_time(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    with pg.begin() as session:
        row = seed_output(session, catalog, store, tags=["春季", "Case"])
        identifier, job_id = public_id("content", row.id), public_id("job", row.job_id)
        workflow_id = public_id("wf", session.get(WorkflowRelease, catalog["release_id"]).workflow_id)
        session.scalar(select(PublicModel)).alias = "renamed-current-model"
        seed_output(session, catalog, store, tags=["case"])
    owner = catalog["identity"]
    epoch = int(EPOCH.timestamp())
    filters = dict(tenant_id=public_id("ten", owner.tenant_id), project_id=public_id("prj", owner.project_id), key_id=public_id("key", owner.key_id), job_id=job_id, model="historical-content-model", workflow_id=workflow_id, release_id=public_id("wfr", catalog["release_id"]), tag="Case", media_kind="image", state="available", created_from=epoch, created_before=epoch + 1)
    repository = ContentRepository(pg, store)
    result = repository.admin_list(ContentQuery(**filters))
    assert [item["id"] for item in result["data"]] == [identifier]
    assert result["filters"] == {"kind": "output", **filters} and not result["has_more"]
    assert result["data"][0]["tags"] == ["春季", "Case"] and result["data"][0]["metadata_revision"] == 1
    for field, value in (("tenant_id", public_id("ten", uuid4())), ("project_id", public_id("prj", uuid4())), ("key_id", public_id("key", uuid4())), ("job_id", public_id("job", uuid4())), ("workflow_id", public_id("wf", uuid4())), ("release_id", public_id("wfr", uuid4())), ("model", "renamed-current-model"), ("tag", "CASE"), ("media_kind", "video"), ("state", "expired"), ("created_before", epoch)):
        changed = {**filters, field: value}
        if field == "created_before":
            changed.pop("created_from")
        assert repository.admin_list(ContentQuery(**changed))["data"] == [], field
    assert "PRIVATE CONTENT TEST PROMPT" not in str(result) and "object_key" not in str(result)


def test_input_matching_uses_exists_and_never_duplicates_shared_references(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    repository = ContentRepository(pg, LocalDevelopmentStore(test_settings.local_storage_root))
    uploaded = repository.upload_asset(catalog["identity"], picture(), "synthetic-input")
    asset_id = parse_id("asset", uploaded["id"])
    owner = catalog["identity"]
    with pg.begin() as session:
        for _ in range(3):
            job = seed_job(session, catalog)
            session.add(JobAssetReference(tenant_id=owner.tenant_id, project_id=owner.project_id, key_id=owner.key_id, job_id=job.id, asset_id=asset_id))
        workflow_id = public_id("wf", session.get(WorkflowRelease, catalog["release_id"]).workflow_id)
    result = repository.admin_list(ContentQuery(kind="input", model="historical-content-model", workflow_id=workflow_id))
    assert len(result["data"]) == 1 and result["data"][0]["id"] == uploaded["id"]
    assert repository.admin_list(ContentQuery(kind="input", model="absent-model"))["data"] == []


def test_admin_content_pages_are_bounded_and_stable(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    with pg.begin() as session:
        for _ in range(53):
            seed_output(session, catalog, store)
    repository = ContentRepository(pg, store)
    first = repository.admin_list(ContentQuery())
    second = repository.admin_list(ContentQuery(page=2))
    assert len(first["data"]) == 50 and first["has_more"] and len(second["data"]) == 3 and not second["has_more"]
    assert not {row["id"] for row in first["data"]} & {row["id"] for row in second["data"]}
    assert repository.admin_list(ContentQuery())["data"] == first["data"]


def test_lifecycle_query_and_projection_agree_and_preview_refuses_unavailable_objects(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    expected: dict[str, UUID] = {}
    with pg.begin() as session:
        for state in ("available", "unverified", "expired", "pending_reclaim", "reclaimed"):
            row = seed_output(session, catalog, store)
            obj = session.get(ObjectRecord, row.object_id)
            if state != "available":
                obj.verified_at = None
            if state in {"expired", "pending_reclaim", "reclaimed"}:
                obj.expires_at = utcnow() - timedelta(seconds=1)
            if state in {"pending_reclaim", "reclaimed"}:
                row.deleted_at = obj.delete_requested_at = utcnow()
            if state == "reclaimed":
                obj.deleted_at = utcnow()
            expected[state] = row.id
    repository = ContentRepository(pg, store)
    for state, identifier in expected.items():
        result = repository.admin_list(ContentQuery(state=state))
        assert len(result["data"]) == 1 and result["data"][0]["id"] == public_id("content", identifier)
        assert result["data"][0]["status"] == state
        assert result["data"][0]["preview_supported"] == (state == "available")
        if state != "available":
            with pytest.raises(DomainError) as error:
                repository.admin_download(identifier, None, preview=True)
            assert error.value.status == 404


def test_tag_mutations_are_revisioned_atomic_idempotent_audited_and_do_not_expand_sharing(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    with pg.begin() as session:
        admin = actor(session)
        row = seed_output(session, catalog, store)
        identifier, object_id, original_owner = row.id, row.object_id, row.key_id
        other = ApiKey(tenant_id=row.tenant_id, project_id=row.project_id, name="Other key", policy=catalog["identity"].policy)
        session.add(other)
        session.flush()
        other_id = other.id
        original_expiry = session.get(ObjectRecord, object_id).expires_at
    repository = ContentRepository(pg, store)
    body = ContentTags(expected_revision=1, tags=["春季", "Restricted"])
    first = repository.admin_tags(admin, identifier, body, "same-content-tag-token", "synthetic")
    replay = repository.admin_tags(admin, identifier, body, "same-content-tag-token", "synthetic-replay")
    assert replay == first and first["metadata_revision"] == 2
    for changed, token in ((ContentTags(expected_revision=1, tags=["other"]), "same-content-tag-token"), (body, "stale-content-tag-token")):
        with pytest.raises(DomainError) as error:
            repository.admin_tags(admin, identifier, changed, token, "synthetic")
        assert error.value.status == 409
    owner = catalog["identity"]
    with pytest.raises(DomainError) as denied:
        repository.download(BusinessIdentity(owner.tenant_id, owner.project_id, other_id, owner.policy), identifier, None)
    assert denied.value.status == 404
    assert "tags" not in repository.metadata(owner, identifier)
    with pg() as session:
        audit = session.scalars(select(AuditEvent).where(AuditEvent.action == "content.tags_changed")).all()
        assert len(audit) == 1 and audit[0].detail == {"revision": 2, "tag_count": 2, "revision_before": 1, "revision_after": 2}
        assert "Restricted" not in str(audit[0].detail)
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 1
        current, obj = session.get(Artifact, identifier), session.get(ObjectRecord, object_id)
        assert current.key_id == original_owner and obj.expires_at == original_expiry and obj.delete_requested_at is None
    assert repository.admin_tags(admin, identifier, ContentTags(expected_revision=2, tags=[]), "clear-content-tag-token", "synthetic")["tags"] == []


@pytest.mark.parametrize("same_token", [False, True])
def test_concurrent_metadata_writes_never_overwrite_another_revision(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, same_token: bool) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    with pg.begin() as session:
        admin = actor(session)
        identifier = seed_output(session, catalog, store).id
    repository, barrier = ContentRepository(pg, store), Barrier(2)
    def write(index: int) -> tuple[int, dict[str, Any] | None]:
        barrier.wait(timeout=10)
        try:
            result = repository.admin_tags(admin, identifier, ContentTags(expected_revision=1, tags=["shared"] if same_token else [str(index)]), "same-concurrent-content-token" if same_token else f"concurrent-content-token-{index}", "synthetic")
            return 200, result
        except DomainError as error:
            return error.status, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert sorted(status for status, _ in results) == ([200, 200] if same_token else [200, 409])
    if same_token:
        assert results[0][1] == results[1][1]
    assert repository.admin_metadata(identifier)["metadata_revision"] == 2
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "content.tags_changed")) == 1


def test_tags_survive_delete_and_database_rejects_invalid_metadata(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    with pg.begin() as session:
        admin = actor(session)
        identifier = seed_output(session, catalog, store).id
    repository = ContentRepository(pg, store)
    repository.admin_delete(admin, identifier, "delete-before-tags-token", "synthetic")
    result = repository.admin_tags(admin, identifier, ContentTags(expected_revision=1, tags=["Archived"]), "tags-after-delete-token", "synthetic")
    assert result["status"] == "pending_reclaim" and result["tags"] == ["Archived"] and not result["preview_supported"]
    for column, value in (("tags", "'{}'::jsonb"), ("tags", "'" + json.dumps(["x"] * 17) + "'::jsonb"), ("metadata_revision", "0")):
        with pytest.raises(IntegrityError), pg.begin() as session:
            session.execute(text(f"UPDATE artifacts SET {column} = {value} WHERE id = :id"), {"id": identifier})
    with pg() as session:
        indexes = session.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN ('ix_assets_tags', 'ix_artifacts_tags', 'ix_assets_owner_created')")).all()
        assert len(indexes) == 3 and all("jsonb_path_ops" in definition for name, definition in indexes if name.endswith("_tags"))


def test_content_key_options_scope_search_and_safe_projection(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    owner = catalog["identity"]
    with pg.begin() as session:
        for index in range(52):
            session.add(ApiKey(tenant_id=owner.tenant_id, project_id=owner.project_id, name=f"Named key {index:02}", policy=owner.policy))
        session.add(ApiKey(tenant_id=owner.tenant_id, project_id=owner.project_id, name="Literal % key", policy=owner.policy))
    repository = ContentRepository(pg, LocalDevelopmentStore(test_settings.local_storage_root))
    query = ContentOptionQuery(kind="key", tenant_id=public_id("ten", owner.tenant_id), project_id=public_id("prj", owner.project_id), q="Named")
    first = repository.admin_filter_options(query)
    assert len(first["data"]) == 50 and first["has_more"] and all(set(row) == {"value", "label"} for row in first["data"])
    assert len(repository.admin_filter_options(query.model_copy(update={"page": 2}))["data"]) == 2
    assert len(repository.admin_filter_options(ContentOptionQuery(kind="key", q="%"))["data"]) == 1
    assert repository.admin_filter_options(query.model_copy(update={"project_id": public_id("prj", uuid4())}))["data"] == []


def test_tag_delete_race_preserves_tombstone_and_only_metadata_changes(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    with pg.begin() as session:
        admin = actor(session)
        identifier = seed_output(session, catalog, store).id
    repository, barrier = ContentRepository(pg, store), Barrier(2)
    def write(index: int) -> None:
        barrier.wait(timeout=10)
        if index:
            repository.admin_delete(admin, identifier, "concurrent-delete-token", "synthetic")
        else:
            repository.admin_tags(admin, identifier, ContentTags(expected_revision=1, tags=["Archived"]), "concurrent-tags-token", "synthetic")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, range(2)))
    result = repository.admin_metadata(identifier)
    assert result["status"] == "pending_reclaim" and result["tags"] == ["Archived"] and result["metadata_revision"] == 2
    with pytest.raises(DomainError) as denied:
        repository.admin_download(identifier, None, preview=True)
    assert denied.value.status == 404


def test_input_tag_edit_and_empty_immutable_version_preview_guard(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    repository = ContentRepository(pg, LocalDevelopmentStore(test_settings.local_storage_root))
    uploaded = repository.upload_asset(catalog["identity"], picture(), "synthetic-input-tags")
    identifier = parse_id("asset", uploaded["id"])
    with pg.begin() as session:
        admin = actor(session)
    result = repository.admin_tags(admin, identifier, ContentTags(expected_revision=1, tags=["Input"]), "input-content-tags-token", "synthetic", asset=True)
    assert result["id"] == uploaded["id"] and result["tags"] == ["Input"] and result["metadata_revision"] == 2
    for version in (None, ""):
        with pg.begin() as session:
            row = session.get(Asset, identifier)
            session.get(ObjectRecord, row.object_id).version_id = version
        assert repository.admin_list(ContentQuery(kind="input", state="unverified"))["data"][0]["id"] == uploaded["id"]
        with pytest.raises(DomainError) as denied:
            repository.admin_download(identifier, None, asset=True, preview=True)
        assert denied.value.status == 404


def test_valid_non_admin_identity_realms_cannot_list_edit_or_preview_content(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    owner = catalog["identity"]
    tokens = {realm: issue_secret(realm) for realm in ("sk-gen", "worker", "gateway")}
    pepper = test_settings.credential_pepper.get_secret_value()
    with pg.begin() as session:
        business_id, business_token = tokens["sk-gen"]
        session.add(ApiCredential(key_id=owner.key_id, identifier=business_id, verifier=verifier(business_token, pepper)))
        node = session.get(Node, catalog["node_id"])
        node.credential_identifier, worker_token = tokens["worker"]
        node.credential_verifier = verifier(worker_token, pepper)
        gateway_id, gateway_token = tokens["gateway"]
        session.add(GatewayService(name="Content test gateway", issuer="content-tests", allowed_cidrs=["127.0.0.1/32"], credential_identifier=gateway_id, credential_verifier=verifier(gateway_token, pepper)))
        row = seed_output(session, catalog, LocalDevelopmentStore(test_settings.local_storage_root))
        identifier = public_id("content", row.id)
    auth = AuthRepository(pg, test_settings)
    assert auth.business(business_token, "127.0.0.1").key_id == owner.key_id
    assert auth.worker(worker_token) == catalog["node_id"]
    assert auth.gateway(gateway_token, "127.0.0.1").issuer == "content-tests"
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50000)) as client:
        for _, token in tokens.values():
            headers = {"Authorization": "Bearer " + token, "Origin": test_settings.public_origin, "If-Match": '"1"', "Idempotency-Key": "realm-content-test-token"}
            for path in ("/admin/api/v1/content", "/admin/api/v1/content/filter-options?kind=key", "/admin/api/v1/content/" + identifier + "/preview"):
                assert client.get(path, headers=headers).status_code == 401
            assert client.patch("/admin/api/v1/content/" + identifier, headers=headers, json={"expected_revision": 1, "tags": ["Denied"]}).status_code == 401


def test_content_http_auth_csrf_ifmatch_ranges_preview_lifecycle_and_sensitive_input(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalDevelopmentStore(test_settings.local_storage_root)
    repository = ContentRepository(pg, store)
    uploaded = repository.upload_asset(catalog["identity"], picture(), "synthetic-http-input")
    with pg.begin() as session:
        actor(session)
        session.add(AdminUser(username="content-observer", password_hash=password_hash(PASSWORD), role="observer"))
        row = seed_output(session, catalog, store)
        identifier, object_id = public_id("content", row.id), row.object_id
        unsupported = public_id("content", seed_output(session, catalog, store, media_type="image/svg+xml").id)
    base = "/admin/api/v1/content/" + identifier
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50000)) as client:
        client.headers["Origin"] = test_settings.public_origin
        for bearer in ("sk-business-synthetic", "wrk-synthetic", "gateway-synthetic"):
            assert client.get(base + "/preview", headers={"Authorization": "Bearer " + bearer}).status_code == 401
        login = client.post("/admin/api/v1/session", json={"username": "content-admin", "password": PASSWORD})
        assert login.status_code == 200, login.text
        csrf = login.json()["csrf_token"]
        assert client.get("/admin/api/v1/content/filter-options", params={"kind": "key"}).status_code == 200
        assert client.get("/admin/api/v1/content", params={"kind": "output", "tag": "missing"}).json()["data"] == []
        for invalid in ({"unexpected": "x"}, {"page": 0}, {"created_from": 2, "created_before": 1}):
            assert client.get("/admin/api/v1/content", params=invalid).status_code == 422
        body = {"expected_revision": 1, "tags": ["HTTP tag"]}
        assert client.patch(base, json=body, headers={"If-Match": '"1"', "Idempotency-Key": "http-content-tag-token"}).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        assert client.patch(base, json=body, headers={"Idempotency-Key": "http-content-tag-token"}).status_code == 428
        headers = {"If-Match": '"1"', "Idempotency-Key": "http-content-tag-token"}
        saved = client.patch(base, json=body, headers=headers)
        assert saved.status_code == 200 and saved.headers["ETag"] == '"2"', saved.text
        assert client.patch(base, json=body, headers=headers).json() == saved.json()
        assert client.get(base).headers["ETag"] == '"2"'
        assert client.patch(base, json={**body, "tags": ["different"]}, headers=headers).status_code == 409
        preview = client.get(base + "/preview", headers={"Range": "bytes=0-7"})
        assert preview.status_code == 206 and preview.content == b"\x89PNG\r\n\x1a\n"
        assert preview.headers["Content-Disposition"].startswith("inline;") and preview.headers["Cache-Control"] == "private, no-store"
        assert preview.headers["X-Content-Type-Options"] == "nosniff" and "sandbox" in preview.headers["Content-Security-Policy"]
        assert client.head(base + "/preview").content == b""
        assert client.head(base + "/preview", headers={"Range": "bytes=0-7"}).status_code == 206
        assert client.get(base + "/preview", headers={"Range": "bytes=999999-"}).status_code == 416
        assert client.get("/admin/api/v1/content/" + unsupported + "/preview").status_code == 415
        assert client.get(base + "/download").headers["Content-Disposition"].startswith("attachment;")
        assert client.get("/admin/api/v1/content/" + uploaded["id"] + "/preview").status_code == 200
        # Exercise the real permission guard with a reduced preset, rather than bypassing dependencies.
        monkeypatch.setitem(ROLE_PERMISSIONS, "administrator", [p for p in ROLE_PERMISSIONS["administrator"] if p != "content:sensitive"])
        assert client.get("/admin/api/v1/content/" + uploaded["id"] + "/preview").status_code == 403
        assert client.head("/admin/api/v1/content/" + uploaded["id"] + "/preview").status_code == 403
        assert client.get(base + "/preview").status_code == 200
        with pg.begin() as session:
            session.get(ObjectRecord, object_id).expires_at = utcnow() - timedelta(seconds=1)
        assert client.get(base + "/preview").status_code == 404
        assert client.delete("/admin/api/v1/session").status_code == 204
        observer = client.post("/admin/api/v1/session", json={"username": "content-observer", "password": PASSWORD})
        assert observer.status_code == 200
        client.headers["X-CSRF-Token"] = observer.json()["csrf_token"]
        assert client.get(base + "/preview").status_code == 403
        assert client.patch(base, json=body, headers=headers).status_code == 403
