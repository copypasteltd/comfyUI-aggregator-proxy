from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.gateway_catalogs import CatalogPublication
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, parse_id, public_id
from generation_infrastructure.gateway_catalogs import GatewayCatalogAdministration
from generation_infrastructure.models import (
    AdminUser,
    AuditEvent,
    Channel,
    GatewayAdmission,
    GatewayCatalog,
    GatewayCatalogReceipt,
    GatewayCatalogRelease,
    GatewayService,
    PublicModel,
    WorkflowBinding,
)
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_gateway import admission_body
from tests.integration.test_gateway import gateway_client as gateway_client

pytestmark = pytest.mark.integration


def test_catalog_gateway_options_are_bounded_literal_and_omit_identity_configuration(gateway_client: Any, pg: sessionmaker[Session]) -> None:
    client, _, _ = gateway_client
    with pg.begin() as session:
        for index in range(52):
            session.add(GatewayService(name=f"Catalog % literal {index:02}", issuer="synthetic-catalog", allowed_cidrs=["192.0.2.1/32"], credential_identifier=uuid4().hex, credential_verifier="0" * 64))
        session.add(GatewayService(name="Catalog ordinary", issuer="synthetic-catalog", allowed_cidrs=["192.0.2.1/32"], credential_identifier=uuid4().hex, credential_verifier="0" * 64, enabled=False))
    first = client.get("/admin/api/v1/gateway-catalog-options", params={"q": "%", "page": 1})
    assert first.status_code == 200
    value = first.json()
    assert len(value["data"]) == 50 and value["has_more"]
    assert all(set(row) == {"id", "name", "enabled"} and "%" in row["name"] for row in value["data"])
    second = client.get("/admin/api/v1/gateway-catalog-options", params={"q": "%", "page": 2}).json()
    assert len(second["data"]) == 2 and not second["has_more"]
    assert not {row["id"] for row in value["data"]} & {row["id"] for row in second["data"]}
    disabled = client.get("/admin/api/v1/gateway-catalog-options", params={"q": "ordinary"}).json()
    assert len(disabled["data"]) == 1 and not disabled["data"][0]["enabled"]
    for query in ({"page": 0}, {"page": 1001}, {"q": "x" * 129}):
        assert client.get("/admin/api/v1/gateway-catalog-options", params=query).status_code == 422


def test_catalog_gateway_selection_does_not_grant_gateway_credential_management(gateway_client: Any, pg: sessionmaker[Session]) -> None:
    client, _, info = gateway_client
    password = "Synthetic-observer-catalog-password"
    with pg.begin() as session:
        session.add(AdminUser(username="catalog-observer", password_hash=password_hash(password), role="observer"))
    login = client.post("/admin/api/v1/session", json={"username": "catalog-observer", "password": password})
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    result = client.get("/admin/api/v1/gateway-catalog-options")
    assert result.status_code == 200 and info["service"]["gateway"]["id"] in {row["id"] for row in result.json()["data"]}
    assert client.get("/admin/api/v1/gateways").status_code == 403
    assert client.get(base_path(info)).status_code == 200
    assert client.post(base_path(info) + "/replay", json={"action": "rollback", "expected_revision": 1, "source_revision": 1}, headers={"If-Match": '"1"', "Idempotency-Key": str(uuid4())}).status_code == 403
    assert client.delete("/admin/api/v1/session").status_code == 204
    assert client.get("/admin/api/v1/gateway-catalog-options").status_code == 401


@pytest.fixture
def published_model(pg: sessionmaker[Session], catalog: dict[str, Any]) -> dict[str, Any]:
    # Protocol-only fixture: this is not a real GPU runtime approval or release.
    with pg.begin() as session:
        for binding in session.scalars(select(WorkflowBinding)):
            binding.runtime_approval = {"profile_id": "synthetic-protocol-fixture"}
        model = session.scalar(select(PublicModel).where(PublicModel.alias == "synthetic-image"))
        assert model
        return {"expected_revision": 0, "instance_id": "synthetic-provider", "models": [{"usage_account_id": "9223372036854775807", "group_ids": ["2", "1"], "price_version": "synthetic-price-v1", "unit_price": "0.05", "model_bindings": [{"model_id": public_id("mdl", model.id), "expected_revision": model.revision}]}]}


def base_path(info: dict[str, Any]) -> str:
    return "/admin/api/v1/gateways/" + info["service"]["gateway"]["id"] + "/catalog"


def publish(client: TestClient, info: dict[str, Any], body: dict[str, Any], *, token: str | None = None) -> Any:
    return client.post(base_path(info) + "/releases", json=body, headers={"If-Match": '"' + str(body["expected_revision"]) + '"', "Idempotency-Key": token or str(uuid4())})


def stamp(release: dict[str, Any]) -> dict[str, Any]:
    return {name: release[name] for name in ("instance_id", "revision", "digest")}


def acknowledge(client: TestClient, headers: dict[str, str], release: dict[str, Any], **changes: Any) -> Any:
    return client.post("/gateway/v1/catalog/receipts", headers=headers, json={**stamp(release), "status": "applied", **changes})


def test_confirmed_catalog_fences_new_requests_and_preserves_existing_intents(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session], test_settings: Settings) -> None:
    client, headers, info = gateway_client
    path = base_path(info)
    assert client.get(path).json()["status"] == "legacy"
    legacy = admission_body()
    original = client.post("/gateway/v1/admissions", json=legacy, headers=headers)
    assert original.status_code == 202
    response = publish(client, info, published_model)
    assert response.status_code == 201, response.text
    first = response.json()
    assert hashlib.sha256(first["configuration_json"].encode()).hexdigest() == first["digest"]
    configuration = json.loads(first["configuration_json"])
    assert configuration["models"][0]["usage_account_id"] == "9223372036854775807"
    assert configuration["models"][0]["unit_price"] == "0.05000000"
    assert configuration["models"][0]["group_ids"] == ["1", "2"]
    pending = client.get(path).json()
    assert pending["status"] == "pending" and not pending["managed"] and pending["confirmed"] is None
    assert client.post("/gateway/v1/admissions", json=admission_body(), headers=headers).status_code == 202
    assert acknowledge(client, headers, first).json()["activated"]
    state = client.get(path).json()
    assert state["status"] == "confirmed" and state["managed"]
    # A fresh repository/session reads committed state; no process-local catalog.
    assert GatewayCatalogAdministration(pg, test_settings).state(parse_id("gateway", info["service"]["gateway"]["id"])) == state
    assert client.post("/gateway/v1/admissions", json=admission_body(), headers=headers).status_code == 409
    request = {**admission_body(), "catalog": stamp(first)}
    accepted = client.post("/gateway/v1/admissions", json=request, headers=headers)
    assert accepted.status_code == 202, accepted.text
    with pg() as session:
        admission = session.get(GatewayAdmission, parse_id("adm", accepted.json()["id"]))
        assert admission and (admission.catalog_revision, admission.catalog_digest, admission.catalog_instance_id) == (1, first["digest"], "synthetic-provider")
    second = publish(client, info, {**published_model, "expected_revision": 1, "models": []}).json()
    assert client.get(path).json()["confirmed"]["revision"] == 1
    assert client.post("/gateway/v1/admissions", json={**admission_body(), "catalog": stamp(first)}, headers=headers).status_code == 202
    assert acknowledge(client, headers, second).json()["activated"]
    assert client.post("/gateway/v1/admissions", json={**admission_body(), "catalog": stamp(second)}, headers=headers).status_code == 409
    assert client.post("/gateway/v1/admissions", json={**admission_body(), "catalog": stamp(first)}, headers=headers).status_code == 409
    assert client.post("/gateway/v1/admissions", json=request, headers=headers).json()["id"] == accepted.json()["id"]
    assert client.post("/gateway/v1/admissions", json=legacy, headers=headers).json()["id"] == original.json()["id"]
    changed = {**request, "catalog": stamp(second)}
    assert client.post("/gateway/v1/admissions", json=changed, headers=headers).status_code == 409


def test_rejected_and_duplicate_receipts_are_monotonic_and_audited_once(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    response = acknowledge(client, headers, first, status="rejected", error_code="storage_unavailable")
    assert response.status_code == 200, response.text
    assert not response.json()["activated"] and response.json()["state"]["status"] == "rejected"
    replay = acknowledge(client, headers, first, status="rejected", error_code="storage_unavailable")
    assert replay.json() == response.json()
    for _ in range(2):
        assert acknowledge(client, headers, first).json()["activated"]
    assert acknowledge(client, headers, first, status="rejected", error_code="apply_failed").status_code == 409
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayCatalogReceipt)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action.in_(["gateway.catalog_rejected", "gateway.catalog_applied"]))) == 2
    second = publish(client, info, {**published_model, "expected_revision": 1}).json()
    assert acknowledge(client, headers, first).json()["activated"]  # Still the active version.
    assert acknowledge(client, headers, second).json()["activated"]
    old = acknowledge(client, headers, first).json()
    assert not old["activated"] and old["state"]["confirmed"]["revision"] == 2


def test_superseded_unconfirmed_receipt_cannot_enable_managed_mode(gateway_client: Any, published_model: dict[str, Any]) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    second = publish(client, info, {**published_model, "expected_revision": 1}).json()
    old = acknowledge(client, headers, first)
    assert old.status_code == 200
    assert not old.json()["activated"] and not old.json()["state"]["managed"]
    assert acknowledge(client, headers, second).json()["activated"]


@pytest.mark.parametrize("change", ["revision", "defaults", "channel_approval", "published", "model_id"])
def test_drift_blocks_confirmation_and_new_admission(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session], change: str) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    assert acknowledge(client, headers, first).json()["activated"]
    pending = publish(client, info, {**published_model, "expected_revision": 1}).json()
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        assert model
        if change == "revision":
            model.revision += 1
        elif change == "model_id":
            model.id = uuid4()
        elif change == "defaults":
            model.defaults = {**model.defaults, "quality": "changed"}
        elif change == "published":
            model.published = False
        else:
            for binding in session.scalars(select(WorkflowBinding)):
                binding.runtime_approval = None
    state = client.get(base_path(info)).json()
    assert state["status"] == "stale" and state["desired_stale"] and state["confirmed_stale"]
    assert acknowledge(client, headers, pending).status_code == 409
    request = {**admission_body(), "catalog": stamp(first)}
    # Channel approvals also gate the actual execution path; reject at admission.
    assert client.post("/gateway/v1/admissions", json=request, headers=headers).status_code in (409, 422)


def test_publication_idempotency_and_mutation_controls(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    token = str(uuid4())
    path = base_path(info) + "/releases"
    assert client.post(path, json=published_model, headers={"Idempotency-Key": token}).status_code == 428
    assert client.post(path, json=published_model, headers={"If-Match": '"0"', "Idempotency-Key": token, "X-CSRF-Token": "invalid"}).status_code == 403
    first = publish(client, info, published_model, token=token)
    assert first.status_code == 201, first.text
    assert publish(client, info, published_model, token=token).json() == first.json()
    assert publish(client, info, {**published_model, "models": []}, token=token).status_code == 409
    assert publish(client, info, published_model).status_code == 409
    changed_instance = {**published_model, "expected_revision": 1, "instance_id": "another-provider"}
    assert publish(client, info, changed_instance).status_code == 409
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayCatalogRelease)) == 1
    assert "configuration_json" not in client.get(path).json()["data"][0]
    assert client.get(path + "/1").json() == first.json()


def test_retry_and_rollback_create_new_validated_versions(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    assert acknowledge(client, headers, first).json()["activated"]
    changed = deepcopy(published_model)
    changed["expected_revision"] = 1
    changed["models"][0]["unit_price"] = "0.10"
    second = publish(client, info, changed).json()
    assert acknowledge(client, headers, second).json()["activated"]
    path = base_path(info) + "/replay"
    body = {"action": "rollback", "expected_revision": 2, "source_revision": 1}
    response = client.post(path, json=body, headers={"If-Match": '"2"', "Idempotency-Key": str(uuid4())})
    assert response.status_code == 201, response.text
    third = response.json()
    assert third["revision"] == 3 and third["source_revision"] == 1 and third["digest"] != first["digest"]
    assert json.loads(third["configuration_json"])["models"][0]["unit_price"] == "0.05000000"
    assert client.get(base_path(info)).json()["confirmed"]["revision"] == 2
    assert acknowledge(client, headers, third).json()["activated"]
    # A metadata-only revision may be refreshed; old release content stays fixed.
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        assert model
        model.revision += 1
    retry_body = {"action": "retry", "expected_revision": 3, "source_revision": 3}
    retry = client.post(path, json=retry_body, headers={"If-Match": '"3"', "Idempotency-Key": str(uuid4())})
    assert retry.status_code == 201, retry.text
    fourth = retry.json()
    assert fourth["revision"] == 4
    assert json.loads(fourth["configuration_json"])["models"][0]["model_bindings"][0]["revision"] == 2
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        assert model
        model.defaults = {**model.defaults, "size": "512x512"}
    invalid = {"action": "rollback", "expected_revision": 4, "source_revision": 1}
    assert client.post(path, json=invalid, headers={"If-Match": '"4"', "Idempotency-Key": str(uuid4())}).status_code == 409


def test_gateway_catalog_credentials_and_receipt_binding(gateway_client: Any, published_model: dict[str, Any]) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    pull = "/gateway/v1/catalog?instance_id=synthetic-provider"
    assert client.get(pull).status_code == 401
    assert client.get(pull, headers=headers).json()["desired"]["digest"] == first["digest"]
    assert client.get("/gateway/v1/catalog?instance_id=another-provider", headers=headers).status_code == 409
    assert acknowledge(client, headers, {**first, "digest": "0" * 64}).status_code == 409
    assert acknowledge(client, headers, {**first, "instance_id": "another-provider"}).status_code == 409
    other = client.post("/admin/api/v1/gateways", json={"name": "Other synthetic gateway", "issuer": "sub2api", "allowed_cidrs": ["127.0.0.1/32"]}, headers={"Idempotency-Key": str(uuid4())}).json()
    other_headers = {"Authorization": "Bearer " + other["secret"]}
    assert client.get(pull, headers=other_headers).json()["status"] == "legacy"
    assert acknowledge(client, other_headers, first).status_code == 404
    assert client.post("/gateway/v1/catalog/receipts", json={**stamp(first), "status": "rejected", "error_code": "https://private.example/secret"}, headers=headers).status_code == 422


def test_concurrent_publications_use_cas_and_preserve_immutable_facts(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session], test_settings: Settings) -> None:
    client, headers, info = gateway_client
    gateway_id = parse_id("gateway", info["service"]["gateway"]["id"])
    with pg() as session:
        user = session.scalar(select(AdminUser))
        assert user
        actor = AdminIdentity(user.id, user.username, user.role, uuid4())
    barrier = Barrier(2)
    repository = GatewayCatalogAdministration(pg, test_settings)
    def publish_competing() -> str:
        barrier.wait(timeout=10)
        try:
            repository.publish(actor, gateway_id, CatalogPublication.model_validate(published_model), str(uuid4()), "synthetic-concurrent-publish")
            return "published"
        except DomainError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: publish_competing(), range(2)))
    assert sorted(results) == ["config_revision_conflict", "published"]
    first = client.get(base_path(info)).json()["desired"]
    assert acknowledge(client, headers, first).json()["activated"]
    with pg() as session:
        assert session.get(GatewayCatalog, gateway_id).confirmed_revision == 1
    with pytest.raises(DBAPIError, match="immutable"), pg.begin() as session:
        session.execute(text("UPDATE gateway_catalog_releases SET action='publish' WHERE gateway_id=:gateway"), {"gateway": gateway_id})
    with pytest.raises(DBAPIError, match="gateway_catalog_state_immutable"), pg.begin() as session:
        session.execute(text("UPDATE gateway_catalogs SET confirmed_revision=NULL WHERE gateway_id=:gateway"), {"gateway": gateway_id})
    with pytest.raises(DBAPIError, match="gateway_catalog_receipt_immutable"), pg.begin() as session:
        session.execute(text("UPDATE gateway_catalog_receipts SET status='rejected',error_code='apply_failed' WHERE gateway_id=:gateway"), {"gateway": gateway_id})
    with pg() as session:
        assert session.get(GatewayCatalog, gateway_id).confirmed_revision == 1


def test_catalog_price_and_scope_are_rechecked_for_new_requests(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    assert acknowledge(client, headers, first).json()["activated"]
    wrong_price = {**admission_body(), "catalog": stamp(first), "price_version": "unapproved-price"}
    assert client.post("/gateway/v1/admissions", json=wrong_price, headers=headers).status_code == 409
    assert client.post("/gateway/v1/admissions", json={**admission_body(), "catalog": {**stamp(first), "digest": "0" * 64}}, headers=headers).status_code == 409
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayAdmission)) == 0


def test_activation_races_legacy_admission_without_lost_fences(gateway_client: Any, published_model: dict[str, Any]) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    requests = [admission_body() for _ in range(3)]
    barrier = Barrier(4)
    def competing(index: int) -> Any:
        barrier.wait(timeout=10)
        return acknowledge(client, headers, first) if index == 3 else client.post("/gateway/v1/admissions", json=requests[index], headers=headers)
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(competing, range(4)))
    assert responses[3].status_code == 200 and responses[3].json()["activated"]
    for request, response in zip(requests, responses[:3], strict=True):
        assert response.status_code in (202, 409), response.text
        if response.status_code == 202:
            replay = client.post("/gateway/v1/admissions", json=request, headers=headers)
            assert replay.status_code == 202 and replay.json()["id"] == response.json()["id"]
    assert client.post("/gateway/v1/admissions", json=admission_body(), headers=headers).status_code == 409


def test_catalog_migration_refuses_fact_loss_and_admission_stamp_is_immutable(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    first = publish(client, info, published_model).json()
    assert acknowledge(client, headers, first).json()["activated"]
    accepted = client.post("/gateway/v1/admissions", json={**admission_body(), "catalog": stamp(first)}, headers=headers)
    assert accepted.status_code == 202
    with pytest.raises(DBAPIError, match="gateway_admission_catalog_immutable"), pg.begin() as session:
        session.execute(text("UPDATE gateway_admissions SET catalog_revision=NULL,catalog_digest=NULL,catalog_instance_id=NULL WHERE id=:identifier"), {"identifier": parse_id("adm", accepted.json()["id"])})
    migration_path = Path(__file__).resolve().parents[2] / "migrations/versions/1b8e0c5f3247_gateway_catalog_publication.py"
    specification = spec_from_file_location("catalog_publication_migration", migration_path)
    assert specification and specification.loader
    module = module_from_spec(specification)
    specification.loader.exec_module(module)
    with pytest.raises(RuntimeError, match="facts exist"), pg.begin() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            module.downgrade()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(GatewayCatalogRelease)) == 1
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "1b8e0c5f3247"


def test_publication_needs_an_enabled_channel_and_confirmation_rechecks_it(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    with pg.begin() as session:
        for channel in session.scalars(select(Channel)):
            channel.enabled = False
    assert publish(client, info, published_model).status_code == 409
    with pg.begin() as session:
        channel = session.scalar(select(Channel).order_by(Channel.id))
        assert channel
        channel.enabled = True
    response = publish(client, info, published_model)
    assert response.status_code == 201, response.text
    first = response.json()
    with pg.begin() as session:
        for channel in session.scalars(select(Channel)):
            channel.draining = True
    assert acknowledge(client, headers, first).status_code == 409
    with pg.begin() as session:
        channel = session.scalar(select(Channel).order_by(Channel.id))
        assert channel
        channel.draining = False
    assert acknowledge(client, headers, first).json()["activated"]


def test_maximum_catalog_revision_can_confirm_and_cannot_wrap(gateway_client: Any, published_model: dict[str, Any], pg: sessionmaker[Session]) -> None:
    client, headers, info = gateway_client
    template = publish(client, info, published_model).json()
    other = client.post("/admin/api/v1/gateways", json={"name": "Synthetic maximum revision", "issuer": "sub2api", "allowed_cidrs": ["127.0.0.1/32"]}, headers={"Idempotency-Key": str(uuid4())}).json()
    gateway_id = parse_id("gateway", other["gateway"]["id"])
    configuration = json.loads(template["configuration_json"])
    configuration.update(gateway_id=other["gateway"]["id"], revision=2147483647)
    payload = canonical_json(configuration)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with pg.begin() as session:
        actor_id = session.scalar(select(AdminUser.id))
        session.add(GatewayCatalogRelease(gateway_id=gateway_id, revision=2147483647, instance_id=template["instance_id"], digest=digest, configuration_json=payload, action="publish", actor_id=actor_id))
        session.add(GatewayCatalog(gateway_id=gateway_id, revision=2147483647, instance_id=template["instance_id"]))
    release = {**template, "revision": 2147483647, "digest": digest}
    result = acknowledge(client, {"Authorization": "Bearer " + other["secret"]}, release)
    assert result.status_code == 200, result.text
    assert result.json()["activated"]
    retry = client.post("/admin/api/v1/gateways/" + other["gateway"]["id"] + "/catalog/replay", json={"action": "retry", "expected_revision": 2147483647, "source_revision": 2147483647}, headers={"If-Match": '"2147483647"', "Idempotency-Key": str(uuid4())})
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "catalog_revision_exhausted"
