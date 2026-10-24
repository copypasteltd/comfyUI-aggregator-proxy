from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import COOKIE, create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.model_files import ModelFileReadRequest, ModelFileRegistration
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.admin_channels import enqueue_command
from generation_infrastructure.admin_model_files import ModelFileAdministration
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.models import (
    AdminUser,
    Channel,
    Gpu,
    GpuOccupancy,
    ModelArtifact,
    Node,
    WorkerCommand,
)
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from windows_worker.model_files import discover_models, inspect_model

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-model-file-test-password"


@pytest.fixture
def model_actor(pg: sessionmaker[Session]) -> AdminIdentity:
    with pg.begin() as session:
        user = AdminUser(
            username="model-owner",
            password_hash=password_hash(PASSWORD),
            role="workflow_maintainer",
        )
        session.add(user)
        session.flush()
        return AdminIdentity(user.id, user.username, user.role, uuid4())


@pytest.fixture
def source(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "models/checkpoints/synthetic.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"Synthetic opaque bytes, not a real model.\n" * 30)
    directory = discover_models(tmp_path)
    with pg.begin() as session:
        for identifier in catalog["channel_ids"]:
            channel = session.get(Channel, identifier)
            channel.observation = {
                "inventory": {},
                "inventory_observed_at": int(utcnow().timestamp()),
                "model_catalog": {
                    "boot_id": str(catalog["boot_id"]),
                    "revision": 1,
                    "observed_at": int(utcnow().timestamp()),
                    "catalog": directory,
                },
            }
    return {"root": tmp_path, "catalog": directory, "entry": directory["entries"][0]}


def request(
    catalog: dict[str, Any], source: dict[str, Any], index: int = 0
) -> ModelFileReadRequest:
    return ModelFileReadRequest(
        channel_id=public_id("ch", catalog["channel_ids"][index]),
        expected_revision=1,
        catalog_digest=source["catalog"]["catalog_digest"],
        relative_path=source["entry"]["relative_path"],
    )


def repository(pg: sessionmaker[Session], settings: Settings) -> CommandRepository:
    return CommandRepository(pg, SecretVault(settings.encryption_key.get_secret_value()))


def finish(
    pg: sessionmaker[Session], settings: Settings, catalog: dict[str, Any], source: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    repo = repository(pg, settings)
    claimed = repo.claim(catalog["node_id"], catalog["boot_id"])
    assert claimed and claimed["type"] == "inspect_model_file" and not claimed["requires_drain"]
    result = {
        "success": True,
        "applied_revision": 1,
        "inspection": inspect_model(
            source["root"], claimed["payload"], maximum_bytes=10**6, seconds=10
        ),
    }
    repo.receipt(
        catalog["node_id"],
        parse_id("cmd", claimed["command_id"]),
        catalog["boot_id"],
        claimed["command_digest"],
        result,
    )
    return claimed, result


def test_scan_receipt_to_hash_to_registration_is_idempotent_and_preserves_gpu(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
) -> None:
    repo = repository(pg, test_settings)
    with pg.begin() as session:
        channel = session.get(Channel, catalog["channel_ids"][0])
        enqueue_command(session, model_actor, channel, "scan_inventory", {"revision": 1}, "scan")
    scan = repo.claim(catalog["node_id"], catalog["boot_id"])
    receipt = {
        "success": True,
        "applied_revision": 1,
        "inventory": {"node_classes": ["SyntheticNode"], "node_schema_digest": "1" * 64},
        "model_catalog": source["catalog"],
    }
    repo.receipt(
        catalog["node_id"],
        parse_id("cmd", scan["command_id"]),
        catalog["boot_id"],
        scan["command_digest"],
        receipt,
    )
    service = ModelFileAdministration(pg, test_settings)
    created = service.create(model_actor, request(catalog, source), "create", "read")
    assert service.create(model_actor, request(catalog, source), "create", "read") == created
    op = parse_id("op", created["operation_id"])
    assert service.detail(model_actor, op)["status"] == "pending"
    with pytest.raises(DomainError) as duplicate:
        service.create(model_actor, request(catalog, source), "second", "read")
    assert duplicate.value.code == "model_file_read_pending"
    claimed, result = finish(pg, test_settings, catalog, source)
    assert repo.receipt(
        catalog["node_id"],
        parse_id("cmd", claimed["command_id"]),
        catalog["boot_id"],
        claimed["command_digest"],
        result,
    )["accepted"]
    proof = service.detail(model_actor, op)
    assert not proof["stale"] and proof["inspection"]["sha256"] == result["inspection"]["sha256"]
    data = ModelFileRegistration(name="Verified synthetic file", kind="checkpoint")
    first = service.register(model_actor, op, data, "register", "read")
    assert service.register(model_actor, op, data, "register", "read") == first
    assert service.register(model_actor, op, data, "another-token", "read")["id"] == first["id"]
    inventory = AdminQueries(pg, test_settings).inventory(catalog["channel_ids"][0])["model_files"]
    assert inventory["data"][0]["registration_status"] == "unverified"
    assert inventory["data"][0]["registered_count"] == 1
    assert service.detail(model_actor, op)["matching_registrations"] == 1
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(ModelArtifact)) == 1
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is None
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is None


@pytest.mark.parametrize("change", ["boot", "revision", "age", "digest", "unapplied", "disabled"])
def test_stale_source_prevents_new_inspections(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
    change: str,
) -> None:
    with pg.begin() as session:
        channel = session.get(Channel, catalog["channel_ids"][0])
        node = session.get(Node, catalog["node_id"])
        if change == "boot":
            node.current_boot_id = uuid4()
        elif change == "revision":
            channel.desired_revision = 2
        elif change == "unapplied":
            channel.applied_revision = 0
        elif change == "disabled":
            node.enabled = False
        else:
            wrapper = {**channel.observation["model_catalog"]}
            if change == "age":
                wrapper["observed_at"] = int(utcnow().timestamp()) - 301
            else:
                wrapper["catalog"] = {**source["catalog"], "catalog_digest": "0" * 64}
            channel.observation = {**channel.observation, "model_catalog": wrapper}
    with pytest.raises(DomainError):
        ModelFileAdministration(pg, test_settings).create(
            model_actor, request(catalog, source), "stale", "read"
        )


@pytest.mark.parametrize("change", ["boot", "revision", "age", "scan_age"])
def test_successful_inspection_is_not_importable_after_its_context_expires(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
    change: str,
) -> None:
    service = ModelFileAdministration(pg, test_settings)
    op = parse_id(
        "op",
        service.create(model_actor, request(catalog, source), "create", "read")["operation_id"],
    )
    claimed, _ = finish(pg, test_settings, catalog, source)
    with pg.begin() as session:
        if change == "boot":
            session.get(Node, catalog["node_id"]).current_boot_id = uuid4()
        elif change == "revision":
            session.get(Channel, catalog["channel_ids"][0]).desired_revision = 2
        elif change == "age":
            session.get(WorkerCommand, parse_id("cmd", claimed["command_id"])).completed_at = (
                utcnow() - timedelta(seconds=301)
            )
        else:
            channel = session.get(Channel, catalog["channel_ids"][0])
            wrapper = {
                **channel.observation["model_catalog"],
                "observed_at": int(utcnow().timestamp()) - 301,
            }
            channel.observation = {**channel.observation, "model_catalog": wrapper}
    assert service.detail(model_actor, op)["stale"]
    with pytest.raises(DomainError):
        service.register(
            model_actor,
            op,
            ModelFileRegistration(name="Expired", kind="checkpoint"),
            "register",
            "read",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "unsafe"),
        ("bytes", 1),
        ("relative_path", "models/checkpoints/other.safetensors"),
        ("catalog_digest", "0" * 64),
        ("absolute_path", "C:/private"),
    ],
)
def test_spoofed_receipt_never_becomes_registration_evidence(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
    field: str,
    value: Any,
) -> None:
    service = ModelFileAdministration(pg, test_settings)
    op = parse_id(
        "op",
        service.create(model_actor, request(catalog, source), "create", "read")["operation_id"],
    )
    repo = repository(pg, test_settings)
    claimed = repo.claim(catalog["node_id"], catalog["boot_id"])
    inspection = inspect_model(source["root"], claimed["payload"], maximum_bytes=10**6, seconds=10)
    with pytest.raises(DomainError) as rejected:
        repo.receipt(
            catalog["node_id"],
            parse_id("cmd", claimed["command_id"]),
            catalog["boot_id"],
            claimed["command_digest"],
            {"success": True, "applied_revision": 1, "inspection": {**inspection, field: value}},
        )
    assert rejected.value.code == "invalid_model_inspection"
    assert service.detail(model_actor, op)["status"] == "running"


def test_legacy_scan_clears_previous_model_catalog(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
) -> None:
    with pg.begin() as session:
        enqueue_command(
            session,
            model_actor,
            session.get(Channel, catalog["channel_ids"][0]),
            "scan_inventory",
            {"revision": 1},
            "legacy",
        )
    repo = repository(pg, test_settings)
    claimed = repo.claim(catalog["node_id"], catalog["boot_id"])
    repo.receipt(
        catalog["node_id"],
        parse_id("cmd", claimed["command_id"]),
        catalog["boot_id"],
        claimed["command_digest"],
        {"success": True, "applied_revision": 1, "inventory": {}},
    )
    assert (
        AdminQueries(pg, test_settings).inventory(catalog["channel_ids"][0])["model_files"][
            "status"
        ]
        == "unsupported"
    )


def test_cross_channel_same_path_hash_deduplicates_concurrent_registrations(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
) -> None:
    service = ModelFileAdministration(pg, test_settings)
    ops: list[UUID] = []
    for index in range(2):
        ops.append(
            parse_id(
                "op",
                service.create(
                    model_actor, request(catalog, source, index), f"create-{index}", "read"
                )["operation_id"],
            )
        )
        finish(pg, test_settings, catalog, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda op: service.register(
                    model_actor,
                    op,
                    ModelFileRegistration(name="Concurrent", kind="checkpoint"),
                    str(uuid4()),
                    "read",
                ),
                ops,
            )
        )
    assert results[0]["id"] == results[1]["id"]


def test_only_creator_or_administrator_can_read_operation(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
) -> None:
    service = ModelFileAdministration(pg, test_settings)
    op = parse_id(
        "op",
        service.create(model_actor, request(catalog, source), "create", "read")["operation_id"],
    )
    other = AdminIdentity(uuid4(), "other", "workflow_maintainer", uuid4())
    with pytest.raises(DomainError) as denied:
        service.detail(other, op)
    assert denied.value.status == 404
    admin = AdminIdentity(uuid4(), "admin", "administrator", uuid4())
    assert service.detail(admin, op)["status"] == "pending"


@pytest.mark.parametrize(
    "role,expected",
    [("observer", 403), ("operator", 403), ("workflow_maintainer", 202), ("administrator", 202)],
)
def test_http_admin_permissions_csrf_and_closed_registration_body(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    test_settings: Settings,
    role: str,
    expected: int,
) -> None:
    with pg.begin() as session:
        session.add(
            AdminUser(username="http-model", password_hash=password_hash(PASSWORD), role=role)
        )
    login = AuthRepository(pg, test_settings).login("http-model", PASSWORD, "127.0.0.1", "test")
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        path = "/admin/api/v1/model-file-reads"
        assert (
            client.post(
                path,
                json=request(catalog, source).model_dump(),
                headers={"Authorization": "Bearer synthetic-business-key"},
            ).status_code
            == 401
        )
        client.cookies.set(COOKIE, login["token"])
        headers = {
            "Origin": test_settings.public_origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "http-create-model-file",
        }
        # The configured cookie name is part of the actual session contract.
        client.cookies.clear()
        client.cookies.set(COOKIE, login["token"])
        assert (
            client.post(
                path,
                json=request(catalog, source).model_dump(),
                headers={"Origin": test_settings.public_origin},
            ).status_code
            == 403
        )
        response = client.post(path, json=request(catalog, source).model_dump(), headers=headers)
        assert response.status_code == expected, response.text
        if expected == 202:
            identifier = response.json()["operation_id"]
            bad = client.post(
                path + "/" + identifier + "/registrations",
                json={"name": "forged", "kind": "checkpoint", "sha256": "1" * 64},
                headers=headers,
            )
            assert bad.status_code == 422


def test_actual_worker_scans_native_files_and_hashes_over_fixed_actions(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from windows_worker.commands import CommandExecutor
    from windows_worker.configuration import WorkerSettings

    paths: list[str] = []

    class ComfyFixture(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            body = b'{"SyntheticNode":{"input":{}}}'
            self.send_response(200 if self.path == "/object_info" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ComfyFixture)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        local = WorkerSettings(
            control_origin="https://127.0.0.1:8443",
            credential_file=source["root"] / "unused.secret",
            state_root=source["root"] / "state",
            gpus=[{"hardware_id": "SYNTHETIC-GPU-1", "name": "Synthetic"}],
            channels=[
                {"base_url": origin, "hardware_id": "SYNTHETIC-GPU-1", "comfy_root": source["root"]}
            ],
        )
        worker = CommandExecutor(local.state_root, local)
        repo = repository(pg, test_settings)
        with pg.begin() as session:
            channel = session.get(Channel, catalog["channel_ids"][0])
            channel.base_url = origin
            enqueue_command(
                session, model_actor, channel, "scan_inventory", {"revision": 1}, "native-files"
            )
        claimed = repo.claim(catalog["node_id"], catalog["boot_id"])
        scanned = worker.execute(claimed)
        assert scanned["model_catalog"]["entries"] == source["catalog"]["entries"]
        repo.receipt(
            catalog["node_id"],
            parse_id("cmd", claimed["command_id"]),
            catalog["boot_id"],
            claimed["command_digest"],
            scanned,
        )
        service = ModelFileAdministration(pg, test_settings)
        op = parse_id(
            "op",
            service.create(model_actor, request(catalog, source), "native-create", "native-files")[
                "operation_id"
            ],
        )
        claimed = repo.claim(catalog["node_id"], catalog["boot_id"])
        inspected = worker.execute(claimed)
        repo.receipt(
            catalog["node_id"],
            parse_id("cmd", claimed["command_id"]),
            catalog["boot_id"],
            claimed["command_digest"],
            inspected,
        )
        login = AuthRepository(pg, test_settings).login(
            model_actor.username, PASSWORD, "127.0.0.1", "native-files"
        )
        with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
            client.cookies.set(COOKIE, login["token"])
            registered = client.post(
                f"/admin/api/v1/model-file-reads/{public_id('op', op)}/registrations",
                json={"name": "Native synthetic bytes", "kind": "checkpoint"},
                headers={
                    "Origin": test_settings.public_origin,
                    "X-CSRF-Token": login["csrf_token"],
                    "Idempotency-Key": "native-register-model-file",
                },
            )
            assert registered.status_code == 201, registered.text
            inventory = client.get(
                f"/admin/api/v1/channels/{request(catalog, source).channel_id}/inventory"
            )
            assert inventory.status_code == 200, inventory.text
            assert inventory.json()["model_files"]["data"][0]["registered_count"] == 1
        assert paths == ["/object_info"]  # No /prompt, model load or download.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("invalid", ["extra_path", "digest", "revision_type", "unknown_kind"])
def test_invalid_model_catalog_receipt_is_rejected_atomically(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
    invalid: str,
) -> None:
    from copy import deepcopy

    with pg.begin() as session:
        enqueue_command(
            session,
            model_actor,
            session.get(Channel, catalog["channel_ids"][0]),
            "scan_inventory",
            {"revision": 1},
            "bad-catalog",
        )
    repo = repository(pg, test_settings)
    claimed = repo.claim(catalog["node_id"], catalog["boot_id"])
    result = {
        "success": True,
        "applied_revision": 1,
        "inventory": {"node_classes": [], "node_schema_digest": "1" * 64},
        "model_catalog": deepcopy(source["catalog"]),
    }
    if invalid == "extra_path":
        result["model_catalog"]["entries"][0]["absolute_path"] = "C:/private"
    elif invalid == "digest":
        result["model_catalog"]["catalog_digest"] = "0" * 64
    elif invalid == "revision_type":
        result["applied_revision"] = True
    else:
        result["model_catalog"]["entries"][0]["kind"] = "untrusted"
    with pytest.raises(DomainError) as rejected:
        repo.receipt(
            catalog["node_id"],
            parse_id("cmd", claimed["command_id"]),
            catalog["boot_id"],
            claimed["command_digest"],
            result,
        )
    assert rejected.value.code == "invalid_model_inventory"
    with pg() as session:
        assert session.get(WorkerCommand, parse_id("cmd", claimed["command_id"])).state == "running"
        assert (
            session.get(Channel, catalog["channel_ids"][0]).observation["model_catalog"]["catalog"]
            == source["catalog"]
        )


@pytest.mark.parametrize("change", ["age", "revision", "catalog_removed"])
def test_valid_hash_observation_finishes_after_source_changes_but_cannot_register(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    source: dict[str, Any],
    model_actor: AdminIdentity,
    test_settings: Settings,
    change: str,
) -> None:
    service = ModelFileAdministration(pg, test_settings)
    op = parse_id(
        "op",
        service.create(model_actor, request(catalog, source), "source-change-create", "read")[
            "operation_id"
        ],
    )
    repo = repository(pg, test_settings)
    claimed = repo.claim(catalog["node_id"], catalog["boot_id"])
    result = {
        "success": True,
        "applied_revision": 1,
        "inspection": inspect_model(
            source["root"], claimed["payload"], maximum_bytes=10**6, seconds=10
        ),
    }
    with pg.begin() as session:
        channel = session.get(Channel, catalog["channel_ids"][0])
        if change == "revision":
            channel.desired_revision = 2
        elif change == "age":
            channel.observation = {
                **channel.observation,
                "model_catalog": {
                    **channel.observation["model_catalog"],
                    "observed_at": int(utcnow().timestamp()) - 301,
                },
            }
        else:
            channel.observation = {
                k: v for k, v in channel.observation.items() if k != "model_catalog"
            }
        enqueue_command(
            session,
            model_actor,
            session.get(Channel, catalog["channel_ids"][1]),
            "scan_inventory",
            {"revision": 1},
            "next-read",
        )
    accepted = repo.receipt(
        catalog["node_id"],
        parse_id("cmd", claimed["command_id"]),
        catalog["boot_id"],
        claimed["command_digest"],
        result,
    )
    assert accepted == {"accepted": True, "status": "succeeded"}
    assert (
        repo.receipt(
            catalog["node_id"],
            parse_id("cmd", claimed["command_id"]),
            catalog["boot_id"],
            claimed["command_digest"],
            result,
        )
        == accepted
    )
    assert service.detail(model_actor, op)["stale"]
    with pytest.raises(DomainError):
        service.register(
            model_actor,
            op,
            ModelFileRegistration(name="Stale evidence", kind="checkpoint"),
            "stale-proof-register",
            "read",
        )
    next_command = repo.claim(catalog["node_id"], catalog["boot_id"])
    assert next_command and next_command["type"] == "scan_inventory"
