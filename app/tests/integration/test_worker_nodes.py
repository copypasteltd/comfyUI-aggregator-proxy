from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.admin import ChannelCreate
from generation_contracts.worker_nodes import WorkerNodeAction, WorkerNodeCreate, WorkerNodePatch
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.admin_channels import ChannelAdministration
from generation_infrastructure.admin_nodes import NodeAdministration
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminMutation,
    AdminUser,
    Attempt,
    AuditEvent,
    Channel,
    Gpu,
    GpuOccupancy,
    ManagementOperation,
    Node,
    NodeBoot,
    WorkerCommand,
    WorkflowBinding,
)
from generation_infrastructure.security import SecretVault, password_hash, secret_identifier
from generation_infrastructure.settings import Settings
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-worker-node-test-password"


def administrator(pg: sessionmaker[Session], role: str = "administrator") -> AdminIdentity:
    with pg.begin() as session:
        user = AdminUser(username="node-" + role, password_hash=password_hash(PASSWORD), role=role)
        session.add(user)
        session.flush()
        return AdminIdentity(user.id, user.username, user.role, uuid4())


def action(admin: NodeAdministration, actor: AdminIdentity, identifier: UUID, kind: str, revision: int = 1, token: str | None = None) -> dict[str, Any]:
    return admin.action(actor, identifier, WorkerNodeAction.model_validate({"action": kind, "expected_revision": revision}), token or str(uuid4()), "node-test")


def leases(pg: sessionmaker[Session], settings: Settings) -> LeaseRepository:
    return LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)


@pytest.mark.parametrize("header", ["mismatched", "malformed", "matching", "omitted"])
def test_expected_node_header_prevents_wrong_credential_pair_from_mutating_boot(pg: sessionmaker[Session], test_settings: Settings, header: str) -> None:
    admin = NodeAdministration(pg, test_settings)
    created = admin.create_node(administrator(pg), WorkerNodeCreate(name="Synthetic identity-pair check"), str(uuid4()), "pair-test")
    identifier = parse_id("node", created["node"]["id"])
    with pg() as session:
        before_audit = session.scalar(select(func.count()).select_from(AuditEvent))
    headers = {"Authorization": "Bearer " + created["secret"]}
    if header != "omitted":
        headers["X-Worker-Node-Id"] = created["node"]["id"] if header == "matching" else "malformed" if header == "malformed" else "node_" + uuid4().hex
    boot = str(uuid4())
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        response = client.post("/internal/v1/register", headers=headers, json={"protocol_version": "0.4.0", "boot_id": boot, "inventory": {"gpus": [{"hardware_id": "GPU-SYNTHETIC", "name": "Synthetic"}]}})
    allowed = header in ("matching", "omitted")
    assert response.status_code == (200 if allowed else 422 if header == "malformed" else 401)
    if allowed:
        assert response.json()["node_id"] == created["node"]["id"] and response.json()["boot_id"] == boot
    else:
        assert created["secret"] not in response.text
    with pg() as session:
        node = session.get(Node, identifier)
        assert node is not None
        assert node.current_boot_id == (UUID(boot) if allowed else None)
        assert session.scalar(select(func.count()).select_from(NodeBoot)) == int(allowed)
        assert session.scalar(select(func.count()).select_from(Gpu)) == int(allowed)
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == before_audit + int(allowed)
        if not allowed:
            assert node.inventory == {}


def test_concurrent_create_replay_and_no_persisted_secret(pg: sessionmaker[Session], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    data = WorkerNodeCreate(name="  Windows % _ 节点  ")
    with ThreadPoolExecutor(8) as executor:
        results = list(executor.map(lambda _: admin.create_node(actor, data, "same-node-creation", "node-test"), range(8)))
    assert len({r["node"]["id"] for r in results}) == 1
    assert sum(r["secret_available"] for r in results) == 1
    first = next(r for r in results if r["secret_available"])
    identifier = parse_id("node", first["node"]["id"])
    assert first["node"]["name"] == "Windows % _ 节点" and first["node"]["gpu_count"] == 0
    assert not first["node"]["boot_registered"] and first["node"]["observed_at"] is None
    assert AuthRepository(pg, test_settings).worker(first["secret"]) == identifier
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Node)) == 1
        node = session.get(Node, identifier)
        assert node is not None and node.credential_verifier != first["secret"]
        persisted = json.dumps([r.result for r in session.scalars(select(AdminMutation))] + [r.detail for r in session.scalars(select(AuditEvent))])
        assert first["secret"] not in persisted
        assert "credential_verifier" not in persisted and "inventory" not in persisted
    replay = admin.create_node(actor, data, "same-node-creation", "node-test")
    assert not replay["secret_available"] and "secret" not in replay
    with pytest.raises(DomainError) as error:
        admin.create_node(actor, WorkerNodeCreate(name="different"), "same-node-creation", "node-test")
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize("guard", ["open_attempt", "occupancy", "uncertain", "management_hold", "running_command"])
@pytest.mark.parametrize("kind", ["disable", "rotate_credential", "archive"])
def test_lifecycle_rejects_every_nonidle_fact(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, guard: str, kind: str) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    claim = None
    if guard in ("open_attempt", "occupancy", "uncertain"):
        JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "node idle guard"}}, str(uuid4()), "node-test")
        claim = leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
        assert claim
    with pg.begin() as session:
        if guard == "open_attempt":
            session.delete(session.get(GpuOccupancy, catalog["gpu_id"]))
        elif guard in ("occupancy", "uncertain"):
            attempt = session.get(Attempt, parse_id("att", claim["attempt_id"]))
            attempt.closed_at = utcnow()
            occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
            occupancy.uncertain = guard == "uncertain"
        elif guard == "management_hold":
            session.get(Gpu, catalog["gpu_id"]).management_hold = uuid4()
        else:
            operation = ManagementOperation(actor_id=actor.user_id, command="probe_channel", state="running")
            session.add(operation)
            session.flush()
            session.add(WorkerCommand(operation_id=operation.id, node_id=catalog["node_id"], channel_id=catalog["channel_ids"][0], command="probe_channel", payload={}, state="running", boot_id=catalog["boot_id"], lease_expires_at=utcnow() - timedelta(hours=1)))
    before = admin.get_node(catalog["node_id"])
    assert not before["can_disable"] and not before["can_rotate"] and not before["can_archive"]
    with pytest.raises(DomainError) as error:
        action(admin, actor, catalog["node_id"], kind)
    assert error.value.code == "node_not_idle"
    after = admin.get_node(catalog["node_id"])
    assert after == before
    # Pure metadata changes remain possible while execution is unresolved.
    renamed = admin.patch_node(actor, catalog["node_id"], WorkerNodePatch(name="Renamed while busy", expected_revision=1), "rename-busy-node", "node-test")
    assert renamed["node"]["revision"] == 2 and renamed["node"]["activity"] == before["activity"]


def test_rotation_and_disable_require_new_boot_keep_gpu_and_invalidate_bindings(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    repository = leases(pg, test_settings)
    commands = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    with pg.begin() as session:
        gpu = session.get(Gpu, catalog["gpu_id"])
        inventory = {"gpus": [{"hardware_id": gpu.hardware_id, "name": gpu.name, "memory_bytes": gpu.memory_bytes}]}
    repository.register(catalog["node_id"], catalog["boot_id"], inventory)
    assert admin.get_node(catalog["node_id"])["revision"] == 1
    rotated = action(admin, actor, catalog["node_id"], "rotate_credential", token="rotate-once-token")
    assert rotated["secret_available"] and not rotated["node"]["boot_registered"]
    assert AuthRepository(pg, test_settings).worker(rotated["secret"]) == catalog["node_id"]
    replay = action(admin, actor, catalog["node_id"], "rotate_credential", token="rotate-once-token")
    assert not replay["secret_available"] and "secret" not in replay
    for call in (lambda: repository.register(catalog["node_id"], catalog["boot_id"], inventory), lambda: repository.heartbeat(catalog["node_id"], catalog["boot_id"], []), lambda: repository.claim(catalog["node_id"], catalog["boot_id"]), lambda: commands.claim(catalog["node_id"], catalog["boot_id"])):
        with pytest.raises(DomainError) as error:
            call()
        assert error.value.code == "stale_boot"
    new_boot = uuid4()
    repository.register(catalog["node_id"], new_boot, inventory)
    repository.heartbeat(catalog["node_id"], new_boot, [])
    with pg() as session:
        assert list(session.scalars(select(Gpu.id))) == [catalog["gpu_id"]]
        assert all(c.verified_revision is None for c in session.scalars(select(Channel)))
        assert all(b.verified_digest is None for b in session.scalars(select(WorkflowBinding)))
        assert session.scalar(select(func.count()).select_from(NodeBoot)) == 2
        assert session.get(Node, catalog["node_id"]).revision == 2
    disabled = action(admin, actor, catalog["node_id"], "disable", 2)
    assert not disabled["node"]["enabled"] and disabled["node"]["observed_at"] is None
    for call in (lambda: AuthRepository(pg, test_settings).worker(rotated["secret"]), lambda: repository.register(catalog["node_id"], uuid4(), inventory), lambda: repository.heartbeat(catalog["node_id"], new_boot, []), lambda: repository.claim(catalog["node_id"], new_boot), lambda: commands.claim(catalog["node_id"], new_boot)):
        with pytest.raises(DomainError):
            call()
    action(admin, actor, catalog["node_id"], "enable", 3)
    with pytest.raises(DomainError):
        repository.register(catalog["node_id"], new_boot, inventory)
    repository.register(catalog["node_id"], uuid4(), inventory)
    assert admin.get_node(catalog["node_id"])["revision"] == 4


def test_rotate_revokes_previous_credential_and_does_not_persist_plaintext(pg: sessionmaker[Session], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    first = admin.create_node(actor, WorkerNodeCreate(name="Credential lifecycle"), "first-enrollment-token", "node-test")
    identifier = parse_id("node", first["node"]["id"])
    rotated = action(admin, actor, identifier, "rotate_credential")
    with pytest.raises(DomainError):
        AuthRepository(pg, test_settings).worker(first["secret"])
    assert AuthRepository(pg, test_settings).worker(rotated["secret"]) == identifier
    with pg() as session:
        assert rotated["secret"] not in json.dumps([r.result for r in session.scalars(select(AdminMutation))])


def test_previously_authenticated_registration_cannot_cross_credential_rotation(pg: sessionmaker[Session], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    first = admin.create_node(actor, WorkerNodeCreate(name="In-flight registration"), "in-flight-registration", "node-test")
    auth = AuthRepository(pg, test_settings)
    identifier = auth.worker(first["secret"])
    old_identifier = secret_identifier("worker", first["secret"])
    rotated = action(admin, actor, identifier, "rotate_credential")
    repository = leases(pg, test_settings)
    with pytest.raises(DomainError) as error:
        repository.register(identifier, uuid4(), {"gpus": []}, credential_identifier=old_identifier)
    assert error.value.code == "invalid_credentials"
    assert not admin.get_node(identifier)["boot_registered"]
    repository.register(identifier, uuid4(), {"gpus": []}, credential_identifier=secret_identifier("worker", rotated["secret"]))
    assert admin.get_node(identifier)["boot_registered"]


def test_lifecycle_large_historical_inventory_has_bounded_refusal(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    with pg.begin() as session:
        for i in range(100):
            session.add(Gpu(node_id=catalog["node_id"], name="Synthetic old GPU", hardware_id="historical-" + str(i)))
    detail = admin.get_node(catalog["node_id"])
    assert len(detail["gpus"]) == 100 and detail["gpus_truncated"]
    with pytest.raises(DomainError) as error:
        action(admin, actor, catalog["node_id"], "disable")
    assert error.value.code == "node_inventory_limit"
    assert admin.get_node(catalog["node_id"])["revision"] == 1


def test_archive_is_logical_terminal_and_blocks_new_channel(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    with pytest.raises(DomainError) as error:
        action(admin, actor, catalog["node_id"], "archive")
    assert error.value.code == "node_still_referenced"
    action(admin, actor, catalog["node_id"], "disable")
    with pytest.raises(DomainError) as error:
        action(admin, actor, catalog["node_id"], "archive", 2)
    assert error.value.code == "node_still_referenced"
    with pg.begin() as session:
        for channel in session.scalars(select(Channel)):
            channel.deleted_at = utcnow()
    archived = action(admin, actor, catalog["node_id"], "archive", 2)
    assert archived["node"]["archived_at"] and not archived["node"]["enabled"]
    assert ChannelAdministration(pg, test_settings).list_nodes()["data"] == []
    for kind in ("enable", "disable", "rotate_credential", "archive"):
        with pytest.raises(DomainError) as error:
            action(admin, actor, catalog["node_id"], kind, 3)
        assert error.value.code == "node_archived"
    with pytest.raises(DomainError):
        admin.patch_node(actor, catalog["node_id"], WorkerNodePatch(name="revive", expected_revision=3), "rename-archived-node", "node-test")
    identity = catalog["identity"]
    data = ChannelCreate(tenant_id=public_id("ten", identity.tenant_id), project_id=public_id("prj", identity.project_id), node_id=public_id("node", catalog["node_id"]), gpu_id=public_id("gpu", catalog["gpu_id"]), name="Invalid archived node reference", base_url="http://127.0.0.1:18188")
    with pytest.raises(DomainError) as error:
        ChannelAdministration(pg, test_settings).create_channel(actor, data, "archived-channel-create", "node-test")
    assert error.value.code == "node_unavailable"
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]) is not None
        assert session.scalar(select(func.count()).select_from(Channel)) == 2


@pytest.mark.parametrize("resource", ["node", "gpu"])
def test_lifecycle_nowait_locks_rollback_without_mutation(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, resource: str) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    before = admin.get_node(catalog["node_id"])
    with pg.begin() as locked:
        # A concurrent claim holds Node SHARE; another claim may hold GPU UPDATE.
        if resource == "node":
            locked.scalar(select(Node).where(Node.id == catalog["node_id"]).with_for_update(read=True))
        else:
            locked.scalar(select(Gpu).where(Gpu.id == catalog["gpu_id"]).with_for_update())
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(action, admin, actor, catalog["node_id"], "disable")
            with pytest.raises(DomainError) as error:
                future.result(timeout=3)
        assert error.value.code == "node_busy"
    assert admin.get_node(catalog["node_id"]) == before
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AdminMutation)) == 0
    assert not action(admin, actor, catalog["node_id"], "disable")["node"]["enabled"]


def test_enable_preserves_uncertain_occupancy_and_pending_commands(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "uncertain"}}, str(uuid4()), "node-test")
    claim = leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    with pg.begin() as session:
        session.get(Node, catalog["node_id"]).enabled = False
        session.get(GpuOccupancy, catalog["gpu_id"]).uncertain = True
        operation = ManagementOperation(actor_id=actor.user_id, command="probe_channel")
        session.add(operation)
        session.flush()
        session.add(WorkerCommand(operation_id=operation.id, node_id=catalog["node_id"], channel_id=catalog["channel_ids"][0], command="probe_channel", payload={}))
    result = action(admin, actor, catalog["node_id"], "enable")
    assert result["node"]["activity"]["uncertain_gpus"] == 1
    with pg() as session:
        assert session.get(Attempt, parse_id("att", claim["attempt_id"])).closed_at is None
        assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain
        assert session.scalar(select(WorkerCommand.state)) == "pending"


def test_page_search_literal_wildcards_filter_and_safe_projection(pg: sessionmaker[Session], test_settings: Settings) -> None:
    actor = administrator(pg)
    admin = NodeAdministration(pg, test_settings)
    created = admin.create_node(actor, WorkerNodeCreate(name="Windows % _ \\ literal"), "literal-node-name", "node-test")
    with pg.begin() as session:
        for i in range(54):
            session.add(Node(name="Node " + str(i), credential_identifier=uuid4().hex, credential_verifier="0" * 64, enabled=i != 0))
    page = admin.list_nodes()
    assert page["total"] == 55 and len(page["data"]) == 50 and page["has_more"]
    second = admin.list_nodes(2)
    assert len(second["data"]) == 5 and not second["has_more"]
    for q in ("%", "_", "\\", created["node"]["id"]):
        result = admin.list_nodes(q=q)
        assert result["total"] == 1 and result["data"][0]["id"] == created["node"]["id"]
    assert admin.list_nodes(state="disabled")["total"] == 1
    assert admin.list_nodes(state="enabled")["total"] == 54
    assert admin.list_nodes(state="archived")["total"] == 0
    detail = admin.get_node(parse_id("node", created["node"]["id"]))
    assert detail["gpus"] == [] and detail["channels"] == []
    assert not {"inventory", "credential_identifier", "credential_verifier", "pepper_version"} & detail.keys()
    with pg() as session:
        assert session.scalar(text("SELECT count(*) FROM nodes WHERE revision <> 1")) == 0


def test_http_permissions_csrf_idempotency_revision_and_worker_identity(pg: sessionmaker[Session], test_settings: Settings) -> None:
    administrator(pg)
    administrator(pg, "observer")
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        path = "/admin/api/v1/worker-nodes"
        assert client.get(path).status_code == 401
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": "node-administrator", "password": PASSWORD}).json()
        assert client.post(path, json={"name": "node"}).status_code == 403
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        assert client.post(path, json={"name": "node"}).status_code == 400
        headers = {"Idempotency-Key": "http-node-registration"}
        created = client.post(path, json={"name": "HTTP Windows"}, headers=headers)
        assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
        data = created.json()
        assert data["secret_available"] and data["secret"].startswith("worker_")
        replay = client.post(path, json={"name": "HTTP Windows"}, headers=headers).json()
        assert not replay["secret_available"] and replay["secret"] is None
        resource = path + "/" + data["node"]["id"]
        body = {"action": "disable", "expected_revision": 1}
        headers = {"Idempotency-Key": "http-node-disable"}
        assert client.post(resource + "/actions", json=body, headers=headers).status_code == 428
        headers["If-Match"] = '"1"'
        disabled = client.post(resource + "/actions", json=body, headers=headers)
        assert disabled.status_code == 200 and disabled.headers["etag"] == '"2"'
        assert client.post(resource + "/actions", json=body, headers=headers).json() == disabled.json()
        assert client.post(resource + "/actions", json=body, headers={**headers, "Idempotency-Key": "stale-node-revision"}).status_code == 409
        assert client.get(path, params={"page": 0}).status_code == 422
        assert client.get(path, params={"state": "arbitrary"}).status_code == 422
        login = client.post("/admin/api/v1/session", json={"username": "node-observer", "password": PASSWORD}).json()
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        assert client.get(path).status_code == 200
        assert client.get(resource).status_code == 200
        assert client.post(path, json={"name": "forbidden"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 403
        assert client.post(resource + "/actions", json={"action": "enable", "expected_revision": 2}, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"2"'}).status_code == 403
        client.cookies.clear()
        assert client.get(path, headers={"Authorization": "Bearer " + data["secret"]}).status_code == 401


@pytest.mark.parametrize("name", ["", "   ", "node\n", "\tname", "a\x00b", "a\x7fb", "n" * 129])
def test_node_name_rejects_empty_controls_and_excess_length(name: str) -> None:
    with pytest.raises(ValidationError):
        WorkerNodeCreate(name=name)
