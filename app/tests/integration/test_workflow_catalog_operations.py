from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event, local
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.workflow_catalogs import WorkflowCatalogRequest
from generation_contracts.workflow_imports import WorkflowImportPreview, WorkflowPull
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.admin_workflow_catalogs import WorkflowCatalogAdministration
from generation_infrastructure.admin_workflow_imports import WorkflowImportAdministration
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import AdminUser, Channel, Gpu, WorkerCommand
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker
from windows_worker.workflow_catalogs import list_exports

pytestmark = pytest.mark.integration


@pytest.fixture
def catalog_actor(pg: sessionmaker[Session]) -> AdminIdentity:
    with pg.begin() as session:
        user = AdminUser(username="catalog-owner", password_hash=password_hash("Synthetic-catalog-password"), role="workflow_maintainer")
        session.add(user)
        session.flush()
        return AdminIdentity(user.id, user.username, user.role, uuid4())


def observation(catalog: dict[str, Any], status: str | None = "ready", age: int = 0) -> dict[str, Any]:
    value = {"channel_id": public_id("ch", catalog["channel_ids"][0]), "revision": 1, "observed_ago_ms": age, "observation": {"reachable": False, "error_code": "unreachable"}}
    if status is not None:
        value["workflow_export"] = {"protocol": "local-directory-v1", "status": status}
    return value


def observe(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings, status: str | None = "ready", age: int = 0) -> None:
    LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value())).heartbeat(catalog["node_id"], catalog["boot_id"], [observation(catalog, status, age)])


def request(catalog: dict[str, Any]) -> WorkflowCatalogRequest:
    return WorkflowCatalogRequest(channel_id=public_id("ch", catalog["channel_ids"][0]), expected_revision=1)


def test_export_observations_are_independent_of_queue_and_require_current_boot_revision_and_freshness(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    service = WorkflowCatalogAdministration(pg, test_settings)
    identifier = catalog["channel_ids"][0]
    assert service.source_status(identifier)["status"] == "unknown"
    observe(pg, catalog, test_settings)
    assert service.source_status(identifier)["status"] == "ready"
    observe(pg, catalog, test_settings, "not_configured", age=10000)
    assert service.source_status(identifier)["status"] == "ready"  # Older observation cannot regress it.
    observe(pg, catalog, test_settings, "not_configured")
    state = service.source_status(identifier)
    assert state["status"] == "not_configured" and state["catalog_supported"]
    observe(pg, catalog, test_settings, None)
    assert service.source_status(identifier)["status"] == "unknown"
    observe(pg, catalog, test_settings)
    with pg.begin() as session:
        channel = session.get(Channel, identifier)
        channel.observation = {**channel.observation, "workflow_export_observed_at": (utcnow() - timedelta(seconds=16)).timestamp()}
    assert service.source_status(identifier)["status"] == "unknown"
    observe(pg, catalog, test_settings)
    new_boot = uuid4()
    LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value())).register(catalog["node_id"], new_boot, {"gpus": []})
    assert service.source_status(identifier)["status"] == "unknown"
    catalog["boot_id"] = new_boot
    observe(pg, catalog, test_settings, age=15000)
    assert service.source_status(identifier)["status"] == "unknown"
    observe(pg, catalog, test_settings)
    assert service.source_status(identifier)["status"] == "ready"
    with pg.begin() as session:
        session.get(Channel, identifier).desired_revision = 2
    assert service.source_status(identifier)["status"] == "unknown"


def test_catalog_operation_is_idempotent_actor_owned_and_cannot_be_used_as_source(pg: sessionmaker[Session], catalog: dict[str, Any], catalog_actor: AdminIdentity, test_settings: Settings, tmp_path: Path) -> None:
    service = WorkflowCatalogAdministration(pg, test_settings)
    with pytest.raises(DomainError) as missing:
        service.browse(catalog_actor, request(catalog), "missing-capability", "test")
    assert missing.value.code == "workflow_export_unavailable"
    observe(pg, catalog, test_settings)
    with ThreadPoolExecutor(6) as pool:
        operations = list(pool.map(lambda _: service.browse(catalog_actor, request(catalog), "one-catalog", "test"), range(6)))
    assert len({x["operation_id"] for x in operations}) == 1
    operation_id = parse_id("op", operations[0]["operation_id"])
    with pytest.raises(DomainError) as pending:
        WorkflowImportAdministration(pg, test_settings).pull(catalog_actor, WorkflowPull(channel_id=request(catalog).channel_id, expected_revision=1, relative_path="sample.json"), "file-while-catalog", "test")
    assert pending.value.code == "workflow_pull_pending"
    repository = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    command = repository.claim(catalog["node_id"], catalog["boot_id"])
    assert command and not command["requires_drain"] and command["payload"]["mode"] == "catalog"
    assert service.detail(catalog_actor, operation_id)["status"] == "running"
    (tmp_path / "sample.json").write_text('{"1":{"class_type":"Synthetic","inputs":{"text":"private"}}}', encoding="utf-8")
    result = {"success": True, "applied_revision": 1, **list_exports(tmp_path, "", None)}
    command_id = parse_id("cmd", command["command_id"])
    with pytest.raises(DomainError):
        repository.receipt(catalog["node_id"], command_id, catalog["boot_id"], command["command_digest"], {**result, "catalog_digest": "0" * 64})
    for _ in range(2):
        repository.receipt(catalog["node_id"], command_id, catalog["boot_id"], command["command_digest"], result)
    detail = service.detail(catalog_actor, operation_id)
    assert detail["catalog"]["entries"][0]["name"] == "sample.json" and detail["status"] == "succeeded"
    generic = AdminQueries(pg, test_settings).operation(operation_id)
    assert "catalog" not in generic["targets"][0]["receipt"]
    with pytest.raises(DomainError) as source:
        WorkflowImportAdministration(pg, test_settings).preview(catalog_actor, WorkflowImportPreview(source_operation_id=operations[0]["operation_id"], source_digest="a" * 64, target={"mode": "create", "name": "Invalid source", "task_type": "text_to_image"}), "catalog-not-source", "test")
    assert source.value.code == "workflow_source_unavailable"
    with pg.begin() as session:
        user = AdminUser(username="other-catalog-admin", password_hash="!", role="administrator")
        session.add(user)
        session.flush()
        other = AdminIdentity(user.id, user.username, user.role, uuid4())
    with pytest.raises(DomainError) as denied:
        service.detail(other, operation_id)
    assert denied.value.status == 404
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is None
        assert not session.get(Channel, catalog["channel_ids"][0]).draining


def test_catalog_pending_command_cannot_cross_worker_boot(pg: sessionmaker[Session], catalog: dict[str, Any], catalog_actor: AdminIdentity, test_settings: Settings) -> None:
    observe(pg, catalog, test_settings)
    service = WorkflowCatalogAdministration(pg, test_settings)
    operation = service.browse(catalog_actor, request(catalog), "prior-boot", "test")
    vault = SecretVault(test_settings.encryption_key.get_secret_value())
    new_boot = uuid4()
    LeaseRepository(pg, vault).register(catalog["node_id"], new_boot, {"gpus": []})
    assert CommandRepository(pg, vault).claim(catalog["node_id"], new_boot) is None
    result = service.detail(catalog_actor, parse_id("op", operation["operation_id"]))
    assert result["status"] == "failed" and result["error_code"] == "superseded_boot" and result["catalog"] is None


def test_selected_file_digest_is_verified_by_cloud_before_persisting(pg: sessionmaker[Session], catalog: dict[str, Any], catalog_actor: AdminIdentity, test_settings: Settings) -> None:
    service = WorkflowImportAdministration(pg, test_settings)
    service.pull(catalog_actor, WorkflowPull(channel_id=request(catalog).channel_id, expected_revision=1, relative_path="sample.json", expected_source_digest="a" * 64), "selected-file", "test")
    repository = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    command = repository.claim(catalog["node_id"], catalog["boot_id"])
    assert command
    graph = {"1": {"class_type": "Changed", "inputs": {}}}
    result = {"success": True, "applied_revision": 1, "source_document": graph, "source_digest": fingerprint(graph), "export_format": "comfy-api", "source_bytes": 60}
    with pytest.raises(DomainError) as changed:
        repository.receipt(catalog["node_id"], parse_id("cmd", command["command_id"]), catalog["boot_id"], command["command_digest"], result)
    assert changed.value.code == "workflow_export_changed"
    with pg() as session:
        assert session.get(WorkerCommand, parse_id("cmd", command["command_id"])).receipt is None


def test_catalog_http_permissions_csrf_and_input_validation(pg: sessionmaker[Session], catalog: dict[str, Any], catalog_actor: AdminIdentity, test_settings: Settings) -> None:
    observe(pg, catalog, test_settings)
    status_url = "/admin/api/v1/channels/" + request(catalog).channel_id + "/workflow-exports"
    body = request(catalog).model_dump()
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get(status_url).status_code == 401
        assert client.post("/admin/api/v1/workflow-catalogs", json=body).status_code == 401
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": catalog_actor.username, "password": "Synthetic-catalog-password"})
        assert login.status_code == 200
        assert client.get(status_url).json()["status"] == "ready"
        assert client.post("/admin/api/v1/workflow-catalogs", json=body).status_code == 403
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.post("/admin/api/v1/workflow-catalogs", json=body).status_code == 400
        client.headers["Idempotency-Key"] = "http-catalog-idempotency"
        assert client.post("/admin/api/v1/workflow-catalogs", json={**body, "relative_directory": "../escape"}).status_code == 422
        accepted = client.post("/admin/api/v1/workflow-catalogs", json=body)
        assert accepted.status_code == 202
        assert client.get("/admin/api/v1/workflow-catalogs/" + accepted.json()["operation_id"]).json()["status"] == "pending"
        with pg.begin() as session:
            session.get(AdminUser, catalog_actor.user_id).role = "observer"
        assert client.get(status_url).status_code == 403
        assert client.get("/admin/api/v1/workflow-catalogs/" + accepted.json()["operation_id"]).status_code == 403
        assert client.post("/admin/api/v1/workflow-catalogs", json=body).status_code == 403


@pytest.mark.parametrize("mode", ["catalog", "file"])
def test_workflow_reads_and_heartbeat_follow_node_then_channel_lock_order(pg: sessionmaker[Session], catalog: dict[str, Any], catalog_actor: AdminIdentity, test_settings: Settings, mode: str) -> None:
    observe(pg, catalog, test_settings)
    node_locked, read_started = Event(), Event()
    role = local()
    engine = pg.kw["bind"]

    def before(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        sql = statement.upper()
        if getattr(role, "value", None) == "read" and "FROM NODES" in sql and "FOR SHARE" in sql:
            read_started.set()

    def after(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        sql = statement.upper()
        if getattr(role, "value", None) == "heartbeat" and "FROM NODES" in sql and "FOR UPDATE" in sql:
            node_locked.set()
            assert read_started.wait(5), "Read did not reach its first resource lock"
        elif getattr(role, "value", None) == "read" and "FROM CHANNELS" in sql and "FOR UPDATE" in sql:
            read_started.set()

    def heartbeat() -> None:
        role.value = "heartbeat"
        observe(pg, catalog, test_settings)

    def read() -> dict[str, Any]:
        role.value = "read"
        assert node_locked.wait(5)
        if mode == "catalog":
            return WorkflowCatalogAdministration(pg, test_settings).browse(catalog_actor, request(catalog), "concurrent-catalog-heartbeat", "test")
        return WorkflowImportAdministration(pg, test_settings).pull(catalog_actor, WorkflowPull(channel_id=request(catalog).channel_id, expected_revision=1, relative_path="sample.json"), "concurrent-file-heartbeat", "test")

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(2) as pool:
            heartbeat_result, read_result = pool.submit(heartbeat), pool.submit(read)
            heartbeat_result.result(timeout=12)
            assert read_result.result(timeout=12)["status"] == "pending"
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
