import importlib.util
import multiprocessing
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import capacity_sample
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.catalog import ModelPatch
from generation_contracts.device_pool import DeviceRouting
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.device_pools import target_for_job
from generation_infrastructure.models import (
    ApiKey,
    Attempt,
    Channel,
    DevicePool,
    DevicePoolMember,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    PublicModel,
    WorkflowBinding,
    WorkflowRelease,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.test_admin_controls import PASSWORD, admin_context
from tests.integration.test_dispatch_transactions import (
    create_job,
    finish_preparation,
    leases,
    spawn_claim,
)

pytestmark = pytest.mark.integration


def pool_catalog(pg, catalog, weights=(1, 1, 1)):
    """Synthetic independent physical resources; never real fleet acceptance."""
    identity = catalog["identity"]
    devices = [{"node_id": catalog["node_id"], "boot_id": catalog["boot_id"], "gpu_id": catalog["gpu_id"], "channel_ids": catalog["channel_ids"]}]
    with pg.begin() as session:
        for index in range(1, len(weights)):
            node = Node(name=f"Synthetic device {index}", credential_identifier=uuid4().hex, credential_verifier="0" * 64, current_boot_id=uuid4(), observed_at=utcnow())
            session.add(node)
            session.flush()
            gpu = Gpu(node_id=node.id, hardware_id=f"SYNTHETIC-POOL-{index}", name=f"Synthetic GPU {index}")
            session.add(gpu)
            session.flush()
            channel = Channel(tenant_id=identity.tenant_id, project_id=identity.project_id, node_id=node.id, gpu_id=gpu.id, name=f"Synthetic route {index}", base_url=f"http://127.0.0.1:{18200+index}", enabled=True, desired_revision=1, applied_revision=1, verified_revision=1)
            session.add(channel)
            session.flush()
            session.add(WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel.id, release_id=catalog["release_id"], desired_digest=catalog["release_digest"], applied_digest=catalog["release_digest"], verified_digest=catalog["release_digest"]))
            devices.append({"node_id": node.id, "boot_id": node.current_boot_id, "gpu_id": gpu.id, "channel_ids": [channel.id]})
        model = session.scalar(select(PublicModel))
        model.channel_ids = [public_id("ch", identifier) for device in devices for identifier in device["channel_ids"]]
        model.routing = {"policy": "balanced_ready", "weights": {public_id("gpu", device["gpu_id"]): weight for device, weight in zip(devices, weights, strict=True)}}
    refresh_devices(pg, devices)
    return devices


def refresh_devices(pg, devices):
    with pg.begin() as session:
        for device in devices:
            node = session.get(Node, device["node_id"])
            node.observed_at = utcnow()
            node.inventory = {"capacity_sequence": 1, "capacity": {"boot_id": str(node.current_boot_id), "observed_at_ms": int(utcnow().timestamp() * 1000), "sample": capacity_sample()}}
            for identifier in device["channel_ids"]:
                channel = session.get(Channel, identifier)
                channel.observation = {"queue": {"reachable": True, "queue_running": 0, "queue_pending": 0}, "queue_observed_at": utcnow().timestamp(), "queue_revision": channel.desired_revision}


def choice(pg, identifier):
    with pg() as session:
        selected = target_for_job(session, session.get(Job, parse_id("job", identifier)), allow_synthetic_catalog=True)
        return selected.gpu_id if selected else None


@pytest.mark.parametrize("weights,rounds", [((1, 1, 1), 100), ((5, 3, 2), 20), ((1,) * 20, 20)])
def test_global_assignment_persists_despite_wrong_worker_polling(pg, catalog, test_settings, weights, rounds):
    devices = pool_catalog(pg, catalog, weights)
    counts = Counter()
    for _ in range(rounds):
        refresh_devices(pg, devices)
        identifier = create_job(pg, catalog)
        selected = choice(pg, identifier)
        target = next(device for device in devices if device["gpu_id"] == selected)
        # A faster unselected Worker cannot steal or advance the cursor.
        wrong = next(device for device in devices if device != target)
        repository = leases(pg, test_settings)
        assert repository.claim(wrong["node_id"], wrong["boot_id"]) is None
        claim = repository.claim(target["node_id"], target["boot_id"])
        assert claim and claim["job_id"] == identifier
        counts[selected] += 1
        finish_preparation(repository, target, claim)
    with pg() as session:
        pool = session.scalar(select(DevicePool))
        members = list(session.scalars(select(DevicePoolMember)))
        assert pool.sequence == rounds
        assert len(members) == len(weights)  # two initial channels share one GPU
        assert {row.gpu_id: row.assigned_count for row in members} == dict(counts)
        assert sum(row.assigned_count for row in members) == rounds
    if len(set(weights)) == 1:
        assert max(counts.values()) - min(counts.values()) <= 1
    else:
        assert [counts[device["gpu_id"]] for device in devices] == [rounds * weight // sum(weights) for weight in weights]


def test_concurrent_claim_replay_and_rollback_charge_once(pg, catalog, test_settings, monkeypatch):
    devices = pool_catalog(pg, catalog)
    identifier = create_job(pg, catalog)
    target = next(device for device in devices if device["gpu_id"] == choice(pg, identifier))
    repository = leases(pg, test_settings)
    original = repository._claim_projection

    def crash(*args):
        raise RuntimeError("synthetic_after_accounting")

    monkeypatch.setattr(repository, "_claim_projection", crash)
    with pytest.raises(RuntimeError, match="synthetic_after_accounting"):
        repository.claim(target["node_id"], target["boot_id"], "rollback")
    with pg() as session:
        assert session.scalar(select(DevicePool.sequence)) == 0
        assert session.scalar(select(func.count()).select_from(Attempt)) == 0
        assert session.scalar(select(func.count()).select_from(GpuOccupancy)) == 0
        assert all(row.credit == row.assigned_count == row.last_sequence == 0 for row in session.scalars(select(DevicePoolMember)))
    monkeypatch.setattr(repository, "_claim_projection", original)
    refresh_devices(pg, devices)
    with ThreadPoolExecutor(6) as executor:
        results = list(executor.map(lambda _: leases(pg, test_settings).claim(target["node_id"], target["boot_id"], "same-request"), range(6)))
    assert len({result["attempt_id"] for result in results}) == 1
    with pg() as session:
        assert session.scalar(select(DevicePool.sequence)) == 1
        assert session.scalar(select(func.count()).select_from(Attempt)) == 1
        assert session.scalar(select(func.sum(DevicePoolMember.assigned_count))) == 1


@pytest.mark.parametrize("gate", ["busy", "draining", "disabled", "hold", "stale", "unreachable", "external_queue", "queue_unknown", "stale_queue", "bad_revision", "unapproved", "capacity"])
def test_ineligible_devices_never_receive_global_offer(pg, catalog, test_settings, gate):
    devices = pool_catalog(pg, catalog)
    identifier = create_job(pg, catalog)
    blocked = next(device for device in devices if device["gpu_id"] == choice(pg, identifier))
    with pg.begin() as session:
        node, gpu = session.get(Node, blocked["node_id"]), session.get(Gpu, blocked["gpu_id"])
        if gate == "disabled":
            node.enabled = False
        elif gate == "hold":
            gpu.management_hold = uuid4()
        elif gate == "stale":
            node.observed_at = utcnow() - timedelta(seconds=16)
        elif gate == "capacity":
            node.inventory = {}
        for channel_id in blocked["channel_ids"]:
            channel = session.get(Channel, channel_id)
            observation = deepcopy(channel.observation)
            if gate == "draining":
                channel.draining = True
            elif gate == "unreachable":
                observation["queue"]["reachable"] = False
            elif gate == "external_queue":
                observation["queue"]["queue_pending"] = 1
            elif gate == "queue_unknown":
                observation = {}
            elif gate == "stale_queue":
                observation["queue_observed_at"] -= 16
            elif gate == "bad_revision":
                channel.desired_revision += 1
            elif gate == "unapproved":
                binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id, WorkflowBinding.release_id == catalog["release_id"]))
                binding.verified_digest = "0" * 64
            channel.observation = observation
    if gate == "busy":
        claim = leases(pg, test_settings).claim(blocked["node_id"], blocked["boot_id"])
        identifier = create_job(pg, catalog)
        assert claim
    assert choice(pg, identifier) in {device["gpu_id"] for device in devices if device != blocked}
    with pg() as session:
        assert session.scalar(select(DevicePool.sequence)) == (1 if gate == "busy" else 0)


def test_pool_versions_and_database_guards_preserve_original_route(pg, catalog):
    devices = pool_catalog(pg, catalog)
    before = create_job(pg, catalog)
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        original = session.get(Job, parse_id("job", before)).device_pool_id
        model.routing = {"policy": "balanced_ready", "weights": {public_id("gpu", devices[0]["gpu_id"]): 3}}
    after = create_job(pg, catalog)
    with pg() as session:
        assert session.get(Job, parse_id("job", before)).device_pool_id == original
        assert session.get(Job, parse_id("job", after)).device_pool_id != original
    for statement in ["UPDATE device_pools SET configuration='{}'::jsonb", "UPDATE jobs SET device_pool_id=NULL WHERE device_pool_id IS NOT NULL", "UPDATE device_pool_members SET weight=2"]:
        with pytest.raises(IntegrityError), pg.begin() as session:
            session.execute(text(statement))


def test_legacy_strategy_and_admin_invalid_weight_rollback(pg, catalog, test_settings):
    legacy = create_job(pg, catalog)
    actor, _, _ = admin_context(pg, test_settings)
    with pg() as session:
        model = session.scalar(select(PublicModel))
        model_id, revision = model.id, model.revision
        patch = {"release_id": public_id("wfr", model.release_id), "channel_ids": model.channel_ids, "defaults": model.defaults}
        assert session.get(Job, parse_id("job", legacy)).device_pool_id is None
    with pg.begin() as session:
        source = session.get(WorkflowRelease, catalog["release_id"])
        manifest = {**source.manifest, "presets": [{"size": "1024x1024", "quality": "standard"}]}
        release = WorkflowRelease(tenant_id=source.tenant_id, project_id=source.project_id, workflow_id=source.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        patch["release_id"] = public_id("wfr", release.id)
    service = CatalogAdministration(pg, test_settings)
    with pytest.raises(DomainError, match="权重"):
        service.patch_model(actor, model_id, ModelPatch(**patch, expected_revision=revision, routing=DeviceRouting(policy="balanced_ready", weights={"gpu_" + uuid4().hex: 2})), "synthetic-pool")
    assert service.device_pool(model_id)["policy"] == "first_ready"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(DevicePool)) == 0


@pytest.mark.parametrize("role", ["administrator", "operator", "workflow_maintainer", "observer"])
def test_http_pool_is_read_only_admin_scope(pg, catalog, test_settings, role):
    pool_catalog(pg, catalog)
    actor, _, _ = admin_context(pg, test_settings, role=role)
    with pg() as session:
        model = session.scalar(select(PublicModel))
        identifier = public_id("mdl", model.id)
    path = f"/admin/api/v1/public-models/{identifier}/device-pool"
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get(path).status_code == 401
        assert client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD}, headers={"Origin": test_settings.public_origin}).status_code == 200
        response = client.get(path)
        assert response.status_code == 200
        result = response.json()
        assert result["sequence"] == 0 and len(result["members"]) == 3
        assert "input" not in result and "base_url" not in result
        assert client.get("/admin/api/v1/public-models/mdl_" + uuid4().hex + "/device-pool").status_code == 404
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(DevicePool)) == 0


def migration():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/04d137a00001_device_pools.py"
    spec = importlib.util.spec_from_file_location("device_pool_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_migration_preserves_legacy_job_and_rejects_used_pool_downgrade(pg, catalog):
    identifier = create_job(pg, catalog)
    with pg.begin() as session:
        before = session.execute(text("SELECT id,state,request_fingerprint,execution FROM jobs")).one()
        with Operations.context(MigrationContext.configure(session.connection())):
            migration().downgrade()
            migration().upgrade()
        assert session.execute(text("SELECT id,state,request_fingerprint,execution FROM jobs")).one() == before
        assert session.get(Job, parse_id("job", identifier)).device_pool_id is None
        assert session.scalar(select(PublicModel.routing)) == {"policy": "first_ready", "weights": {}}
    pool_catalog(pg, catalog)
    create_job(pg, catalog)
    with pytest.raises(DBAPIError, match="forward recovery"), pg.begin() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            migration().downgrade()


def test_key_subset_and_offline_return_never_widen_original_authorization(pg, catalog, test_settings):
    devices = pool_catalog(pg, catalog)
    allowed = devices[-1]
    with pg.begin() as session:
        key = session.get(ApiKey, catalog["identity"].key_id)
        policy = deepcopy(key.policy)
        policy["channels"] = {"mode": "selected", "selected_ids": [public_id("ch", allowed["channel_ids"][0])]}
        key.policy = policy
    identifier = create_job(pg, catalog)
    assert choice(pg, identifier) == allowed["gpu_id"]
    for device in devices[:-1]:
        assert leases(pg, test_settings).claim(device["node_id"], device["boot_id"]) is None
    with pg.begin() as session:
        node = session.get(Node, allowed["node_id"])
        node.observed_at = utcnow() - timedelta(seconds=16)
    assert choice(pg, identifier) is None
    refresh_devices(pg, devices)
    assert choice(pg, identifier) == allowed["gpu_id"]
    with pg() as session:
        assert session.get(Job, parse_id("job", identifier)).eligible_channels == [public_id("ch", allowed["channel_ids"][0])]
        assert session.scalar(select(DevicePool.sequence)) == 0


def test_native_processes_accept_one_pool_assignment(pg, catalog, test_settings):
    devices = pool_catalog(pg, catalog)
    identifier = create_job(pg, catalog)
    target = next(device for device in devices if device['gpu_id'] == choice(pg, identifier))
    context = multiprocessing.get_context('spawn')
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(target=spawn_claim, args=(test_settings.database_url.get_secret_value(), test_settings.encryption_key.get_secret_value(), target['node_id'], target['boot_id'], ready, start, results)) for _ in range(4)]
    try:
        for process in processes:
            process.start()
        assert all(ready.get(timeout=60) is True for _ in processes)
        refresh_devices(pg, devices)
        start.set()
        outcomes = [results.get(timeout=60) for _ in processes]
        for process in processes:
            process.join(15)
        assert all('error_type' not in item for item in outcomes), outcomes
        assert sum(item['passed'] for item in outcomes) == 1
        assert {item['job_id'] for item in outcomes if item['passed']} == {identifier}
        assert all(process.exitcode == 0 for process in processes)
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(10)
        for queue in (ready, results):
            queue.close()
            queue.join_thread()
    with pg() as session:
        assert session.scalar(select(DevicePool.sequence)) == 1
        assert session.scalar(select(func.sum(DevicePoolMember.assigned_count))) == 1
        assert session.scalar(select(func.count()).select_from(Attempt)) == 1
