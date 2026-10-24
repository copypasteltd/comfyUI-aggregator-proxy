from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, current_thread
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.admin import ChannelCreate
from generation_contracts.catalog import ValidationJob
from generation_contracts.channel_deletion import ChannelDeletionCheck
from generation_contracts.validation import JobCreate, validate
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.admin_channels import ChannelAdministration
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminUser,
    Attempt,
    AuditEvent,
    Channel,
    ChannelRevision,
    Gpu,
    GpuOccupancy,
    Job,
    ManagementOperation,
    Node,
    OutboxEvent,
    PublicModel,
    WorkerCommand,
    WorkflowBinding,
)
from generation_infrastructure.security import SecretVault, issue_secret, password_hash
from generation_infrastructure.settings import Settings
from generation_infrastructure.validation_jobs import ValidationJobs
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-channel-deletion-password"
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "PRIVATE synthetic deletion test"}}


def actor(pg: sessionmaker[Session], role: str = "administrator") -> AdminIdentity:
    with pg.begin() as session:
        row = AdminUser(username="delete-" + role, role=role, password_hash=password_hash(PASSWORD))
        session.add(row)
        session.flush()
        return AdminIdentity(row.id, row.username, row.role, uuid4())


def unpublish(pg: sessionmaker[Session]) -> UUID:
    with pg.begin() as session:
        row = session.scalar(select(PublicModel))
        assert row is not None
        row.published = False
        return row.id


def extra_channel(pg: sessionmaker[Session], catalog: dict[str, Any]) -> UUID:
    with pg.begin() as session:
        identity = catalog["identity"]
        row = Channel(tenant_id=identity.tenant_id, project_id=identity.project_id, node_id=catalog["node_id"], gpu_id=catalog["gpu_id"], name="Unbound shared GPU channel", base_url="http://127.0.0.1:18199", enabled=True, applied_revision=1, verified_revision=1)
        session.add(row)
        session.flush()
        return row.id


def pending_command(pg: sessionmaker[Session], identity: AdminIdentity, identifier: UUID, *, state: str = "pending") -> UUID:
    with pg.begin() as session:
        channel = session.get(Channel, identifier)
        assert channel is not None
        operation = ManagementOperation(actor_id=identity.user_id, command="probe_channel")
        session.add(operation)
        session.flush()
        session.add(WorkerCommand(operation_id=operation.id, channel_id=identifier, node_id=channel.node_id, command="probe_channel", state=state, lease_expires_at=utcnow() - timedelta(seconds=600), payload={"revision": 1, "PRIVATE": "SYNTHETIC-NOT-A-REAL-SECRET"}))
        return operation.id


def audit_counts(pg: sessionmaker[Session]) -> tuple[int, int]:
    with pg() as session:
        return (session.scalar(select(func.count()).select_from(AuditEvent)), session.scalar(select(func.count()).select_from(OutboxEvent)))


@pytest.mark.parametrize("kind", ["active_jobs", "open_attempts", "pending_commands", "published_models", "gpu_occupancy", "gpu_management_hold"])
def test_each_deletion_blocker_is_authoritative_and_does_not_change_resources(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str) -> None:
    identity = actor(pg)
    identifier = catalog["channel_ids"][0]
    if kind in ("active_jobs", "open_attempts", "gpu_occupancy"):
        JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "deletion-fixture")
        if kind != "active_jobs":
            leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
            claim = leases.claim(catalog["node_id"], catalog["boot_id"])
            assert claim is not None
            identifier = parse_id("ch", claim["channel"]["id"])
            if kind == "open_attempts":
                with pg.begin() as session:
                    session.get(Job, parse_id("job", claim["job_id"])).state = "failed"
                    session.get(Attempt, parse_id("att", claim["attempt_id"])).generation_released_at = utcnow()
                    session.delete(session.get(GpuOccupancy, catalog["gpu_id"]))
            else:
                identifier = extra_channel(pg, catalog)
                with pg.begin() as session:
                    session.get(GpuOccupancy, catalog["gpu_id"]).uncertain = True
    if kind == "pending_commands":
        pending_command(pg, identity, identifier, state="running")
    if kind == "gpu_management_hold":
        with pg.begin() as session:
            session.get(Gpu, catalog["gpu_id"]).management_hold = uuid4()
    if kind != "published_models":
        unpublish(pg)
    else:
        with pg.begin() as session:
            channel = session.get(Channel, identifier)
            channel.enabled = False
            channel.verified_revision = None
    admin = ChannelAdministration(pg, test_settings)
    before = audit_counts(pg)
    result = admin.deletion_check(identifier)
    validate("admin-channel-deletion", result)
    assert ChannelDeletionCheck.model_validate(result).model_dump() == result
    assert [item["kind"] for item in result["blockers"]] == [kind]
    assert result["can_delete"] is False and result["deleted"] is False
    assert "PRIVATE" not in str(result) and "SYNTHETIC-NOT-A-REAL-SECRET" not in str(result)
    with pytest.raises(DomainError) as rejected:
        admin.delete_channel(identity, identifier, 1, "blocked-delete")
    assert (rejected.value.code, rejected.value.status) == ("channel_in_use", 409)
    assert audit_counts(pg) == before
    with pg() as session:
        channel = session.get(Channel, identifier)
        assert channel.deleted_at is None and channel.revision == 1
        if kind == "gpu_occupancy":
            assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain is True
        if kind == "gpu_management_hold":
            assert session.get(Gpu, catalog["gpu_id"]).management_hold is not None


@pytest.mark.parametrize("state, blocked", [("pending", True), ("running", True), ("unknown_future_state", True), ("succeeded", False), ("failed", False)])
def test_command_lease_expiry_is_not_a_deletion_release(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, state: str, blocked: bool) -> None:
    identity = actor(pg)
    unpublish(pg)
    identifier = catalog["channel_ids"][0]
    pending_command(pg, identity, identifier, state=state)
    admin = ChannelAdministration(pg, test_settings)
    assert admin.deletion_check(identifier)["can_delete"] is not blocked
    if blocked:
        with pytest.raises(DomainError, match="渠道仍有"):
            admin.delete_channel(identity, identifier, 1, "expired-command")
    else:
        assert admin.delete_channel(identity, identifier, 1, "terminal-command")["deleted_at"] is not None


def test_mixed_blockers_are_distinct_stable_and_bounded_without_total_counts(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = actor(pg)
    identifier = catalog["channel_ids"][0]
    for _ in range(12):
        JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "bounded-blockers")
        pending_command(pg, identity, identifier)
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        for index in range(11):
            session.add(PublicModel(tenant_id=model.tenant_id, project_id=model.project_id, release_id=model.release_id, alias="deletion-bound-" + str(index), task_type=model.task_type, channel_ids=[public_id("ch", identifier)], defaults=model.defaults, published=True))
    unpublish(pg)
    admin = ChannelAdministration(pg, test_settings)
    first = admin.deletion_check(identifier)
    assert [item["kind"] for item in first["blockers"]] == ["active_jobs", "pending_commands", "published_models"]
    for item in first["blockers"]:
        assert len(item["resource_ids"]) == 10 and item["has_more"] is True
        assert item["resource_ids"] == sorted(set(item["resource_ids"]))
        assert set(item) == {"kind", "resource_ids", "has_more"}
    assert admin.deletion_check(identifier)["blockers"] == first["blockers"]


def test_success_is_logical_audited_once_and_keeps_physical_and_historical_resources(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = actor(pg)
    unpublish(pg)
    identifier = catalog["channel_ids"][0]
    with pg.begin() as session:
        channel = session.get(Channel, identifier)
        session.add(ChannelRevision(channel_id=identifier, revision=1, config={"base_url": channel.base_url, "encrypted_auth": "SYNTHETIC-CIPHERTEXT"}))
        session.get(Gpu, catalog["gpu_id"]).fence = 47
    admin = ChannelAdministration(pg, test_settings)
    before = audit_counts(pg)
    clear = admin.deletion_check(identifier)
    assert clear["can_delete"] is True and clear["blockers"] == []
    result = admin.delete_channel(identity, identifier, 1, "logical-delete")
    assert result["deleted_at"] is not None and result["revision"] == 2 and not result["enabled"] and result["draining"]
    assert admin.delete_channel(identity, identifier, 1, "repeat-logical-delete") == result
    after = admin.deletion_check(identifier)
    assert after["deleted"] and not after["can_delete"] and not after["blockers"]
    assert audit_counts(pg) == (before[0] + 1, before[1] + 1)
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]).fence == 47
        assert session.get(Node, catalog["node_id"]) is not None
        assert session.scalar(select(func.count()).select_from(ChannelRevision).where(ChannelRevision.channel_id == identifier)) == 1
        assert session.scalar(select(func.count()).select_from(WorkflowBinding).where(WorkflowBinding.channel_id == identifier)) == 1
        assert session.scalar(select(func.count()).select_from(PublicModel)) == 1


def test_read_is_repeatable_read_only_and_has_database_budgets(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import generation_infrastructure.admin_channels as module
    original = module.channel_deletion_blockers
    observed = []

    def inspect(session, channel, gpu):
        observed.append(tuple(session.scalar(text("SHOW " + name)) for name in ["transaction_read_only", "transaction_isolation", "statement_timeout", "lock_timeout"]))
        return original(session, channel, gpu)

    monkeypatch.setattr(module, "channel_deletion_blockers", inspect)
    before = audit_counts(pg)
    ChannelAdministration(pg, test_settings).deletion_check(catalog["channel_ids"][0])
    assert observed == [("on", "repeatable read", "5s", "1s")]
    assert audit_counts(pg) == before


@pytest.mark.parametrize("read", [True, False])
def test_actual_pg_lock_timeout_is_sanitized_and_rolls_back(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, read: bool) -> None:
    identity = actor(pg)
    unpublish(pg)
    admin = ChannelAdministration(pg, test_settings)
    with pg.begin() as blocker:
        if read:
            blocker.execute(text("LOCK TABLE channels IN ACCESS EXCLUSIVE MODE"))
        else:
            blocker.scalar(select(Gpu).where(Gpu.id == catalog["gpu_id"]).with_for_update())
        started = time.monotonic()
        with pytest.raises(DomainError) as rejected:
            if read:
                admin.deletion_check(catalog["channel_ids"][0])
            else:
                admin.delete_channel(identity, catalog["channel_ids"][0], 1, "locked-delete")
        assert 0.7 < time.monotonic() - started < 4
        assert (rejected.value.code, rejected.value.status) == (("channel_deletion_unavailable", 503) if read else ("channel_busy", 409))
        assert "SELECT" not in str(rejected.value) and "127.0.0.1" not in str(rejected.value)
    assert admin.deletion_check(catalog["channel_ids"][0])["can_delete"]


@pytest.mark.parametrize("role, status", [("administrator", 200), ("operator", 200), ("workflow_maintainer", 403), ("observer", 403)])
def test_deletion_check_roles_and_safe_http_shape(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str, status: int) -> None:
    identity = actor(pg, role)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        login = client.post("/admin/api/v1/session", headers={"Origin": test_settings.public_origin}, json={"username": identity.username, "password": PASSWORD})
        assert login.status_code == 200
        path = "/admin/api/v1/channels/" + public_id("ch", catalog["channel_ids"][0]) + "/deletion-check"
        result = client.get(path)
        assert result.status_code == status
        if status == 200:
            assert result.headers["etag"] == '"1"' and result.headers["cache-control"] == "no-store"
            validate("admin-channel-deletion", result.json())
            assert result.json()["blockers"][0]["kind"] == "published_models"
            assert client.get("/admin/api/v1/channels/ch_" + "f" * 32 + "/deletion-check").status_code == 404
            invalid = client.get("/admin/api/v1/channels/invalid/deletion-check")
            assert invalid.status_code == 404 and invalid.json()["error"]["code"] == "not_found"


def test_http_delete_keeps_csrf_preconditions_and_does_not_trust_prior_check(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = actor(pg)
    unpublish(pg)
    identifier = catalog["channel_ids"][0]
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        login = client.post("/admin/api/v1/session", headers={"Origin": test_settings.public_origin}, json={"username": identity.username, "password": PASSWORD}).json()
        path = "/admin/api/v1/channels/" + public_id("ch", identifier)
        headers = {"Origin": test_settings.public_origin, "X-CSRF-Token": login["csrf_token"], "Idempotency-Key": str(uuid4())}
        assert client.delete(path, params={"revision": 1}).status_code == 403
        assert client.delete(path, params={"revision": 1}, headers=headers).status_code == 428
        assert client.delete(path, params={"revision": 1}, headers={**headers, "If-Match": '"2"'}).status_code == 409
        assert client.get(path + "/deletion-check").json()["can_delete"]
        pending_command(pg, identity, identifier)
        denied = client.delete(path, params={"revision": 1}, headers={**headers, "If-Match": '"1"'})
        assert denied.status_code == 409 and denied.json()["error"]["code"] == "channel_in_use"
        assert client.get(path + "/deletion-check").json()["blockers"][0]["kind"] == "pending_commands"


@pytest.mark.parametrize("prefix", ["worker", "key", "gateway"])
def test_other_bearer_identities_cannot_read_deletion_constraints(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, prefix: str) -> None:
    identifier, secret = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        result = client.get("/admin/api/v1/channels/" + public_id("ch", catalog["channel_ids"][0]) + "/deletion-check", headers={"Authorization": "Bearer " + secret})
        assert result.status_code == 401 and identifier not in result.text and secret not in result.text


@contextmanager
def paused_sql(pg: sessionmaker[Session], prefix: str, fragment: str | tuple[str, ...]):
    reached, release = Event(), Event()
    engine = pg.kw["bind"]

    def before(conn, cursor, statement, parameters, context, executemany):
        fragments = (fragment,) if isinstance(fragment, str) else fragment
        if current_thread().name.startswith(prefix) and all(item in statement for item in fragments) and not reached.is_set():
            reached.set()
            assert release.wait(6), "Bounded deletion-race fixture was not released"

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield reached, release
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", before)


@contextmanager
def tracked_backend(pg: sessionmaker[Session], prefix: str):
    engine = pg.kw["bind"]
    pids = []
    reached = Event()

    def before(conn, cursor, statement, parameters, context, executemany):
        if current_thread().name.startswith(prefix) and not reached.is_set():
            pids.append(conn.connection.driver_connection.info.backend_pid)
            reached.set()

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield reached, pids
    finally:
        event.remove(engine, "before_cursor_execute", before)


def assert_waiting_on_pg_lock(pg: sessionmaker[Session], pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with pg() as session:
            if session.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid}):
                return
        time.sleep(0.02)
    raise AssertionError("Expected the competing native PostgreSQL transaction to wait on a lock")


@pytest.mark.parametrize("writer", ["publish", "command", "validation"])
def test_delete_wins_against_new_reference_writers(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, writer: str) -> None:
    identity = actor(pg)
    model_id = unpublish(pg)
    identifier = catalog["channel_ids"][0]
    with pg.begin() as session:
        session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier)).runtime_approval = {"test_only": True}
    admin = ChannelAdministration(pg, test_settings)

    def compete():
        if writer == "publish":
            CatalogAdministration(pg, test_settings).model_action(identity, model_id, 1, True, "publish-race")
        elif writer == "command":
            admin.channel_command(identity, identifier, "probe_channel", str(uuid4()), "command-race")
        else:
            ValidationJobs(pg, test_settings).create(identity, model_id, ValidationJob(channel_id=public_id("ch", identifier), request=JobCreate.model_validate(REQUEST)), str(uuid4()), "validation-race")

    with paused_sql(pg, "delete-first", "jobs.eligible_channels") as (reached, release), tracked_backend(pg, "reference-second") as (tracked, pids):
        with ThreadPoolExecutor(1, thread_name_prefix="delete-first") as first, ThreadPoolExecutor(1, thread_name_prefix="reference-second") as second:
            deletion = first.submit(admin.delete_channel, identity, identifier, 1, "delete-first")
            try:
                assert reached.wait(3)
                reference = second.submit(compete)
                assert tracked.wait(3)
                assert_waiting_on_pg_lock(pg, pids[0])
            finally:
                release.set()
            assert deletion.result(timeout=5)["deleted_at"] is not None
            with pytest.raises(DomainError) as rejected:
                reference.result(timeout=5)
            assert rejected.value.code == {"publish": "channel_not_verified", "command": "not_found", "validation": "validation_target_changed"}[writer]
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkerCommand)) == 0
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.get(PublicModel, model_id).published is False


@pytest.mark.parametrize("writer", ["publish", "command", "admission"])
def test_reference_wins_and_delete_rechecks_after_waiting(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, writer: str) -> None:
    identity = actor(pg)
    identifier = catalog["channel_ids"][0]
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        model.channel_ids = [public_id("ch", identifier)]
        model.published = writer == "admission"
        model_id = model.id
        session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier)).runtime_approval = {"test_only": True}
    admin = ChannelAdministration(pg, test_settings)

    def compete():
        if writer == "publish":
            return CatalogAdministration(pg, test_settings).model_action(identity, model_id, 1, True, "publisher-first")
        if writer == "command":
            return admin.channel_command(identity, identifier, "probe_channel", str(uuid4()), "command-first")
        return JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "admission-first")

    fragment = {"publish": "UPDATE public_models", "command": "INSERT INTO worker_commands", "admission": "INSERT INTO jobs"}[writer]
    with paused_sql(pg, "reference-first", fragment) as (reached, release), tracked_backend(pg, "delete-second") as (tracked, pids):
        with ThreadPoolExecutor(1, thread_name_prefix="reference-first") as first, ThreadPoolExecutor(1, thread_name_prefix="delete-second") as second:
            reference = first.submit(compete)
            try:
                assert reached.wait(3)
                if writer == "admission":
                    # Publication can end while an accepted request retains its
                    # locked route. Deletion must still see its committed Job.
                    unpublish(pg)
                deletion = second.submit(admin.delete_channel, identity, identifier, 1, "delete-second")
                assert tracked.wait(3)
                assert_waiting_on_pg_lock(pg, pids[0])
            finally:
                release.set()
            reference.result(timeout=5)
            with pytest.raises(DomainError) as rejected:
                deletion.result(timeout=5)
            assert rejected.value.code == "channel_in_use"
    result = admin.deletion_check(identifier)
    assert [item["kind"] for item in result["blockers"]] == [{"publish": "published_models", "command": "pending_commands", "admission": "active_jobs"}[writer]]


def test_receipt_and_delete_share_gpu_then_channel_order_and_preserve_new_revision(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = actor(pg)
    admin = ChannelAdministration(pg, test_settings)
    business = catalog["identity"]
    created = admin.create_channel(identity, ChannelCreate(tenant_id=public_id("ten", business.tenant_id), project_id=public_id("prj", business.project_id), node_id=public_id("node", catalog["node_id"]), gpu_id=public_id("gpu", catalog["gpu_id"]), name="Receipt race", base_url="http://127.0.0.1:18198"), str(uuid4()), "receipt-create")
    identifier = parse_id("ch", created["channel"]["id"])
    commands = CommandRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    claim = commands.claim(catalog["node_id"], catalog["boot_id"])
    assert claim is not None

    def receipt():
        return commands.receipt(catalog["node_id"], parse_id("cmd", claim["command_id"]), catalog["boot_id"], claim["command_digest"], {"success": True, "applied_revision": 1})

    with paused_sql(pg, "receipt-first", ("FROM channels", "FOR UPDATE")) as (reached, release), tracked_backend(pg, "delete-after-receipt") as (tracked, pids):
        with ThreadPoolExecutor(1, thread_name_prefix="receipt-first") as first, ThreadPoolExecutor(1, thread_name_prefix="delete-after-receipt") as second:
            completed = first.submit(receipt)
            try:
                assert reached.wait(3)
                deletion = second.submit(admin.delete_channel, identity, identifier, 1, "delete-after-receipt")
                assert tracked.wait(3)
                assert_waiting_on_pg_lock(pg, pids[0])
            finally:
                release.set()
            assert completed.result(timeout=5)["accepted"]
            with pytest.raises(DomainError) as rejected:
                deletion.result(timeout=5)
            assert rejected.value.code == "config_revision_conflict"
    current = admin.deletion_check(identifier)
    assert current["can_delete"] and current["revision"] == 2
    assert admin.delete_channel(identity, identifier, 2, "explicit-current-delete")["deleted_at"] is not None
