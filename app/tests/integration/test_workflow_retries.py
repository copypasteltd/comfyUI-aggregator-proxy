from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, current_thread
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.catalog import WorkflowSync
from generation_contracts.workflow_retries import WorkflowSyncRetry
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.admin_workflow_retries import RETRY_KEY, WorkflowSyncRetries
from generation_infrastructure.command_recovery import RECOVERY_KEY
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.models import (
    AdminMutation,
    AdminUser,
    AuditEvent,
    Channel,
    Gpu,
    ManagementOperation,
    Node,
    WorkerCommand,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, admin_context

pytestmark = pytest.mark.integration


def test_duplicate_terminal_receipt_and_retry_do_not_deadlock(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    actor, source, repo = completed_batch(pg, catalog, test_settings)
    service = WorkflowSyncRetries(pg, test_settings)
    body = retry_body(service.options(source))
    with pg() as session:
        command = session.get(WorkerCommand, parse_id("cmd", body.targets[0].command_id))
        cid, result = command.id, command.receipt
        digest = fingerprint({"type": command.command, "payload": command.payload})
    receipt_before_channel, retry_before_command, resume = Event(), Event(), Event()
    engine = pg.kw["bind"]

    def pause(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        name = current_thread().name
        if (
            name.endswith("receipt_0")
            and "FROM channels" in statement
            and "FOR UPDATE" in statement
        ):
            receipt_before_channel.set()
            assert resume.wait(5)
        if (
            name.endswith("retry_0")
            and "FROM worker_commands" in statement
            and "FOR UPDATE" in statement
        ):
            retry_before_command.set()

    event.listen(engine, "before_cursor_execute", pause)
    try:
        with (
            ThreadPoolExecutor(1, thread_name_prefix="receipt") as receipts,
            ThreadPoolExecutor(1, thread_name_prefix="retry") as retries,
        ):
            receipt = receipts.submit(
                repo.receipt, catalog["node_id"], cid, catalog["boot_id"], digest, result
            )
            assert receipt_before_channel.wait(5)
            retry = retries.submit(
                service.retry, actor, source, body, "concurrent-duplicate-receipt", "test"
            )
            assert retry_before_command.wait(5)
            resume.set()
            assert receipt.result(timeout=5) == {"accepted": True, "status": "failed"}
            assert retry.result(timeout=5)["status"] == "pending"
    finally:
        resume.set()
        event.remove(engine, "before_cursor_execute", pause)


def test_retry_preserves_real_generation_occupancy_and_waits_for_gpu(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    from generation_infrastructure.jobs import JobRepository
    from generation_infrastructure.leases import LeaseRepository
    from generation_infrastructure.models import GpuOccupancy

    job = JobRepository(pg).create(
        catalog["identity"],
        {
            "model": "synthetic-image",
            "task_type": "text_to_image",
            "input": {"prompt": "synthetic occupancy test"},
        },
        "before-management",
        "test",
    )
    actor, source, repo = completed_batch(pg, catalog, test_settings)
    with pg.begin() as session:
        session.get(Channel, catalog["channel_ids"][0]).draining = False
    claim = LeaseRepository(
        pg,
        SecretVault(test_settings.encryption_key.get_secret_value()),
        allow_synthetic_catalog=True,
    ).claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == job["id"]
    service = WorkflowSyncRetries(pg, test_settings)
    with pg() as session:
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        before = (
            occupancy.attempt_id,
            occupancy.uncertain,
            session.get(Gpu, catalog["gpu_id"]).fence,
        )
    service.retry(actor, source, retry_body(service.options(source)), "occupied-generation", "test")
    assert repo.claim(catalog["node_id"], catalog["boot_id"]) is None
    with pg() as session:
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        assert (
            occupancy.attempt_id,
            occupancy.uncertain,
            session.get(Gpu, catalog["gpu_id"]).fence,
        ) == before


def completed_batch(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    settings: Settings,
    *,
    all_failed: bool = False,
) -> tuple[AdminIdentity, UUID, CommandRepository]:
    actor, _, _ = admin_context(pg, settings)
    body = WorkflowSync(
        direction="push",
        release_id=public_id("wfr", catalog["release_id"]),
        release_digest=catalog["release_digest"],
        targets=[
            {"channel_id": public_id("ch", c), "expected_revision": 1}
            for c in catalog["channel_ids"]
        ],
    )
    accepted = CatalogAdministration(pg, settings).synchronize(
        actor, body, "original-batch", "test"
    )
    repo = CommandRepository(pg, SecretVault(settings.encryption_key.get_secret_value()))
    for _ in catalog["channel_ids"]:
        issued = repo.claim(catalog["node_id"], catalog["boot_id"])
        assert issued
        failed = all_failed or issued["payload"]["channel_id"] != public_id(
            "ch", catalog["channel_ids"][0]
        )
        result = (
            {
                "success": False,
                "error_code": "synthetic_dependency_missing",
                "private": "never-project-worker-json",
                RETRY_KEY: {"forged": "ignored"},
            }
            if failed
            else {
                "success": True,
                "applied_revision": 1,
                "environment_verified": True,
                "release_digest": catalog["release_digest"],
            }
        )
        repo.receipt(
            catalog["node_id"],
            parse_id("cmd", issued["command_id"]),
            catalog["boot_id"],
            issued["command_digest"],
            result,
        )
    return actor, parse_id("op", accepted["operation_id"]), repo


def retry_body(options: dict[str, Any], *, all_targets: bool = False) -> WorkflowSyncRetry:
    return WorkflowSyncRetry(
        release_id=options["release_id"],
        release_digest=options["release_digest"],
        targets=[
            {
                "command_id": t["command_id"],
                "channel_id": t["channel_id"],
                "expected_revision": t["current_revision"],
            }
            for t in options["targets"]
            if all_targets or t["eligible"]
        ],
    )


def test_partial_failure_retries_only_failed_preserves_original_and_survives_service_restart(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    actor, source, repo = completed_batch(pg, catalog, test_settings)
    service = WorkflowSyncRetries(pg, test_settings)
    options = service.options(source)
    assert options["source_status"] == "partial_failed" and len(options["targets"]) == 2
    assert [t["eligible"] for t in options["targets"]].count(True) == 1
    assert (
        "never-project-worker-json" not in str(options)
        and "manifest" not in str(options)
        and "base_url" not in str(options)
    )
    with pg.begin() as session:
        before = {
            c.id: (c.state, c.receipt, c.completed_at)
            for c in session.scalars(select(WorkerCommand))
        }
        success = session.get(Channel, catalog["channel_ids"][0])
        binding = session.scalar(
            select(WorkflowBinding).where(WorkflowBinding.channel_id == success.id)
        )
        binding.runtime_approval = {"test_only_preserve_success": True}
        success_revision = success.revision
    body = retry_body(options)
    result = service.retry(actor, source, body, "one-retry", "test")
    child = parse_id("op", result["operation_id"])
    with pg() as session:
        assert session.get(ManagementOperation, source).state == "partial_failed"
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 3
        for identifier, original in before.items():
            command = session.get(WorkerCommand, identifier)
            assert (command.state, command.receipt, command.completed_at) == original
        success = session.get(Channel, catalog["channel_ids"][0])
        assert success.revision == success_revision
        assert session.scalar(
            select(WorkflowBinding).where(WorkflowBinding.channel_id == success.id)
        ).runtime_approval == {"test_only_preserve_success": True}
        target = session.get(Channel, parse_id("ch", body.targets[0].channel_id))
        assert target.draining and target.revision == body.targets[0].expected_revision + 1
        assert session.get(ManagementOperation, child).summary["retry_of"] == public_id(
            "op", source
        )
    rebuilt = WorkflowSyncRetries(pg, test_settings)
    assert rebuilt.retry(actor, source, body, "one-retry", "replayed") == result
    replay_options = rebuilt.options(source)
    failed = next(t for t in replay_options["targets"] if t["status"] == "failed")
    assert (
        failed["reason"] == "already_retried"
        and failed["retry_operation_id"] == result["operation_id"]
    )
    with pytest.raises(DomainError) as duplicate:
        rebuilt.retry(actor, source, body, "different-key", "test")
    assert duplicate.value.code == "workflow_retry_already_retried"
    issued = repo.claim(catalog["node_id"], catalog["boot_id"])
    assert issued and issued["payload"]["release_digest"] == options["release_digest"]
    repo.receipt(
        catalog["node_id"],
        parse_id("cmd", issued["command_id"]),
        catalog["boot_id"],
        issued["command_digest"],
        {
            "success": True,
            "applied_revision": 1,
            "environment_verified": True,
            "release_digest": options["release_digest"],
        },
    )
    assert AdminQueries(pg, test_settings).operation(child)["status"] == "succeeded"
    assert AdminQueries(pg, test_settings).operation(child)["retry_of"] == public_id("op", source)
    assert RETRY_KEY not in str(AdminQueries(pg, test_settings).operations())
    with pg() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "workflow.sync_retried")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "workflow.channel_retried")
            )
            == 1
        )


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_retry_has_one_durable_successor(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, same_key: bool
) -> None:
    actor, source, _ = completed_batch(pg, catalog, test_settings)
    service = WorkflowSyncRetries(pg, test_settings)
    body = retry_body(service.options(source))

    def submit(index: int) -> dict[str, Any] | str:
        try:
            return service.retry(
                actor, source, body, "same-key" if same_key else f"key-{index}", "test"
            )
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(6) as executor:
        results = list(executor.map(submit, range(6)))
    accepted = [r["operation_id"] for r in results if isinstance(r, dict)]
    assert len(set(accepted)) == 1
    assert len(accepted) == (6 if same_key else 1)
    assert all(isinstance(r, dict) or r == "workflow_retry_already_retried" for r in results)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 3
        assert session.scalar(select(func.count()).select_from(ManagementOperation)) == 2


def test_disjoint_failed_targets_merge_lineage_and_each_can_retry_later_child(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    actor, source, repo = completed_batch(pg, catalog, test_settings, all_failed=True)
    service = WorkflowSyncRetries(pg, test_settings)
    body = retry_body(service.options(source))
    with ThreadPoolExecutor(2) as executor:
        results = list(
            executor.map(
                lambda t: service.retry(
                    actor, source, body.model_copy(update={"targets": [t]}), t.command_id, "test"
                ),
                body.targets,
            )
        )
    assert len({r["operation_id"] for r in results}) == 2
    with pg() as session:
        assert len(session.get(ManagementOperation, source).summary[RETRY_KEY]) == 2
    issued = repo.claim(catalog["node_id"], catalog["boot_id"])
    assert issued
    repo.receipt(
        catalog["node_id"],
        parse_id("cmd", issued["command_id"]),
        catalog["boot_id"],
        issued["command_digest"],
        {"success": False, "error_code": "synthetic_dependency_missing"},
    )
    with pg() as session:
        child = session.get(WorkerCommand, parse_id("cmd", issued["command_id"])).operation_id
    options = service.options(child)
    assert options["targets"][0]["eligible"]
    assert service.retry(actor, child, retry_body(options), "retry-child", "test")[
        "operation_id"
    ] not in {r["operation_id"] for r in results}


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing-receipt", "failure_evidence_required"),
        ("unknown", "operation_incomplete"),
        ("recovered", "revalidation_required"),
        ("deleted", "channel_unavailable"),
        ("configuration", "configuration_changed"),
        ("release", "release_unavailable"),
        ("binding", "release_unavailable"),
        ("busy", "management_busy"),
        ("superseded", "superseded"),
    ],
)
def test_changed_or_uncertain_target_rejected_without_new_effects(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    change: str,
    reason: str,
) -> None:
    actor, source, _ = completed_batch(pg, catalog, test_settings)
    service = WorkflowSyncRetries(pg, test_settings)
    body = retry_body(service.options(source))
    command_id = parse_id("cmd", body.targets[0].command_id)
    with pg.begin() as session:
        command = session.get(WorkerCommand, command_id)
        channel = session.get(Channel, command.channel_id)
        if change == "missing-receipt":
            command.receipt = {"error_code": "synthetic_no_execution_fact"}
        elif change == "unknown":
            command.state, command.completed_at, command.receipt = "running", None, None
            command.lease_expires_at = utcnow() - timedelta(seconds=1)
            session.get(ManagementOperation, source).state = "running"
            session.get(Gpu, channel.gpu_id).management_hold = command.id
        elif change == "recovered":
            session.get(ManagementOperation, source).summary = {
                RECOVERY_KEY: {
                    public_id("cmd", command.id): {
                        "protocol": "command-reconciliation-v1",
                        "reconciled_at": int(utcnow().timestamp()),
                    }
                }
            }
        elif change == "deleted":
            channel.deleted_at = utcnow()
        elif change == "configuration":
            channel.desired_revision += 1
        elif change == "release":
            session.get(WorkflowRelease, catalog["release_id"]).validated = False
        elif change == "binding":
            session.scalar(
                select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id)
            ).desired_digest = "0" * 64
        else:
            other = ManagementOperation(
                actor_id=actor.user_id,
                command="probe_channel" if change == "busy" else "apply_workflow_release",
                state="succeeded",
            )
            session.add(other)
            session.flush()
            session.add(
                WorkerCommand(
                    operation_id=other.id,
                    node_id=channel.node_id,
                    channel_id=channel.id,
                    command=other.command,
                    payload=command.payload,
                    state="pending" if change == "busy" else "succeeded",
                )
            )
    projected = next(
        t
        for t in service.options(source)["targets"]
        if t["command_id"] == body.targets[0].command_id
    )
    assert not projected["eligible"] and projected["reason"] == reason
    with pg() as session:
        count = session.scalar(select(func.count()).select_from(WorkerCommand))
        revisions = [(c.id, c.revision) for c in session.scalars(select(Channel))]
    with pytest.raises(DomainError) as rejected:
        service.retry(actor, source, body, "must-rollback", "test")
    assert rejected.value.code == "workflow_retry_" + reason
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == count
        assert [(c.id, c.revision) for c in session.scalars(select(Channel))] == revisions
        assert RETRY_KEY not in session.get(ManagementOperation, source).summary
        assert (
            session.scalar(
                select(func.count())
                .select_from(AdminMutation)
                .where(AdminMutation.operation.like("workflow.retry_sync:%"))
            )
            == 0
        )
        if change == "unknown":
            assert session.get(Gpu, catalog["gpu_id"]).management_hold == command_id


@pytest.mark.parametrize(
    "change,code",
    [
        ("success", "workflow_retry_not_failed"),
        ("revision", "config_revision_conflict"),
        ("target", "workflow_retry_target_conflict"),
        ("digest", "workflow_retry_version_conflict"),
    ],
)
def test_invalid_selection_rolls_back_entire_batch(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    change: str,
    code: str,
) -> None:
    actor, source, _ = completed_batch(pg, catalog, test_settings)
    service = WorkflowSyncRetries(pg, test_settings)
    options = service.options(source)
    body = retry_body(options, all_targets=change == "success")
    if change == "revision":
        body.targets[0].expected_revision = 999
    elif change == "target":
        body.targets[0].channel_id = public_id("ch", uuid4())
    elif change == "digest":
        body.release_digest = "f" * 64
    with pytest.raises(DomainError) as rejected:
        service.retry(actor, source, body, "invalid", "test")
    assert rejected.value.code == code
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 2
        assert all(c.revision == 2 for c in session.scalars(select(Channel)))
        assert RETRY_KEY not in session.get(ManagementOperation, source).summary


def test_retry_rechecks_revision_and_never_releases_other_gpu_hold(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    actor, source, repo = completed_batch(pg, catalog, test_settings, all_failed=True)
    service = WorkflowSyncRetries(pg, test_settings)
    body = retry_body(service.options(source))
    other_hold = uuid4()
    with pg.begin() as session:
        gpu = session.get(Gpu, catalog["gpu_id"])
        gpu.management_hold, gpu.fence = other_hold, 41
        session.get(Channel, parse_id("ch", body.targets[-1].channel_id)).revision += 1
    with pytest.raises(DomainError) as stale:
        service.retry(actor, source, body, "stale-second-target", "test")
    assert stale.value.code == "config_revision_conflict"
    body = retry_body(service.options(source))
    service.retry(actor, source, body, "new-snapshot", "test")
    assert repo.claim(catalog["node_id"], catalog["boot_id"]) is None
    with pg() as session:
        gpu = session.get(Gpu, catalog["gpu_id"])
        assert gpu.management_hold == other_hold and gpu.fence == 41
        assert all(
            b.runtime_approval is None and b.verified_digest is None
            for b in session.scalars(select(WorkflowBinding))
        )


def test_source_lock_timeout_is_bounded_and_sanitized(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    actor, source, _ = completed_batch(pg, catalog, test_settings)
    service = WorkflowSyncRetries(pg, test_settings)
    body = retry_body(service.options(source))
    with pg.begin() as blocker:
        blocker.execute(select(Node).where(Node.id == catalog["node_id"]).with_for_update())
        with pytest.raises(DomainError) as rejected:
            service.retry(actor, source, body, "busy-lock", "test")
        assert rejected.value.code == "command_busy" and "SELECT" not in rejected.value.message
    assert service.retry(actor, source, body, "busy-lock", "test")["status"] == "pending"


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("administrator", True),
        ("workflow_maintainer", True),
        ("operator", False),
        ("observer", False),
    ],
)
def test_http_retry_permissions_csrf_and_real_operation_projection(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    role: str,
    allowed: bool,
) -> None:
    actor, source, _ = completed_batch(pg, catalog, test_settings)
    with pg.begin() as session:
        session.get(AdminUser, actor.user_id).role = role
    root = "/admin/api/v1/workflow-syncs/" + public_id("op", source)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get(root + "/retry-options").status_code == 401
        client.headers["Origin"] = test_settings.public_origin
        login = client.post(
            "/admin/api/v1/session", json={"username": actor.username, "password": PASSWORD}
        )
        assert login.status_code == 200
        preview = client.get(root + "/retry-options")
        assert preview.status_code == (200 if allowed else 403)
        body = retry_body(WorkflowSyncRetries(pg, test_settings).options(source)).model_dump(
            mode="json"
        )
        assert (
            client.post(
                root + "/retries",
                json=body,
                headers={"Idempotency-Key": "workflow-http-retry-token"},
            ).status_code
            == 403
        )
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        accepted = client.post(
            root + "/retries", json=body, headers={"Idempotency-Key": "workflow-http-retry-token"}
        )
        assert accepted.status_code == (202 if allowed else 403)
        if allowed:
            assert (
                client.post(
                    root + "/retries",
                    json=body,
                    headers={"Idempotency-Key": "workflow-http-retry-token"},
                ).json()
                == accepted.json()
            )
            detail = client.get(
                "/admin/api/v1/operations/" + accepted.json()["operation_id"]
            ).json()
            assert detail["retry_of"] == public_id("op", source) and len(detail["targets"]) == 1
            assert "never-project-worker-json" not in preview.text
