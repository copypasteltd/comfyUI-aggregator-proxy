from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.defaults import default_key_policy, default_limits
from generation_domain.values import public_id, utcnow
from generation_infrastructure.access_events import (
    AccessEventWriter,
    AccessRecord,
    prune_access_events,
)
from generation_infrastructure.models import (
    AccessEvent,
    AdminUser,
    ApiCredential,
    ApiKey,
    ExternalIdentityBinding,
    GatewayService,
    Job,
    Project,
    Tenant,
)
from generation_infrastructure.security import issue_secret, password_hash, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-access-tests-password"


@pytest.fixture
def http(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> Iterator[tuple[TestClient, str, UUID]]:
    identifier, secret = issue_secret("sk-gen")
    key_id = catalog["identity"].key_id
    with pg.begin() as session:
        key = session.get(ApiKey, key_id)
        assert key
        key.policy = {**key.policy, "limits": {name: {"mode": "unlimited", "value": None} for name in key.policy["limits"]}}
        session.add(ApiCredential(key_id=key_id, identifier=identifier, verifier=verifier(secret, test_settings.credential_pepper.get_secret_value())))
        session.add_all([AdminUser(username="access-admin", password_hash=password_hash(PASSWORD), role="administrator"), AdminUser(username="access-observer", password_hash=password_hash(PASSWORD), role="observer")])
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin, client=("127.0.0.1", 50900)) as client:
        client.headers["Origin"] = test_settings.public_origin
        yield client, secret, key_id


def login(client: TestClient, username: str = "access-admin") -> None:
    response = client.post("/admin/api/v1/session", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def path(key_id: UUID) -> str:
    return "/admin/api/v1/api-keys/" + public_id("key", key_id) + "/access-events"


def seed_event(identity: Any, **values: Any) -> AccessEvent:
    data = {"request_id": uuid4(), "tenant_id": identity.tenant_id, "project_id": identity.project_id, "key_id": identity.key_id, "started_at": utcnow(), "entry": "direct", "method": "GET", "route": "/v1/access-policy", "peer_ip": "127.0.0.1", "client_ip": "127.0.0.1", "status_code": 200, "error_code": None, "completion": "completed", "duration_milliseconds": 1, "request_bytes": 0, "response_bytes": 2}
    return AccessEvent(**{**data, **values})


def test_http_records_real_results_without_payloads_or_client_identity(http: tuple[TestClient, str, UUID], pg: sessionmaker[Session]) -> None:
    client, secret, key_id = http
    supplied_request_id = str(uuid4())
    headers = {"Authorization": "Bearer " + secret, "X-Request-ID": supplied_request_id, "X-Forwarded-For": "198.51.100.20", "X-Client-IP": "198.51.100.21", "X-External-Key-Ref": "Synthetic-private-external-reference"}
    first = client.get("/v1/access-policy?credential=Synthetic-private-query", headers=headers)
    assert first.status_code == 200
    invalid = client.post("/v1/jobs", json={"input": {"prompt": "Synthetic-private-prompt"}}, headers=headers)
    assert invalid.status_code == 422
    bad_id = client.get("/v1/assets/Synthetic-private-path", headers=headers)
    assert bad_id.status_code == 404
    with pg() as session:
        rows = list(session.scalars(select(AccessEvent).order_by(AccessEvent.created_at)))
        assert len(rows) == 3 and all(row.key_id == key_id for row in rows)
        assert {row.status_code for row in rows} == {200, 404, 422}
        assert rows[0].request_id == UUID(first.headers["X-Request-ID"]) != UUID(supplied_request_id)
        assert rows[-1].route == "/v1/assets/{identifier}"
        assert all(row.peer_ip == row.client_ip == "127.0.0.1" for row in rows)
        assert all(row.completion == "completed" and row.response_bytes > 0 for row in rows)
        assert rows[1].request_bytes == len(invalid.request.content)
        serialized = json.dumps([{column.name: str(getattr(row, column.name)) for column in AccessEvent.__table__.columns} for row in rows])
        for private in (secret, "Synthetic-private-query", "Synthetic-private-prompt", "Synthetic-private-path", "Synthetic-private-external-reference"):
            assert private not in serialized
    login(client)
    result = client.get(path(key_id)).json()
    assert len(result["data"]) == 3 and result["capture_health"]["written"] == 3
    assert result["data"][0]["error_code"] and result["next_cursor"] is None


def test_verified_denials_rotation_and_invalid_secret_are_attributed_correctly(http: tuple[TestClient, str, UUID], pg: sessionmaker[Session], test_settings: Settings) -> None:
    client, secret, key_id = http
    headers = {"Authorization": "Bearer " + secret}
    assert client.get("/v1/access-policy", headers=headers).status_code == 200
    wrong = secret.partition(".")[0] + "." + "x" * 43
    assert client.get("/v1/access-policy", headers={"Authorization": "Bearer " + wrong}).status_code == 401
    with pg.begin() as session:
        credential = session.scalar(select(ApiCredential).where(ApiCredential.key_id == key_id))
        assert credential
        credential.expires_at = utcnow() - timedelta(seconds=1)
    assert client.get("/v1/access-policy", headers=headers).status_code == 401
    with pg.begin() as session:
        credential, key = session.scalar(select(ApiCredential).where(ApiCredential.key_id == key_id)), session.get(ApiKey, key_id)
        assert credential and key
        credential.expires_at = None
        key.expires_at = utcnow() - timedelta(seconds=1)
    assert client.get("/v1/access-policy", headers=headers).status_code == 401
    with pg.begin() as session:
        key = session.get(ApiKey, key_id)
        assert key
        key.expires_at = None
        key.enabled = False
    assert client.get("/v1/access-policy", headers=headers).status_code == 401
    with pg.begin() as session:
        key = session.get(ApiKey, key_id)
        assert key
        key.enabled = True
        key.policy = {**key.policy, "allowed_cidrs": {"mode": "allowlist", "values": ["192.0.2.0/24"]}}
    assert client.get("/v1/access-policy", headers=headers).status_code == 403
    with pg.begin() as session:
        key = session.get(ApiKey, key_id)
        assert key
        key.policy = {**key.policy, "allowed_cidrs": {"mode": "any", "values": []}, "limits": {**key.policy["limits"], "rpm": {"mode": "bounded", "value": 1}, "burst": {"mode": "bounded", "value": 1}}}
    assert client.get("/v1/access-policy", headers=headers).status_code == 200
    assert client.get("/v1/access-policy", headers=headers).status_code == 429
    login(client)
    rotated = client.post("/admin/api/v1/api-keys/" + public_id("key", key_id) + "/actions", json={"action": "rotate", "expected_revision": 1, "overlap_seconds": 0}, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'})
    assert rotated.status_code == 200
    assert client.get("/v1/access-policy", headers=headers).status_code == 401
    with pg.begin() as session:
        key = session.get(ApiKey, key_id)
        assert key
        key.policy = {**key.policy, "limits": {name: {"mode": "unlimited", "value": None} for name in key.policy["limits"]}}
    assert client.get("/v1/access-policy", headers={"Authorization": "Bearer " + rotated.json()["secret"]}).status_code == 200
    with pg() as session:
        all_rows = list(session.scalars(select(AccessEvent)))
        attributed = [row for row in all_rows if row.key_id == key_id]
        anonymous = [row for row in all_rows if row.key_id is None]
        assert len(anonymous) == 1 and anonymous[0].status_code == 401
        assert len(attributed) == 9
        assert {row.error_code for row in attributed} >= {"invalid_credentials", "ip_denied", "rate_limited"}


def test_access_history_requires_management_session_and_not_other_realms(http: tuple[TestClient, str, UUID], pg: sessionmaker[Session]) -> None:
    client, secret, key_id = http
    assert client.get(path(key_id), headers={"Authorization": "Bearer " + secret}).status_code == 401
    login(client, "access-observer")
    assert client.get(path(key_id)).status_code == 403
    login(client)
    assert client.get(path(key_id)).status_code == 200
    assert client.get(path(uuid4())).status_code == 404
    assert client.get(path(key_id), params={"result": "private"}).status_code == 422
    for cursor in ("", "!invalid", "x" * 129):
        assert client.get(path(key_id), params={"before": cursor}).status_code == 422
    # Only business ingress is observed; the management reads above do not log themselves.
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AccessEvent)) == 0


def test_gateway_records_only_verified_explicit_subject_and_trusted_ip(http: tuple[TestClient, str, UUID], catalog: dict[str, Any], pg: sessionmaker[Session], test_settings: Settings) -> None:
    client, local_secret, local_key = http
    identifier, secret = issue_secret("gateway")
    with pg.begin() as session:
        service = GatewayService(name="Synthetic log gateway", issuer="sub2api-log-test", credential_identifier=identifier, credential_verifier=verifier(secret, test_settings.credential_pepper.get_secret_value()), allowed_cidrs=["127.0.0.1/32"])
        session.add(service)
        session.flush()
        policy = default_key_policy()
        policy["limits"] = {name: {"mode": "unlimited", "value": None} for name in policy["limits"]}
        key = ApiKey(tenant_id=catalog["identity"].tenant_id, project_id=catalog["identity"].project_id, name="Synthetic external log Key", issuer="gateway:" + service.id.hex, policy=policy)
        session.add(key)
        session.flush()
        session.add(ExternalIdentityBinding(tenant_id=key.tenant_id, project_id=key.project_id, gateway_id=service.id, key_id=key.id, external_tenant_ref="Synthetic-private-tenant-ref", external_key_ref="Synthetic-private-key-ref"))
        external_id, service_id = key.id, service.id
    heads = {"Authorization": "Bearer " + secret, "X-External-Tenant-Ref": "Synthetic-private-tenant-ref", "X-External-Key-Ref": "Synthetic-private-key-ref", "X-Client-IP": "203.0.113.9"}
    assert client.get("/gateway/v1/models", headers=heads).status_code == 200
    assert client.get("/v1/access-policy", headers={**heads, "Authorization": "Bearer " + local_secret}).status_code == 200
    invalid = {**heads, "Authorization": "Bearer " + secret.partition(".")[0] + "." + "x" * 43}
    assert client.get("/gateway/v1/models", headers=invalid).status_code == 401
    assert client.get("/gateway/v1/models", headers={**heads, "X-External-Key-Ref": "unmapped"}).status_code == 403
    with pg.begin() as session:
        key = session.get(ApiKey, external_id)
        assert key
        key.enabled = False
    assert client.get("/gateway/v1/models", headers=heads).status_code == 403
    assert client.get("/gateway/v1/settlements", headers={"Authorization": "Bearer " + secret}).status_code == 200
    with pg() as session:
        rows = list(session.scalars(select(AccessEvent).order_by(AccessEvent.created_at)))
        external = [row for row in rows if row.key_id == external_id]
        assert len(external) == 2 and all(row.gateway_id == service_id and row.client_ip == "203.0.113.9" and row.peer_ip == "127.0.0.1" for row in external)
        local = next(row for row in rows if row.key_id == local_key)
        assert local.gateway_id is None and local.client_ip == "127.0.0.1"
        assert len([row for row in rows if row.key_id is None]) == 3
        assert next(row for row in rows if row.status_code == 401).gateway_id is None
        assert rows[-1].key_id is None and rows[-1].gateway_id == service_id
    login(client)
    body = client.get(path(external_id)).text
    assert secret not in body and "Synthetic-private-key-ref" not in body and "Synthetic-private-tenant-ref" not in body


def test_cursor_pages_keep_key_filter_and_upper_bound_with_deleted_key(http: tuple[TestClient, str, UUID], catalog: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, _, key_id = http
    now = utcnow() - timedelta(hours=1)
    with pg.begin() as session:
        for index in range(105):
            code = (200, 403, 503)[index % 3]
            session.add(seed_event(catalog["identity"], created_at=now + timedelta(seconds=index), status_code=code, error_code="synthetic_denied" if code >= 400 else None, completion="disconnected" if index == 0 else "completed"))
        other = ApiKey(tenant_id=catalog["identity"].tenant_id, project_id=catalog["identity"].project_id, name="Other scoped key", policy=default_key_policy())
        session.add(other)
        session.flush()
        other_id = other.id
    login(client)
    first = client.get(path(key_id)).json()
    assert len(first["data"]) == 50 and first["next_cursor"]
    with pg.begin() as session:
        session.add(seed_event(catalog["identity"]))
        key = session.get(ApiKey, key_id)
        assert key
        key.deleted_at, key.enabled = utcnow(), False
    second = client.get(path(key_id), params={"before": first["next_cursor"]}).json()
    third = client.get(path(key_id), params={"before": second["next_cursor"]}).json()
    repeated_first = client.get(path(key_id), params={"before": third["first_cursor"]}).json()
    ids = [row["id"] for batch in (first, second, third) for row in batch["data"]]
    assert len(ids) == len(set(ids)) == 105 and len(third["data"]) == 5 and third["next_cursor"] is None
    assert repeated_first["data"] == first["data"] and repeated_first["deleted_at"]
    assert client.get(path(other_id), params={"before": first["next_cursor"]}).status_code == 422
    assert client.get(path(key_id), params={"before": first["next_cursor"], "result": "denied"}).status_code == 422
    for result, count in (("success", 35), ("denied", 35), ("error", 36)):
        data = client.get(path(key_id), params={"result": result}).json()["data"]
        assert len(data) == count and all(row["result"] == result for row in data)
    assert client.get(path(other_id)).json()["data"] == []


def test_access_foreign_keys_and_request_id_uniqueness(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    identity = catalog["identity"]
    with pg.begin() as session:
        tenant = Tenant(name="Second access scope", limits=default_limits())
        session.add(tenant)
        session.flush()
        project = Project(tenant_id=tenant.id, name="Second access project", limits=default_limits())
        session.add(project)
        session.flush()
        other = ApiKey(tenant_id=tenant.id, project_id=project.id, name="Second access key", policy=default_key_policy())
        session.add(other)
        session.flush()
        other_project, other_tenant = project.id, tenant.id
    request_id = uuid4()
    with pg.begin() as session:
        session.add(seed_event(identity, request_id=request_id))
    for values in ({"request_id": request_id}, {"tenant_id": other_tenant, "project_id": other_project}, {"project_id": None}, {"entry": "gateway", "gateway_id": None}):
        with pytest.raises(IntegrityError), pg.begin() as session:
            session.add(seed_event(identity, **values))


def test_gateway_access_scope_cannot_reference_another_service(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    identity = catalog["identity"]
    with pg.begin() as session:
        services = [GatewayService(name="Synthetic scope service " + str(index), issuer="access-scope-" + str(index), credential_identifier=uuid4().hex, credential_verifier="0" * 64, allowed_cidrs=["127.0.0.1/32"]) for index in range(2)]
        session.add_all(services)
        session.flush()
        key = ApiKey(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Bound access Key", issuer="gateway:" + services[0].id.hex, policy=default_key_policy())
        session.add(key)
        session.flush()
        session.add(ExternalIdentityBinding(tenant_id=key.tenant_id, project_id=key.project_id, key_id=key.id, gateway_id=services[0].id, external_tenant_ref="synthetic-scope-tenant", external_key_ref="synthetic-scope-key"))
        key_id, correct, incorrect = key.id, services[0].id, services[1].id
    with pg.begin() as session:
        session.add(seed_event(identity, key_id=key_id, entry="gateway", gateway_id=correct))
    with pytest.raises(IntegrityError), pg.begin() as session:
        session.add(seed_event(identity, key_id=key_id, entry="gateway", gateway_id=incorrect))


def test_bounded_retention_skips_locked_rows_and_keeps_live_data(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    with pg.begin() as session:
        old = utcnow() - timedelta(days=31)
        session.add_all([seed_event(catalog["identity"], created_at=old) for _ in range(501)])
        session.add(seed_event(catalog["identity"], created_at=utcnow() - timedelta(days=29)))
    with pg.begin() as blocker:
        blocker.scalar(select(AccessEvent).where(AccessEvent.created_at < utcnow() - timedelta(days=30)).order_by(AccessEvent.id).limit(1).with_for_update())
        assert prune_access_events(pg, 30) == 500
        assert prune_access_events(pg, 30) == 0
    assert prune_access_events(pg, 30) == 1
    assert prune_access_events(pg, 30) == 0
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AccessEvent)) == 1


def test_logging_lock_failure_does_not_change_accepted_job_or_replay(http: tuple[TestClient, str, UUID], pg: sessionmaker[Session]) -> None:
    client, secret, _ = http
    headers = {"Authorization": "Bearer " + secret, "Idempotency-Key": str(uuid4())}
    body = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic log failure test"}}
    with pg.begin() as blocker:
        blocker.execute(text("LOCK TABLE access_events IN ACCESS EXCLUSIVE MODE"))
        first = client.post("/v1/jobs", json=body, headers=headers)
        assert first.status_code == 202
        assert client.app.state.access_writer.health()["failed"] == 1
    again = client.post("/v1/jobs", json=body, headers=headers)
    assert again.status_code == 202 and again.json()["id"] == first.json()["id"]
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(AccessEvent)) == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_writer_capacity_is_bounded_even_when_caller_is_cancelled(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch, cancel: bool) -> None:
    writer = AccessEventWriter(test_settings)
    identity = catalog["identity"]
    event = AccessRecord(uuid4(), utcnow(), identity.tenant_id, identity.project_id, identity.key_id, None, "direct", "GET", "/v1/access-policy", "127.0.0.1", "127.0.0.1", 200, None, "completed", 1, 0, 2)
    release, started = threading.Event(), threading.Event()
    entered, guard = [0], threading.Lock()
    original = writer._write
    def blocked(record: AccessRecord) -> bool:
        with guard:
            entered[0] += 1
            if entered[0] == 2:
                started.set()
        release.wait(timeout=5)
        return original(record)
    monkeypatch.setattr(writer, "_write", blocked)
    tasks = [asyncio.create_task(writer.record(replace(event, request_id=uuid4()))) for _ in range(2)]
    try:
        assert await asyncio.to_thread(started.wait, 3)
        if cancel:
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
        assert not await asyncio.wait_for(writer.record(replace(event, request_id=uuid4())), timeout=2)
        assert writer.health()["overloaded"] == 1
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await writer.close()
    assert writer.health()["written"] == 2


async def test_anonymous_ingress_is_sampled_without_infinite_rows(pg: sessionmaker[Session], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [60.0]
    monkeypatch.setattr("generation_infrastructure.access_events.time", SimpleNamespace(monotonic=lambda: clock[0]))
    writer = AccessEventWriter(test_settings)
    event = AccessRecord(uuid4(), utcnow(), None, None, None, None, "direct", "GET", "/v1/*", "127.0.0.1", None, 401, "invalid_credentials", "completed", 1, 0, 2)
    try:
        for _ in range(31):
            await writer.record(replace(event, request_id=uuid4()))
        assert writer.health()["written"] == 30 and writer.health()["anonymous_skipped"] == 1
        clock[0] = 120.0
        assert await writer.record(replace(event, request_id=uuid4()))
        assert writer.health()["written"] == 31 and writer.health()["anonymous_skipped"] == 1
    finally:
        await writer.close()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AccessEvent)) == 31
