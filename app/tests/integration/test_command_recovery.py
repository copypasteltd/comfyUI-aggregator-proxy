from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.admin import ExpectedRevision
from generation_contracts.command_recovery import CommandReconciliation
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.admin_channels import ChannelAdministration
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.models import (
    AuditEvent,
    Channel,
    Gpu,
    ManagementOperation,
    Node,
    WorkerCommand,
    WorkflowBinding,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import admin_context

pytestmark = pytest.mark.integration


def issued(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    settings: Settings,
    command_type: str = "apply_channel_config",
) -> tuple[CommandRepository, UUID, UUID, str]:
    actor, _, _ = admin_context(pg, settings)
    with pg.begin() as session:
        operation = ManagementOperation(actor_id=actor.user_id, command=command_type)
        session.add(operation)
        session.flush()
        channel = session.get(Channel, catalog["channel_ids"][0])
        assert channel
        payload = {
            "revision": 1,
            "base_url": channel.base_url,
            "gpu_id": public_id("gpu", channel.gpu_id),
            "channel_id": public_id("ch", channel.id),
            "release_digest": catalog["release_digest"],
        }
        command = WorkerCommand(
            operation_id=operation.id,
            node_id=catalog["node_id"],
            channel_id=channel.id,
            command=command_type,
            payload=payload,
            state="running",
            boot_id=catalog["boot_id"],
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        session.add(command)
        session.flush()
        session.get(Gpu, catalog["gpu_id"]).management_hold = command.id
        return (
            CommandRepository(pg, SecretVault(settings.encryption_key.get_secret_value())),
            command.id,
            operation.id,
            fingerprint({"type": command_type, "payload": payload}),
        )


def proof(boot: UUID, digest: str, result: dict[str, Any] | None) -> CommandReconciliation:
    return CommandReconciliation(
        protocol="command-reconciliation-v1",
        boot_id=str(boot),
        command_digest=digest,
        observation="completed" if result is not None else "unknown",
        result=result,
    )


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "kind,result",
    [
        ("apply_channel_config", {"success": True, "applied_revision": 1}),
        ("probe_channel", {"success": True, "applied_revision": 1, "comfy_reachable": True}),
        ("scan_inventory", {"success": True, "applied_revision": 1, "inventory": {}}),
        (
            "collect_diagnostics",
            {
                "success": True,
                "applied_revision": 1,
                "queue_running_count": 0,
                "queue_pending_count": 0,
            },
        ),
        *[
            (kind, {"success": False, "error_code": "synthetic_failure"})
            for kind in (
                "apply_channel_config",
                "apply_workflow_release",
                "pull_workflow",
                "inspect_model_file",
            )
        ],
    ],
)
def test_finished_result_closes_once_without_restoring_environment(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    restart: bool,
    kind: str,
    result: dict[str, Any],
) -> None:
    repo, cid, oid, digest = issued(pg, catalog, test_settings, kind)
    boot = uuid4() if restart else catalog["boot_id"]
    with pg.begin() as session:
        session.get(Node, catalog["node_id"]).current_boot_id = boot
        channel = session.get(Channel, catalog["channel_ids"][0])
        channel.applied_revision, channel.verified_revision = 2, None
        session.get(Gpu, catalog["gpu_id"]).fence = 41
    data = proof(boot, digest, result)
    with ThreadPoolExecutor(4) as pool:
        values = list(pool.map(lambda _: repo.reconcile(catalog["node_id"], cid, data), range(4)))
    assert all(
        value["acknowledged"] and value["resolution"] == "result_requires_revalidation"
        for value in values
    )
    with pg() as session:
        command, channel, gpu = (
            session.get(WorkerCommand, cid),
            session.get(Channel, catalog["channel_ids"][0]),
            session.get(Gpu, catalog["gpu_id"]),
        )
        assert command.state == "failed" and command.completed_at is not None
        assert command.receipt == {"success": False, "error_code": "command_revalidation_required"}
        assert (channel.applied_revision, channel.verified_revision) == (2, None)
        assert gpu.management_hold is None and gpu.fence == 41
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "command.reconciled")
            )
            == 1
        )
    query = AdminQueries(pg, test_settings)
    target = query.operation(oid)["targets"][0]
    assert (
        not target["needs_reconciliation"]
        and target["recovery"]["resolution"] == "result_requires_revalidation"
    )
    assert "_control_recovery" not in str(query.operations())
    with pytest.raises(DomainError, match="不同"):
        repo.reconcile(
            catalog["node_id"],
            cid,
            proof(boot, digest, {"success": False, "error_code": "different_result"}),
        )


@pytest.mark.parametrize("restart", [False, True])
def test_unknown_keeps_hold_and_claim_blocks_every_boot(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, restart: bool
) -> None:
    repo, cid, oid, digest = issued(pg, catalog, test_settings)
    boot = uuid4() if restart else catalog["boot_id"]
    with pg.begin() as session:
        session.get(Node, catalog["node_id"]).current_boot_id = boot
    value = repo.next_command(catalog["node_id"], boot)
    assert value["command"] is None and value["reconciliation"]["command_id"] == public_id(
        "cmd", cid
    )
    assert value["reconciliation"]["reason"] == ("previous_boot" if restart else "expired")
    assert (
        repo.reconcile(catalog["node_id"], cid, proof(boot, digest, None))["resolution"]
        == "evidence_required"
    )
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]).management_hold == cid
        assert session.get(WorkerCommand, cid).state == "running"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "command.reconciled")
            )
            == 0
        )
    target = AdminQueries(pg, test_settings).operation(oid)["targets"][0]
    assert target["needs_reconciliation"] and target["reason"] == value["reconciliation"]["reason"]


def test_lost_ordinary_ack_preserves_original_terminal_and_environment(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    repo, cid, _, digest = issued(pg, catalog, test_settings)
    result = {"success": True, "applied_revision": 1}
    with pg.begin() as session:
        session.get(WorkerCommand, cid).lease_expires_at = utcnow() + timedelta(seconds=30)
    assert repo.receipt(catalog["node_id"], cid, catalog["boot_id"], digest, result)["accepted"]
    with pg.begin() as session:
        boot = uuid4()
        session.get(Node, catalog["node_id"]).current_boot_id = boot
    value = repo.reconcile(catalog["node_id"], cid, proof(boot, digest, result))
    assert value["resolution"] == "already_completed" and value["command_status"] == "succeeded"
    assert repo.reconcile(catalog["node_id"], cid, proof(boot, digest, None))["acknowledged"]
    with pg() as session:
        assert session.get(Channel, catalog["channel_ids"][0]).applied_revision == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "command.receipt")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "command.reconciled")
            )
            == 0
        )


@pytest.mark.parametrize(
    "case,code",
    [
        ("live", "command_still_leased"),
        ("digest", "command_digest_mismatch"),
        ("boot", "stale_boot"),
        ("revision", "invalid_command_reconciliation"),
        ("bool_revision", "invalid_command_reconciliation"),
        ("foreign_node", "not_found"),
        ("disabled", "stale_boot"),
    ],
)
def test_rejects_invalid_evidence_without_release(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    case: str,
    code: str,
) -> None:
    repo, cid, _, digest = issued(pg, catalog, test_settings)
    node_id, boot = catalog["node_id"], catalog["boot_id"]
    result = {
        "success": True,
        "applied_revision": True if case == "bool_revision" else 2 if case == "revision" else 1,
    }
    with pg.begin() as session:
        if case == "live":
            session.get(WorkerCommand, cid).lease_expires_at = utcnow() + timedelta(seconds=30)
        if case == "disabled":
            session.get(Node, node_id).enabled = False
        if case == "foreign_node":
            other = Node(
                name="Foreign",
                credential_identifier=uuid4().hex,
                credential_verifier="0" * 64,
                current_boot_id=boot,
            )
            session.add(other)
            session.flush()
            node_id = other.id
    if case == "digest":
        digest = "f" * 64
    if case == "boot":
        boot = uuid4()
    with pytest.raises(DomainError) as error:
        repo.reconcile(node_id, cid, proof(boot, digest, result))
    assert error.value.code == code
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]).management_hold == cid
        assert session.get(WorkerCommand, cid).state == "running"


def test_other_management_hold_survives_and_reapply_is_explicit_idempotent(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    repo, cid, _, digest = issued(pg, catalog, test_settings)
    actor, _, _ = admin_context(pg, test_settings, name="reapply-admin")
    admin = ChannelAdministration(pg, test_settings)
    channel_id = catalog["channel_ids"][0]
    with pytest.raises(DomainError) as error:
        admin.reapply_channel(
            actor, channel_id, ExpectedRevision(expected_revision=1), "before", "test"
        )
    assert error.value.code == "channel_command_unresolved"
    other = uuid4()
    with pg.begin() as session:
        session.get(Gpu, catalog["gpu_id"]).management_hold = other
    repo.reconcile(
        catalog["node_id"],
        cid,
        proof(catalog["boot_id"], digest, {"success": True, "applied_revision": 1}),
    )
    with ThreadPoolExecutor(4) as pool:
        values = list(
            pool.map(
                lambda _: admin.reapply_channel(
                    actor, channel_id, ExpectedRevision(expected_revision=1), "same", "test"
                ),
                range(4),
            )
        )
    assert len({v["operation_id"] for v in values}) == 1
    with pg() as session:
        channel = session.get(Channel, channel_id)
        assert (
            channel.desired_revision,
            channel.revision,
            channel.verified_revision,
            channel.enabled,
            channel.draining,
        ) == (1, 2, None, False, True)
        assert session.get(Gpu, catalog["gpu_id"]).management_hold == other
        assert (
            session.scalar(
                select(WorkflowBinding).where(WorkflowBinding.channel_id == channel_id)
            ).verified_digest
            is None
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "channel.reapplied")
            )
            == 1
        )
    assert repo.next_command(catalog["node_id"], catalog["boot_id"])["command"] is None


def test_same_node_claims_return_same_command_and_reapply_revision_guard(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    admin = ChannelAdministration(pg, test_settings)
    cid = catalog["channel_ids"][0]
    with pytest.raises(DomainError) as error:
        admin.reapply_channel(
            actor, cid, ExpectedRevision(expected_revision=2), "wrong-revision", "test"
        )
    assert error.value.code == "config_revision_conflict"
    operation = admin.reapply_channel(
        actor, cid, ExpectedRevision(expected_revision=1), "new", "test"
    )
    repo = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    with ThreadPoolExecutor(8) as pool:
        values = list(
            pool.map(lambda _: repo.next_command(catalog["node_id"], catalog["boot_id"]), range(8))
        )
    assert {v["command"]["operation_id"] for v in values} == {operation["operation_id"]}
    assert len({v["command"]["command_id"] for v in values}) == 1


@pytest.mark.parametrize(
    "role,status",
    [("administrator", 202), ("operator", 202), ("observer", 403), ("workflow_maintainer", 403)],
)
def test_reapply_http_identity_revision_and_csrf(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    role: str,
    status: int,
) -> None:
    _, _, login = admin_context(pg, test_settings, role=role)
    identifier = public_id("ch", catalog["channel_ids"][0])
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.cookies.set("gen_admin", login["token"], domain="127.0.0.1", path="/admin")
        headers = {
            "Origin": test_settings.public_origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "reapply-http-test-1",
            "If-Match": '"1"',
        }
        response = client.post(
            f"/admin/api/v1/channels/{identifier}/reapply",
            headers=headers,
            json={"expected_revision": 1},
        )
        assert response.status_code == status, response.text
        if status == 202:
            assert (
                client.post(
                    f"/admin/api/v1/channels/{identifier}/reapply",
                    headers=headers,
                    json={"expected_revision": 1},
                ).json()
                == response.json()
            )
            assert (
                client.post(
                    f"/admin/api/v1/channels/{identifier}/reapply", json={"expected_revision": 2}
                ).status_code
                == 403
            )
