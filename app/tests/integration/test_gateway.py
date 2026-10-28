from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from threading import Barrier
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.defaults import default_key_policy
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.models import (
    AdminUser,
    ApiKey,
    Artifact,
    ExternalIdentityBinding,
    GatewayAdmission,
    GatewayRequestFence,
    GatewayService,
    Job,
    ObjectRecord,
    OutboxEvent,
    QuotaBucket,
    RateState,
    UsageLedger,
)
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from PIL import Image
from sqlalchemy import func, literal, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-gateway-admin-password"


@pytest.fixture
def gateway_client(pg: sessionmaker[Session], test_settings: Settings, catalog: dict[str, Any]) -> Iterator[tuple[TestClient, dict[str, str], dict[str, Any]]]:
    with pg.begin() as session:
        session.add(AdminUser(username="gateway-admin", password_hash=password_hash(PASSWORD), role="administrator"))
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50100)) as client:
        client.headers["Origin"] = test_settings.public_origin
        response = client.post("/admin/api/v1/session", json={"username": "gateway-admin", "password": PASSWORD})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        create_token = str(uuid4())
        service_body = {"name": "Synthetic Sub2API", "issuer": "sub2api", "allowed_cidrs": ["127.0.0.1/32"]}
        response = client.post("/admin/api/v1/gateways", json=service_body, headers={"Idempotency-Key": create_token})
        assert response.status_code == 201, response.text
        service = response.json()
        replay = client.post("/admin/api/v1/gateways", json=service_body, headers={"Idempotency-Key": create_token}).json()
        assert not replay.get("secret") and not replay["secret_available"]
        assert service["secret"] not in client.get("/admin/api/v1/gateways").text
        policy = default_key_policy()
        for name in ("models", "workflow_releases", "channels"):
            policy[name] = {"mode": "all_authorized", "selected_ids": []}
        policy["limits"]["burst"]["value"] = 100
        binding_body = {"gateway_id": service["gateway"]["id"], "tenant_id": public_id("ten", catalog["identity"].tenant_id), "project_id": public_id("prj", catalog["identity"].project_id), "external_tenant_ref": "synthetic-tenant", "external_key_ref": "synthetic-key", "name": "External synthetic key", "policy": policy}
        response = client.post("/admin/api/v1/gateway-bindings", json=binding_body, headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 201, response.text
        binding = response.json()
        headers = {"Authorization": "Bearer " + service["secret"], "X-External-Tenant-Ref": "synthetic-tenant", "X-External-Key-Ref": "synthetic-key", "X-Client-IP": "192.0.2.10"}
        yield client, headers, {"service": service, "binding": binding}


def admission_body() -> dict[str, Any]:
    return {"provider_request_id": str(uuid4()), "external_task_ref": "synthetic-public-task-" + uuid4().hex, "price_version": "synthetic-price-v1", "authorization_ref": "synthetic-reservation-" + uuid4().hex, "request": {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic gateway protocol request"}}}


def test_gateway_rejects_model3d_without_creating_image_billing(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    body = admission_body()
    body["request"] = {"model": "synthetic-mesh", "task_type": "image_to_3d", "input": {"source_image": {"asset_id": "asset_" + "a" * 32}}}
    response = client.post("/gateway/v1/admissions", json=body, headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "gateway_capability_not_supported"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayAdmission)) == 0
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_gateway_cursor_collections_scope_and_shared_read_revoke(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    from generation_infrastructure.jobs import JobRepository
    client, headers, info = gateway_client
    remote_jobs = []
    for _ in range(2):
        body = admission_body()
        body["external_task_ref"] = "rc_" + uuid4().hex
        response = client.post("/gateway/v1/admissions", headers=headers, json=body)
        assert response.status_code == 202, response.text
        remote_jobs.append(parse_id("job", response.json()["job_id"]))
    local = JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Local origin"}}, str(uuid4()), "synthetic-collection")
    contents = []
    with pg.begin() as session:
        for job_id in [*remote_jobs, parse_id("job", local["id"])]:
            job = session.get(Job, job_id)
            obj = ObjectRecord(tenant_id=job.tenant_id, project_id=job.project_id, object_key="outputs/collection-" + uuid4().hex, version_id="synthetic-version", bytes=4096, sha256="a" * 64, media_type="image/png", verified_at=utcnow(), expires_at=utcnow() + timedelta(days=1), metadata_json={"private": "never-forward"})
            session.add(obj)
            session.flush()
            artifact = Artifact(tenant_id=job.tenant_id, project_id=job.project_id, key_id=job.key_id, job_id=job.id, object_id=obj.id, position=0)
            session.add(artifact)
            session.flush()
            contents.append(public_id("content", artifact.id))
        policy = session.get(ApiKey, parse_id("key", info["binding"]["key_id"])).policy
    first = client.get("/gateway/v1/jobs", headers=headers, params={"limit": 1})
    assert first.status_code == 200 and first.json()["has_more"]
    second = client.get("/gateway/v1/jobs", headers=headers, params={"limit": 1, "cursor": first.json()["next_cursor"]})
    assert second.status_code == 200 and not second.json()["has_more"]
    assert {row["id"] for response in (first, second) for row in response.json()["data"]} == {public_id("job", identifier) for identifier in remote_jobs}
    shared_policy = {**policy, "content": {"mode": "project_shared", "project_ids": [public_id("prj", catalog["identity"].project_id)], "creator_key_ids": []}, "scopes": [*policy["scopes"], "content:delete"]}
    reader = client.post("/admin/api/v1/gateway-bindings", json={"gateway_id": info["service"]["gateway"]["id"], "tenant_id": public_id("ten", catalog["identity"].tenant_id), "project_id": public_id("prj", catalog["identity"].project_id), "external_tenant_ref": "synthetic-reader", "external_key_ref": "synthetic-reader-key", "name": "Shared reader", "policy": shared_policy}, headers={"Idempotency-Key": str(uuid4())})
    assert reader.status_code == 201, reader.text
    reader_headers = {**headers, "X-External-Tenant-Ref": "synthetic-reader", "X-External-Key-Ref": "synthetic-reader-key"}
    page = client.get("/gateway/v1/content", headers=reader_headers, params={"limit": 1, "model": "synthetic-image"})
    assert page.status_code == 200 and page.json()["has_more"]
    next_page = client.get("/gateway/v1/content", headers=reader_headers, params={"limit": 1, "model": "synthetic-image", "cursor": page.json()["next_cursor"]})
    assert next_page.status_code == 200 and not next_page.json()["has_more"]
    assert {row["id"] for response in (page, next_page) for row in response.json()["data"]} == set(contents[:2])
    assert all(row["external_task_ref"].startswith("rc_") and row["task_type"] == "text_to_image" for row in page.json()["data"])
    assert "never-forward" not in page.text and "tenant_id" not in page.text and "key_id" not in page.text
    assert client.get("/gateway/v1/jobs", headers=reader_headers).json()["data"] == []
    assert client.get("/gateway/v1/content", headers=service_headers(headers)).status_code == 403
    assert client.get("/gateway/v1/content", headers={**headers, "X-External-Key-Ref": "forged"}).status_code == 403
    assert client.get("/gateway/v1/content/" + contents[0], headers=reader_headers).status_code == 200
    assert client.delete("/gateway/v1/content/" + contents[0], headers={**reader_headers, "Idempotency-Key": str(uuid4())}).status_code == 404
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", reader.json()["key_id"]))
        key.policy = {**key.policy, "content": {"mode": "owner_only", "project_ids": [], "creator_key_ids": []}}
    revoked = client.get("/gateway/v1/content", headers=reader_headers, params={"limit": 1, "model": "synthetic-image", "cursor": page.json()["next_cursor"]})
    assert revoked.status_code == 400 and revoked.json()["error"]["code"] == "invalid_cursor"
    assert client.get("/gateway/v1/content", headers=reader_headers).json()["data"] == []
    assert client.get("/gateway/v1/content/" + contents[0], headers=reader_headers).status_code == 404
    assert client.head("/gateway/v1/content/" + contents[0] + "/download", headers={**reader_headers, "Range": "bytes=0-7"}).status_code == 404


def recovery_body(body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    return {**{name: value for name, value in body.items() if name != "request"}, "external_tenant_ref": headers["X-External-Tenant-Ref"], "external_key_ref": headers["X-External-Key-Ref"]}


def service_headers(headers: dict[str, str]) -> dict[str, str]:
    return {"Authorization": headers["Authorization"]}


def test_gateway_realms_and_explicit_customer_mapping(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]]) -> None:
    client, headers, _ = gateway_client
    assert client.get("/gateway/v1/models", headers=headers).status_code == 200
    assert client.get("/gateway/v1/models", headers={**headers, "X-External-Key-Ref": "unknown"}).status_code == 403
    assert client.get("/gateway/v1/models", headers={**headers, "X-Client-IP": "untrusted-text"}).status_code == 403
    assert client.get("/v1/models", headers=headers).status_code == 401
    assert client.post("/internal/v1/claims", json={"protocol_version": "0.4.0", "boot_id": str(uuid4()), "request_id": str(uuid4())}, headers=headers).status_code == 401
    client.cookies.clear()
    assert client.get("/admin/api/v1/gateways", headers=headers).status_code == 401


def test_gateway_model_retrieval_uses_current_mapping_and_scopes(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    path = "/gateway/v1/models/synthetic-image"
    response = client.get(path, headers=headers)
    assert response.status_code == 200 and response.json()["owned_by"] == "render-control"
    assert client.get(path, headers={**headers, "X-External-Key-Ref": "unmapped"}).status_code == 403
    assert client.get(path, headers=service_headers(headers)).status_code == 403
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", info["binding"]["key_id"]))
        key.policy = {**key.policy, "scopes": ["capabilities:read"]}
    assert client.get(path, headers=headers).status_code == 403
    assert client.get("/gateway/v1/capabilities", headers=headers).status_code == 200
    with pg.begin() as session:
        session.get(ExternalIdentityBinding, parse_id("bind", info["binding"]["id"])).enabled = False
    assert client.get("/gateway/v1/capabilities", headers=headers).status_code == 403


def test_gateway_delivery_policy_uses_current_explicit_mapping(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    response = client.get("/gateway/v1/access-policy", headers=headers)
    assert response.status_code == 200 and "content:download" in response.json()["scopes"]
    assert headers["Authorization"] not in response.text and "external_key_ref" not in response.text
    assert client.get("/gateway/v1/access-policy", headers=service_headers(headers)).status_code == 403
    assert client.get("/gateway/v1/access-policy", headers={**headers, "X-External-Key-Ref": "foreign-key"}).status_code == 403
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", info["binding"]["key_id"]))
        key.policy = {**key.policy, "scopes": [scope for scope in key.policy["scopes"] if scope != "content:download"]}
    current = client.get("/gateway/v1/access-policy", headers=headers)
    assert current.status_code == 200 and "content:download" not in current.json()["scopes"]
    with pg.begin() as session:
        session.get(ExternalIdentityBinding, parse_id("bind", info["binding"]["id"])).enabled = False
    assert client.get("/gateway/v1/access-policy", headers=headers).status_code == 403
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_gateway_asset_upload_requires_idempotency_and_current_mapping(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]]) -> None:
    client, headers, _ = gateway_client
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "teal").save(buffer, format="PNG")
    files = {"file": ("private-input.png", buffer.getvalue(), "image/png")}
    assert client.post("/gateway/v1/assets", headers=headers, files=files).status_code == 400
    upload_headers = {**headers, "Idempotency-Key": "stable-gateway-upload"}
    first = client.post("/gateway/v1/assets", headers=upload_headers, files=files)
    assert first.status_code == 201, first.text
    replay = client.post("/gateway/v1/assets", headers=upload_headers, files=files)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert client.post("/gateway/v1/assets", headers={**upload_headers, "X-External-Key-Ref": "unknown"}, files=files).status_code == 403
    assert client.post("/v1/assets", headers=upload_headers, files=files).status_code == 401


def test_pending_cannot_dispatch_and_concurrent_confirmation_accounts_once(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    client, headers, info = gateway_client
    body = admission_body()
    with ThreadPoolExecutor(8) as pool:
        responses = list(pool.map(lambda _: client.post("/gateway/v1/admissions", json=body, headers=headers), range(8)))
    assert all(response.status_code == 202 for response in responses), [r.text for r in responses]
    created = responses[0].json()
    assert len({r.json()["id"] for r in responses}) == 1 and created["job_status"] == "admission_pending"
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    assert leases.claim(catalog["node_id"], catalog["boot_id"]) is None
    key_id = parse_id("key", info["binding"]["key_id"])
    with pg() as session:
        assert session.scalar(select(QuotaBucket.unfinished_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.scope_id == key_id, QuotaBucket.period == "active")) == 1
        assert session.scalar(select(QuotaBucket.daily_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.scope_id == key_id, QuotaBucket.period != "active")) == 0
    url = "/gateway/v1/admissions/" + created["id"] + "/confirm"
    with ThreadPoolExecutor(8) as pool:
        confirmed = list(pool.map(lambda _: client.post(url, headers=headers), range(8)))
    assert all(r.status_code == 200 and r.json()["status"] == "confirmed" for r in confirmed)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(QuotaBucket.daily_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.scope_id == key_id, QuotaBucket.period != "active")) == 1
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == created["job_id"]
    assert client.post("/gateway/v1/admissions", json={**body, "authorization_ref": "changed-reference"}, headers=headers).status_code == 409


def test_expired_confirm_cannot_resurrect_and_settlement_survives_disabled_customer(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    created = client.post("/gateway/v1/admissions", json=admission_body(), headers=headers).json()
    with pg.begin() as session:
        row = session.get(GatewayAdmission, parse_id("adm", created["id"]))
        row.expires_at = utcnow() - timedelta(seconds=1)
    with ThreadPoolExecutor(2) as pool:
        confirm = pool.submit(client.post, "/gateway/v1/admissions/" + created["id"] + "/confirm", headers=headers)
        expire = pool.submit(Maintenance(pg).tick)
        assert confirm.result().status_code == 409
        expire.result()
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", created["job_id"]))
        assert job.state == "expired" and job.current_attempt_id is None
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        key = session.get(ApiKey, parse_id("key", info["binding"]["key_id"]))
        key.enabled = False
    assert client.get("/gateway/v1/jobs/" + created["job_id"], headers=headers).status_code == 403
    events = client.get("/gateway/v1/settlements", headers={"Authorization": headers["Authorization"]}).json()["data"]
    assert len(events) == 1 and events[0]["outcome"] == "expired" and events[0]["image_count"] == 0
    event_url = "/gateway/v1/settlements/" + events[0]["id"] + "/ack"
    assert client.post(event_url, json={"ledger_ref": "synthetic-ledger-once"}, headers=headers).status_code == 200
    assert client.post(event_url, json={"ledger_ref": "synthetic-ledger-once"}, headers=headers).status_code == 200
    assert client.post(event_url, json={"ledger_ref": "different-ledger"}, headers=headers).status_code == 409
    assert client.get("/gateway/v1/settlements", headers=headers).json()["data"] == []


def test_cancel_pending_keeps_admission_and_job_consistent(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    created = client.post("/gateway/v1/admissions", json=admission_body(), headers=headers).json()
    response = client.post("/gateway/v1/jobs/" + created["job_id"] + "/cancel", headers=headers)
    assert response.status_code == 202 and response.json()["status"] == "cancelled"
    replay = client.post("/gateway/v1/admissions/" + created["id"] + "/confirm", headers=headers)
    assert replay.status_code == 409 and replay.json()["status"] == "aborted"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1


def test_confirmation_moves_resource_reservation_to_confirmation_day(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    import generation_infrastructure.jobs as jobs_module
    client, headers, info = gateway_client
    previous = utcnow() - timedelta(days=1)
    monkeypatch.setattr(jobs_module, "utcnow", lambda: previous)
    response = client.post("/gateway/v1/admissions", json=admission_body(), headers=headers)
    assert response.status_code == 202, response.text
    created = response.json()
    monkeypatch.undo()
    with pg.begin() as session:
        session.get(GatewayAdmission, parse_id("adm", created["id"])).expires_at = utcnow() + timedelta(seconds=120)
    result = client.post("/gateway/v1/admissions/" + created["id"] + "/confirm", headers=headers)
    assert result.status_code == 200, result.text
    with pg() as session:
        key_id = parse_id("key", info["binding"]["key_id"])
        old = session.scalar(select(QuotaBucket).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.scope_id == key_id, QuotaBucket.period == previous.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()))
        current = session.scalar(select(QuotaBucket).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.scope_id == key_id, QuotaBucket.period == utcnow().astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()))
        assert (old.daily_jobs, old.daily_image_count) == (0, 0)
        assert (current.daily_jobs, current.daily_image_count) == (1, 1)


def test_request_unknown_is_read_only_and_closed_request_blocks_late_create(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], test_settings: Settings) -> None:
    client, headers, info = gateway_client
    body = admission_body()
    identity = recovery_body(body, headers)
    lookup = client.post("/gateway/v1/admission-requests/lookup", json=identity, headers=service_headers(headers))
    assert lookup.status_code == 200 and lookup.json() == {**identity, "gateway_id": info["service"]["gateway"]["id"], "status": "unknown", "closure_id": None, "admission": None}
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 0
    closed = client.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers))
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert closed.json()["closure_id"].startswith("gcl_") and closed.json()["admission"] is None
    # Reconstruct the application and repository; the closure cannot depend on
    # the first app's in-memory state. This is not a real process crash test.
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50101)) as recovered:
        replay = recovered.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers))
        assert replay.status_code == 200 and replay.json() == closed.json()
        assert recovered.post("/gateway/v1/admission-requests/lookup", json=identity, headers=service_headers(headers)).json() == closed.json()
        delayed = recovered.post("/gateway/v1/admissions", json=body, headers=headers)
        assert delayed.status_code == 409 and delayed.json()["error"]["code"] == "gateway_request_closed"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(GatewayAdmission)) == 0
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_create_and_close_race_has_one_durable_decision(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    admitted = 0
    for _ in range(8):
        body, barrier = admission_body(), Barrier(2)
        identity = recovery_body(body, headers)

        def create(waiter: Barrier, data: dict[str, Any]) -> Any:
            waiter.wait(timeout=10)
            return client.post("/gateway/v1/admissions", json=data, headers=headers)

        def close(waiter: Barrier, data: dict[str, Any]) -> Any:
            waiter.wait(timeout=10)
            return client.post("/gateway/v1/admission-requests/close", json=data, headers=service_headers(headers))

        with ThreadPoolExecutor(2) as pool:
            pending, closing = pool.submit(create, barrier, body), pool.submit(close, barrier, identity)
            created, closed = pending.result(timeout=25), closing.result(timeout=25)
        assert closed.status_code == 200, closed.text
        if created.status_code == 202:
            admitted += 1
            assert closed.json()["status"] == "admitted" and closed.json()["admission"]["id"] == created.json()["id"]
            assert closed.json()["admission"]["status"] == "aborted"
        else:
            assert created.status_code == 409 and created.json()["error"]["code"] == "gateway_request_closed"
            assert closed.json()["status"] == "closed"
        assert client.post("/gateway/v1/admission-requests/lookup", json=identity, headers=service_headers(headers)).json() == closed.json()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 8
        assert session.scalar(select(func.count()).select_from(Job).where(Job.state == "cancelled")) == admitted
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == admitted
        assert session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.kind == "gateway.settlement")) == admitted
        assert session.scalar(select(func.coalesce(func.sum(QuotaBucket.unfinished_jobs), 0))) == 0


def test_pending_request_cleanup_survives_customer_and_binding_revocation(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    body = admission_body()
    admitted = client.post("/gateway/v1/admissions", json=body, headers=headers).json()
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", info["binding"]["key_id"]))
        key.enabled, key.deleted_at = False, utcnow()
        session.get(ExternalIdentityBinding, parse_id("bind", info["binding"]["id"])).enabled = False
    assert client.post("/gateway/v1/admissions/" + admitted["id"] + "/abort", headers=headers).status_code == 403
    identity = recovery_body(body, headers)
    closed = client.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers))
    assert closed.status_code == 200 and closed.json()["status"] == "admitted"
    assert closed.json()["admission"]["id"] == admitted["id"] and closed.json()["admission"]["status"] == "aborted"
    assert closed.json()["closure_id"] is None
    events = client.get("/gateway/v1/settlements", headers=service_headers(headers)).json()["data"]
    assert len(events) == 1 and events[0]["job_id"] == admitted["job_id"] and events[0]["outcome"] == "cancelled"
    assert client.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers)).json() == closed.json()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1


def test_confirmed_request_close_does_not_cancel_or_release(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    body = admission_body()
    created = client.post("/gateway/v1/admissions", json=body, headers=headers).json()
    assert client.post("/gateway/v1/admissions/" + created["id"] + "/confirm", headers=headers).status_code == 200
    with pg.begin() as session:
        session.get(ApiKey, parse_id("key", info["binding"]["key_id"])).enabled = False
    result = client.post("/gateway/v1/admission-requests/close", json=recovery_body(body, headers), headers=service_headers(headers))
    assert result.status_code == 200 and result.json()["status"] == "admitted"
    assert result.json()["admission"]["status"] == "confirmed" and result.json()["admission"]["job_status"] == "queued"
    with pg() as session:
        assert session.get(Job, parse_id("job", created["job_id"])).state == "queued"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0
        assert session.scalar(select(GatewayRequestFence.closed_at)) is None
        assert session.scalar(select(QuotaBucket.unfinished_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.period == "active")) == 1


def test_confirm_and_service_close_serialize_without_resurrection(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    confirmed_count = 0
    for _ in range(8):
        body, barrier = admission_body(), Barrier(2)
        created = client.post("/gateway/v1/admissions", json=body, headers=headers).json()

        def confirm(waiter: Barrier, admission: dict[str, Any]) -> Any:
            waiter.wait(timeout=10)
            return client.post("/gateway/v1/admissions/" + admission["id"] + "/confirm", headers=headers)

        def close(waiter: Barrier, data: dict[str, Any]) -> Any:
            waiter.wait(timeout=10)
            return client.post("/gateway/v1/admission-requests/close", json=recovery_body(data, headers), headers=service_headers(headers))

        with ThreadPoolExecutor(2) as pool:
            confirming, closing = pool.submit(confirm, barrier, created), pool.submit(close, barrier, body)
            confirmation, closure = confirming.result(timeout=25), closing.result(timeout=25)
        assert closure.status_code == 200 and closure.json()["status"] == "admitted", closure.text
        if confirmation.status_code == 200:
            confirmed_count += 1
            assert closure.json()["admission"]["status"] == "confirmed"
        else:
            assert confirmation.status_code == 409 and closure.json()["admission"]["status"] == "aborted"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.state == "queued")) == confirmed_count
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 8 - confirmed_count
        assert session.scalar(select(QuotaBucket.unfinished_jobs).where(QuotaBucket.scope_kind == "3_key", QuotaBucket.period == "active")) == confirmed_count


@pytest.mark.parametrize("field", ["provider_request_id", "external_task_ref", "authorization_ref", "price_version", "external_tenant_ref", "external_key_ref"])
def test_recovery_identity_mismatch_cannot_close_existing_admission(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], field: str) -> None:
    client, headers, _ = gateway_client
    body = admission_body()
    created = client.post("/gateway/v1/admissions", json=body, headers=headers).json()
    identity = recovery_body(body, headers)
    identity[field] = "changed-reference-000001"
    for action in ("lookup", "close"):
        denied = client.post("/gateway/v1/admission-requests/" + action, json=identity, headers=service_headers(headers))
        assert denied.status_code == 409 and denied.json()["error"]["code"] == "gateway_request_conflict"
    with pg() as session:
        assert session.get(GatewayAdmission, parse_id("adm", created["id"])).state == "pending"
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_request_fences_are_isolated_between_gateway_services(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    body = admission_body()
    created = client.post("/gateway/v1/admissions", json=body, headers=headers).json()
    other = client.post("/admin/api/v1/gateways", json={"name": "Synthetic second service", "issuer": "sub2api-second", "allowed_cidrs": ["127.0.0.1/32"]}, headers={"Idempotency-Key": str(uuid4())})
    assert other.status_code == 201
    foreign_headers = {"Authorization": "Bearer " + other.json()["secret"]}
    identity = recovery_body(body, headers)
    assert client.post("/gateway/v1/admission-requests/lookup", json=identity, headers=foreign_headers).json()["status"] == "unknown"
    assert client.post("/gateway/v1/admission-requests/close", json=identity, headers=foreign_headers).json()["status"] == "closed"
    own = client.post("/gateway/v1/admission-requests/lookup", json=identity, headers=service_headers(headers))
    assert own.status_code == 200 and own.json()["admission"]["status"] == "pending"
    assert own.json()["admission"]["id"] == created["id"]
    assert own.json()["gateway_id"] != other.json()["gateway"]["id"]
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 2
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_request_recovery_requires_active_service_realm_and_strict_body(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    identity = recovery_body(admission_body(), headers)
    assert client.post("/gateway/v1/admission-requests/close", json=identity).status_code == 401
    assert client.post("/gateway/v1/admission-requests/close", json={**identity, "request": {}}, headers=service_headers(headers)).status_code == 422
    assert client.post("/gateway/v1/admission-requests/lookup", json={k: v for k, v in identity.items() if k != "authorization_ref"}, headers=service_headers(headers)).status_code == 422
    with pg.begin() as session:
        session.get(GatewayService, parse_id("gateway", info["service"]["gateway"]["id"])).enabled = False
    assert client.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers)).status_code == 401
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 0


def test_service_recovery_limit_does_not_spend_customer_quota(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers, info = gateway_client
    identity = recovery_body(admission_body(), headers)
    with pg.begin() as session:
        clock = [session.scalar(select(func.clock_timestamp()))]
        session.add(RateState(identity="gateway_recovery:" + str(parse_id("gateway", info["service"]["gateway"]["id"])), tokens=0, updated_at=clock[0], recent=[]))
    # Keep the real PostgreSQL rate rows/transactions, while controlling this
    # test's clock: 120/minute refills one token in 500 ms, so a slow HTTP
    # round trip must not change which side of that boundary is being tested.
    monkeypatch.setattr("generation_infrastructure.auth.func", SimpleNamespace(clock_timestamp=lambda: literal(clock[0])))
    denied = client.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers))
    assert denied.status_code == 429
    assert client.post("/gateway/v1/admission-requests/lookup", json=identity, headers=service_headers(headers)).json()["status"] == "unknown"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 0
        assert session.scalar(select(func.count()).select_from(QuotaBucket)) == 0
    clock[0] += timedelta(milliseconds=499)
    assert client.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers)).status_code == 429
    clock[0] += timedelta(milliseconds=1)
    assert client.post("/gateway/v1/admission-requests/close", json=identity, headers=service_headers(headers)).status_code == 200
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 1
        assert session.scalar(select(func.count()).select_from(QuotaBucket)) == 0


def test_closure_and_audit_failure_roll_back_together(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], test_settings: Settings) -> None:
    client, headers, _ = gateway_client
    body = admission_body()
    with pg.begin() as session:
        session.execute(text("""CREATE FUNCTION synthetic_reject_closure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='gateway.request_closed' THEN RAISE EXCEPTION 'synthetic closure audit failure'; END IF; RETURN NEW; END $$"""))
        session.execute(text("CREATE TRIGGER synthetic_reject_closure BEFORE INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION synthetic_reject_closure()"))
    try:
        # Verify the actual redacted HTTP failure as well as the PG rollback.
        with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50102), raise_server_exceptions=False) as failed:
            result = failed.post("/gateway/v1/admission-requests/close", json=recovery_body(body, headers), headers=service_headers(headers))
            assert result.status_code == 500 and result.json()["error"]["code"] == "internal_error"
            assert "synthetic closure audit failure" not in result.text and "INSERT INTO" not in result.text
    finally:
        with pg.begin() as session:
            session.execute(text("DROP TRIGGER synthetic_reject_closure ON audit_events"))
            session.execute(text("DROP FUNCTION synthetic_reject_closure()"))
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence)) == 0
    assert client.post("/gateway/v1/admissions", json=body, headers=headers).status_code == 202


def fence_migration(*, guards: bool = False) -> ModuleType:
    filename = "3e15b92f08dc_gateway_fence_guards.py" if guards else "6b7d9e51c420_gateway_request_fences.py"
    path = Path(__file__).resolve().parents[2] / "migrations/versions" / filename
    spec = spec_from_file_location("fence_migration_under_test", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fence_migration_backfills_existing_admission_and_disabled_binding(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    body = admission_body()
    created = client.post("/gateway/v1/admissions", json=body, headers=headers).json()
    with pg.begin() as session:
        session.get(ExternalIdentityBinding, parse_id("bind", info["binding"]["id"])).enabled = False
        original_id = session.scalar(select(GatewayRequestFence.id))
    # PG transactional DDL isolates this real upgrade replay. The outer rollback
    # restores the current fixture schema; no other database is involved.
    with pg() as session:
        connection = session.connection()
        session.execute(text("DROP TABLE gateway_request_fences"))
        with Operations.context(MigrationContext.configure(connection)):
            fence_migration().upgrade()
        row = session.execute(text("SELECT id, external_tenant_ref, external_key_ref, authorization_ref, closed_at FROM gateway_request_fences")).mappings().one()
        assert row["id"] == parse_id("adm", created["id"]) and row["authorization_ref"] == body["authorization_ref"]
        assert row["external_tenant_ref"] == headers["X-External-Tenant-Ref"] and row["external_key_ref"] == headers["X-External-Key-Ref"] and row["closed_at"] is None
    with pg() as session:
        assert session.scalar(select(GatewayRequestFence.id)) == original_id


@pytest.mark.parametrize("guards", [False, True])
def test_fence_migration_refuses_downgrade_after_committed_closure(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], guards: bool) -> None:
    client, headers, _ = gateway_client
    result = client.post("/gateway/v1/admission-requests/close", json=recovery_body(admission_body(), headers), headers=service_headers(headers))
    assert result.status_code == 200 and result.json()["status"] == "closed"
    with pg() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            with pytest.raises(DBAPIError, match="durable request closures prohibit downgrade"):
                fence_migration(guards=guards).downgrade()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayRequestFence).where(GatewayRequestFence.closed_at.is_not(None))) == 1


@pytest.mark.parametrize("kind", ["missing", "closed", "wrong_authorization"])
def test_database_fence_blocks_legacy_admission_insert(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], kind: str) -> None:
    client, headers, _ = gateway_client
    body = admission_body()
    created = client.post("/gateway/v1/admissions", json=body, headers=headers).json()
    target = body if kind == "wrong_authorization" else admission_body()
    if kind == "closed":
        assert client.post("/gateway/v1/admission-requests/close", json=recovery_body(target, headers), headers=service_headers(headers)).json()["status"] == "closed"
    # A direct/legacy writer deliberately bypasses the repository's fence check.
    # The BEFORE INSERT check must reject it before ordinary unique constraints.
    with pg() as session:
        with pytest.raises(DBAPIError, match="gateway_request_fence_required"):
            session.execute(text("""
                INSERT INTO gateway_admissions
                  (id,created_at,tenant_id,project_id,key_id,gateway_id,binding_id,job_id,
                   provider_request_id,external_task_ref,intent_digest,price_version,
                   authorization_ref,key_revision,binding_revision,state,expires_at)
                SELECT :new_id,created_at,tenant_id,project_id,key_id,gateway_id,binding_id,job_id,
                       :provider_request_id,:external_task_ref,intent_digest,:price_version,
                       :authorization_ref,key_revision,binding_revision,state,expires_at
                FROM gateway_admissions WHERE id=:original
            """), {"new_id": uuid4(), "original": parse_id("adm", created["id"]), **{key: value for key, value in target.items() if key != "request"}, "authorization_ref": "changed-reference" if kind == "wrong_authorization" else target["authorization_ref"]})
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayAdmission)) == 1


@pytest.mark.parametrize("change", ["DELETE FROM gateway_request_fences", "UPDATE gateway_request_fences SET closed_at=NULL", "UPDATE gateway_request_fences SET authorization_ref='changed-reference'"])
def test_database_fence_cannot_be_removed_reopened_or_rebound(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], change: str) -> None:
    client, headers, _ = gateway_client
    body = admission_body()
    closed = client.post("/gateway/v1/admission-requests/close", json=recovery_body(body, headers), headers=service_headers(headers))
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    with pg() as session:
        with pytest.raises(DBAPIError, match="gateway_request_fence_immutable"):
            session.execute(text(change))
    assert client.post("/gateway/v1/admission-requests/lookup", json=recovery_body(body, headers), headers=service_headers(headers)).json() == closed.json()
    assert client.post("/gateway/v1/admissions", json=body, headers=headers).status_code == 409


def test_database_cannot_close_existing_admission_or_change_its_binding(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    body = admission_body()
    assert client.post("/gateway/v1/admissions", json=body, headers=headers).status_code == 202
    with pg() as session:
        with pytest.raises(DBAPIError, match="gateway_request_already_admitted"):
            session.execute(text("UPDATE gateway_request_fences SET closed_at=NOW()"))
    with pg() as session:
        with pytest.raises(DBAPIError, match="gateway_admission_identity_immutable"):
            session.execute(text("UPDATE gateway_admissions SET authorization_ref='changed-reference'"))
    assert client.post("/gateway/v1/admission-requests/lookup", json=recovery_body(body, headers), headers=service_headers(headers)).json()["admission"]["status"] == "pending"


def test_guard_migration_backfills_gap_and_restores_guards(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    created = client.post("/gateway/v1/admissions", json=admission_body(), headers=headers).json()
    with pg() as session:
        connection = session.connection()
        session.execute(text("DROP TRIGGER preserve_gateway_request_fence ON gateway_request_fences; DROP FUNCTION preserve_gateway_request_fence(); DROP TRIGGER enforce_gateway_admission_fence ON gateway_admissions; DROP FUNCTION enforce_gateway_admission_fence(); DELETE FROM gateway_request_fences"))
        with Operations.context(MigrationContext.configure(connection)):
            fence_migration(guards=True).upgrade()
        assert session.scalar(select(GatewayRequestFence.id)) == parse_id("adm", created["id"])
        assert session.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname IN ('preserve_gateway_request_fence','enforce_gateway_admission_fence') AND NOT tgisinternal")) == 2
    # All destructive fixture DDL above is rolled back by Session.close().
    with pg() as session:
        assert session.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname IN ('preserve_gateway_request_fence','enforce_gateway_admission_fence') AND NOT tgisinternal")) == 2


def test_guard_migration_refuses_conflicting_closed_history(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session]) -> None:
    client, headers, _ = gateway_client
    assert client.post("/gateway/v1/admissions", json=admission_body(), headers=headers).status_code == 202
    with pg() as session:
        session.execute(text("DROP TRIGGER preserve_gateway_request_fence ON gateway_request_fences; UPDATE gateway_request_fences SET closed_at=NOW()"))
        with Operations.context(MigrationContext.configure(session.connection())):
            with pytest.raises(DBAPIError, match="gateway_request_backfill_conflict"):
                fence_migration(guards=True).upgrade()
    with pg() as session:
        assert session.scalar(select(GatewayRequestFence.closed_at)) is None
