from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.defaults import default_key_policy
from generation_domain.values import fingerprint, parse_id, public_id
from generation_infrastructure.models import (
    AdminUser,
    ApiCredential,
    ApiKey,
    Job,
    PublicModel,
    WorkflowRelease,
)
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-password-for-tests-only"


@pytest.fixture
def client(pg: sessionmaker[Session], test_settings: Settings, catalog: dict[str, Any]) -> Iterator[TestClient]:
    with pg.begin() as session:
        session.add(AdminUser(username="test-admin", password_hash=password_hash(PASSWORD), role="administrator"))
        session.add(AdminUser(username="test-observer", password_hash=password_hash(PASSWORD), role="observer"))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50000)) as client:
        client.headers["Origin"] = test_settings.public_origin
        yield client


def login(client: TestClient, username: str = "test-admin") -> dict[str, Any]:
    response = client.post("/admin/api/v1/session", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    data = response.json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    return data


def key_body(catalog: dict[str, Any]) -> dict[str, Any]:
    policy = default_key_policy()
    for name in ("models", "workflow_releases", "channels"):
        policy[name] = {"mode": "all_authorized", "selected_ids": []}
    return {"name": "Synthetic HTTP key", "tenant_id": public_id("ten", catalog["identity"].tenant_id), "project_id": public_id("prj", catalog["identity"].project_id), "policy": policy}


def test_studio_release_header_validation_poll_hint_and_cors(client: TestClient, catalog: dict[str, Any], test_settings: Settings) -> None:
    login(client)
    body = key_body(catalog)
    body["policy"]["limits"]["burst"]["value"] = 100
    issued = client.post("/admin/api/v1/api-keys", json=body, headers={"Idempotency-Key": str(uuid4())}).json()
    headers = {"Authorization": "Bearer " + issued["secret"], "Idempotency-Key": "studio-http-guard-0001", "X-Expected-Release-Digest": "0" * 64}
    request = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "synthetic studio http"}}
    response = client.post("/v1/jobs", headers=headers, json=request)
    assert response.status_code == 409 and response.json()["error"]["code"] == "model_release_changed"
    headers["X-Expected-Release-Digest"] = "invalid"
    assert client.post("/v1/jobs", headers=headers, json=request).status_code == 422
    headers["X-Expected-Release-Digest"] = catalog["release_digest"]
    created = client.post("/v1/jobs", headers=headers, json=request)
    assert created.status_code == 202 and created.headers["Retry-After"] == "2"
    queried = client.get("/v1/jobs/" + created.json()["id"], headers={"Authorization": headers["Authorization"]})
    assert queried.status_code == 200 and queried.headers["Retry-After"] == "2"
    with TestClient(create_app(test_settings.model_copy(update={"allow_any_origin": True})), base_url=test_settings.public_origin, client=("127.0.0.1", 50001)) as external:
        policy = external.get("/v1/access-policy", headers={"Authorization": headers["Authorization"], "Origin": "http://external-canvas.invalid:8111"})
        assert policy.status_code == 200 and policy.headers["X-Studio-Contract"] == "1"
        assert policy.headers["X-Studio-Submission-Lookup"] == "1"
        assert policy.headers["Access-Control-Allow-Origin"] == "http://external-canvas.invalid:8111"
        assert "X-Studio-Contract" in policy.headers["Access-Control-Expose-Headers"]
        assert "X-Studio-Submission-Lookup" in policy.headers["Access-Control-Expose-Headers"]


def test_public_job_cursor_http_rotation_revocation_and_query_validation(client: TestClient, catalog: dict[str, Any], pg: sessionmaker[Session]) -> None:
    login(client)
    body = key_body(catalog)
    body["policy"]["limits"]["burst"]["value"] = 100
    created = client.post("/admin/api/v1/api-keys", json=body, headers={"Idempotency-Key": str(uuid4())}).json()
    headers = {"Authorization": "Bearer " + created["secret"]}
    for _ in range(3):
        accepted = client.post("/v1/jobs", headers=headers, json={"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic private list prompt"}})
        assert accepted.status_code == 202, accepted.text
    first = client.get("/v1/jobs", headers=headers, params={"limit": 1, "status": "queued"})
    assert first.status_code == 200 and first.json()["has_more"] and len(first.json()["data"]) == 1
    assert "Synthetic private list prompt" not in first.text
    identifier = created["key"]["id"]
    rotated = client.post("/admin/api/v1/api-keys/" + identifier + "/actions", json={"action": "rotate", "expected_revision": 1}, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}).json()
    current = {"Authorization": "Bearer " + rotated["secret"]}
    params = {"limit": 1, "status": "queued", "cursor": first.json()["next_cursor"]}
    assert client.get("/v1/jobs", headers=headers, params=params).status_code == 401
    second = client.get("/v1/jobs", headers=current, params=params)
    assert second.status_code == 200 and second.json()["data"][0]["id"] != first.json()["data"][0]["id"]
    for query in ({"page": 1}, {"ignored": "unsafe"}, [("limit", "1"), ("limit", "2")]):
        assert client.get("/v1/jobs", headers=current, params=query).status_code == 400
    assert client.get("/v1/content", headers=current, params={"status": "queued"}).status_code == 400
    assert client.get("/v1/content", headers=current).json() == {"object": "list", "data": [], "has_more": False, "next_cursor": None}
    assert client.get("/v1/jobs").status_code == 401
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", identifier))
        key.policy = {**key.policy, "scopes": [scope for scope in key.policy["scopes"] if scope != "jobs:read"]}
    assert client.get("/v1/jobs", headers=current, params=params).status_code == 403


def test_model_discovery_http_single_scope_and_current_publication(client: TestClient, catalog: dict[str, Any], pg: sessionmaker[Session]) -> None:
    login(client)
    created = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())}).json()
    headers = {"Authorization": "Bearer " + created["secret"]}
    with pg.begin() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        manifest = {**release.manifest, "supported_parameters": ["seed"], "presets": [{"quality": "standard", "size": "1024x1024", "values": {"private.node": "never-forward"}}], "output": {"mime_types": ["image/png"], "expected_count": 1, "max_bytes": 33554432}}
        frozen = WorkflowRelease(tenant_id=release.tenant_id, project_id=release.project_id, workflow_id=release.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"synthetic": True})
        session.add(frozen)
        session.flush()
        session.scalar(select(PublicModel)).release_id = frozen.id
    models = client.get("/v1/models", headers=headers)
    single = client.get("/v1/models/synthetic-image", headers=headers)
    capabilities = client.get("/v1/capabilities", headers=headers)
    assert models.status_code == single.status_code == capabilities.status_code == 200
    assert models.json()["data"] == [single.json()]
    assert capabilities.json()["data"][0]["release"]["digest"] == fingerprint(manifest)
    assert "never-forward" not in capabilities.text
    assert client.get("/v1/models/missing", headers=headers).status_code == 404
    assert client.get("/v1/models/synthetic-image").status_code == 401
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", created["key"]["id"]))
        key.policy = {**key.policy, "scopes": ["capabilities:read"]}
    assert client.get("/v1/models/synthetic-image", headers=headers).status_code == 403
    assert client.get("/v1/capabilities", headers=headers).status_code == 200
    with pg.begin() as session:
        session.scalar(select(PublicModel)).published = False
    assert client.get("/v1/capabilities", headers=headers).json()["data"] == []


def test_images_async_and_native_jobs_share_idempotency(client: TestClient, catalog: dict[str, Any]) -> None:
    login(client)
    created = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())}).json()
    headers = {"Authorization": "Bearer " + created["secret"], "Idempotency-Key": str(uuid4())}
    image = {"model": "synthetic-image", "prompt": "protocol compatibility"}
    response = client.post("/v1/images/generations/async", json=image, headers=headers)
    assert response.status_code == 202, response.text
    native = client.post("/v1/jobs", json={"model": image["model"], "task_type": "text_to_image", "input": {"prompt": image["prompt"]}}, headers=headers)
    assert native.status_code == 202 and native.json()["id"] == response.json()["id"]
    assert client.get("/v1/images/tasks/" + response.json()["id"], headers=headers).json()["status"] == "processing"
    conflict = client.post("/v1/images/generations/async", json={**image, "prompt": "different"}, headers=headers)
    assert conflict.status_code == 409
    for extra in ({"stream": True}, {"size": "invalid"}, {"n": 2}):
        invalid = client.post("/v1/images/generations/async", json={**image, **extra}, headers={**headers, "Idempotency-Key": str(uuid4())})
        assert invalid.status_code == 422, invalid.text


@pytest.mark.parametrize("response_format", [None, "url", "b64_json"])
@pytest.mark.parametrize("missing", ["content:read", "content:download"])
def test_sync_image_modes_require_delivery_scopes_before_create(client: TestClient, catalog: dict[str, Any], pg: sessionmaker[Session], response_format: str | None, missing: str) -> None:
    login(client)
    body = key_body(catalog)
    body["policy"]["scopes"].remove(missing)
    created = client.post("/admin/api/v1/api-keys", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert created.status_code == 201, created.text
    image = {"model": "synthetic-image", "prompt": "synthetic delivery authorization"}
    if response_format is not None:
        image["response_format"] = response_format
    response = client.post("/v1/images/generations", json=image, headers={"Authorization": "Bearer " + created.json()["secret"], "Idempotency-Key": str(uuid4())})
    assert response.status_code == 403, response.text
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_cookie_csrf_and_identity_realms(client: TestClient, catalog: dict[str, Any]) -> None:
    assert client.get("/admin/api/v1/api-keys").status_code == 401
    login_response = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD})
    cookie = login_response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    response = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 403
    session = client.get("/admin/api/v1/session").json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    response = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 201, response.text
    secret = response.json()["secret"]
    assert client.get("/v1/access-policy", headers={"Authorization": "Bearer " + secret}).status_code == 200
    assert client.post("/internal/v1/claims", json={"protocol_version": "0.4.0", "boot_id": str(uuid4()), "request_id": str(uuid4())}, headers={"Authorization": "Bearer " + secret}).status_code == 401
    client.cookies.clear()
    assert client.get("/admin/api/v1/api-keys", headers={"Authorization": "Bearer " + secret}).status_code == 401


def test_one_time_secret_replay_rotation_and_revocation(client: TestClient, catalog: dict[str, Any], pg: sessionmaker[Session]) -> None:
    login(client)
    token = str(uuid4())
    first = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": token})
    assert first.status_code == 201, first.text
    result = first.json()
    identifier = result["key"]["id"]
    repeated = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": token}).json()
    assert repeated["key"]["id"] == identifier and repeated.get("secret") is None
    listing = client.get("/admin/api/v1/api-keys").text
    assert result["secret"] not in listing and "verifier" not in listing
    headers = {"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}
    rotated = client.post("/admin/api/v1/api-keys/" + identifier + "/actions", json={"action": "rotate", "expected_revision": 1}, headers=headers)
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["key"]["id"] == identifier
    assert client.get("/v1/access-policy", headers={"Authorization": "Bearer " + result["secret"]}).status_code == 401
    assert client.get("/v1/access-policy", headers={"Authorization": "Bearer " + rotated.json()["secret"]}).status_code == 200
    with pg() as session:
        assert len(list(session.scalars(select(ApiCredential).where(ApiCredential.identifier.is_not(None))))) == 2
        assert result["secret"] not in str(session.execute(select(ApiKey.policy)).all())


def test_observer_and_stale_configuration_are_rejected(client: TestClient, catalog: dict[str, Any]) -> None:
    login(client, "test-observer")
    assert client.get("/admin/api/v1/queues/snapshot").status_code == 200
    assert client.get("/admin/api/v1/api-keys").status_code == 403
    login(client)
    identifier = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())}).json()["key"]["id"]
    patch = {"name": "changed", "expected_revision": 1}
    endpoint = "/admin/api/v1/api-keys/" + identifier
    assert client.patch(endpoint, json=patch, headers={"Idempotency-Key": str(uuid4())}).status_code == 428
    assert client.patch(endpoint, json=patch, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}).status_code == 200
    assert client.patch(endpoint, json=patch, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}).status_code == 409


def test_request_limits_and_validation_never_echo_secrets(client: TestClient) -> None:
    secret = "Synthetic-private-password"
    response = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": secret, "unexpected": secret})
    assert response.status_code == 422 and secret not in response.text


def test_admin_content_metadata_range_delete_and_observer_access(client: TestClient, catalog: dict[str, Any]) -> None:
    from io import BytesIO

    from PIL import Image

    login(client)
    body = key_body(catalog)
    body["policy"]["scopes"].append("content:delete")
    created = client.post("/admin/api/v1/api-keys", json=body, headers={"Idempotency-Key": str(uuid4())}).json()
    source = BytesIO()
    Image.new("RGB", (32, 32)).save(source, "PNG")
    headers = {"Authorization": "Bearer " + created["secret"]}
    uploaded = client.post("/v1/assets", files={"file": ("synthetic.png", source.getvalue(), "image/png")}, headers=headers)
    assert uploaded.status_code == 201, uploaded.text
    identifier = uploaded.json()["id"]
    listing = client.get("/admin/api/v1/content", params={"kind": "input", "key_id": created["key"]["id"]})
    assert listing.status_code == 200 and listing.json()["data"][0]["id"] == identifier
    assert "object_key" not in listing.text and "version_id" not in listing.text
    path = "/admin/api/v1/content/" + identifier
    head = client.head(path + "/download", headers={"Range": "bytes=0-7"})
    assert head.status_code == 206 and head.content == b"" and head.headers["content-length"] == "8"
    assert client.get(path + "/download", headers={"Range": "bytes=0-7"}).content == b"\x89PNG\r\n\x1a\n"
    idem = {"Idempotency-Key": str(uuid4())}
    assert client.delete(path, headers={**idem, "X-CSRF-Token": "wrong"}).status_code == 403
    assert client.delete(path, headers=idem).status_code == 202
    assert client.delete(path, headers=idem).status_code == 202
    assert client.get("/v1/assets/" + identifier, headers=headers).status_code == 404
    assert client.get(path).json()["deleted_at"]
    login(client, "test-observer")
    assert client.get("/admin/api/v1/content").status_code == 403
    assert client.get(path + "/download").status_code == 403
    chunks = iter([b'{"password":"', b"x" * (1024 * 1024 + 1), b'"}'])
    response = client.post("/admin/api/v1/session", content=chunks, headers={"Content-Type": "application/json"})
    assert response.status_code == 413, response.text
