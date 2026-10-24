from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import COOKIE, create_app
from fastapi.testclient import TestClient
from generation_application.ports import AdminIdentity
from generation_contracts.catalog import ValidationJob
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_contracts.spec_isolation import SpecIsolationCreate, SpecResume
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_alerts import AlertQueries
from generation_infrastructure.admin_spec_isolation import SpecIsolationAdministration
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminUser,
    Artifact,
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    Node,
    ObjectRecord,
    PublicModel,
    SpecIsolation,
)
from generation_infrastructure.outputs import OutputRepository
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from generation_infrastructure.spec_isolation import (
    confirmed_failure,
    prune_inactive,
    verified_success,
)
from generation_infrastructure.storage import LocalDevelopmentStore
from generation_infrastructure.validation_jobs import ValidationJobs
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-spec-isolation-password"
REQUEST = {
    "model": "synthetic-image",
    "task_type": "text_to_image",
    "input": {"prompt": "Synthetic private isolation sample"},
}


@pytest.fixture
def actor(pg: sessionmaker[Session]) -> AdminIdentity:
    with pg.begin() as session:
        user = AdminUser(
            username="spec-admin", password_hash=password_hash(PASSWORD), role="administrator"
        )
        session.add(user)
        session.flush()
        return AdminIdentity(user.id, user.username, user.role, uuid4())


def leases(pg: sessionmaker[Session], settings: Settings) -> LeaseRepository:
    return LeaseRepository(
        pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True
    )


def event(
    catalog: dict[str, Any], claim: dict[str, Any], seq: int, kind: str, payload: dict[str, Any]
) -> WorkerEvent:
    return WorkerEvent(
        protocol_version="0.4.0",
        event_id=str(uuid4()),
        node_id=claim["node_id"],
        boot_id=claim["boot_id"],
        job_id=claim["job_id"],
        attempt_id=claim["attempt_id"],
        fencing_token=claim["fencing_token"],
        journal_seq=seq,
        observed_at=int(utcnow().timestamp()),
        type=kind,
        payload=payload,
    )


def running(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    settings: Settings,
    *,
    validation: bool = False,
    actor: AdminIdentity | None = None,
    parameters: dict[str, Any] | None = None,
) -> tuple[LeaseRepository, dict[str, Any]]:
    request = {**REQUEST, "parameters": parameters or {}}
    if validation:
        assert actor
        with pg() as session:
            model = session.scalar(select(PublicModel))
            model_id = model.id
        ValidationJobs(pg, settings).create(
            actor,
            model_id,
            ValidationJob.model_validate(
                {"channel_id": public_id("ch", catalog["channel_ids"][0]), "request": request}
            ),
            str(uuid4()),
            "test",
        )
    else:
        JobRepository(pg).create(catalog["identity"], request, str(uuid4()), "test")
    repo = leases(pg, settings)
    from conftest import capacity_sample

    with pg() as session:
        sequence = session.get(Node, catalog["node_id"]).inventory.get("capacity_sequence", 0) + 1
    repo.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=sequence))
    claim = repo.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    for seq, (kind, payload) in enumerate(
        [
            ("preparing", {"execution_digest": claim["execution_digest"]}),
            (
                "submit_intent",
                {
                    "association_id": str(uuid4()),
                    "workflow_digest": claim["workflow_digest"],
                    "journal_committed": True,
                },
            ),
            ("submitted", {"prompt_id": "synthetic-prompt"}),
        ],
        1,
    ):
        repo.event(catalog["node_id"], event(catalog, claim, seq, kind, payload))
    return repo, claim


def failure(
    repo: LeaseRepository,
    catalog: dict[str, Any],
    claim: dict[str, Any],
    code: str = "comfy_oom",
    *,
    seq: int = 4,
    stopped: bool = True,
) -> WorkerEvent:
    value = event(
        catalog,
        claim,
        seq,
        "execution_failed",
        {"error_code": code, "execution_stop_confirmed": stopped, "evidence_digest": "d" * 64},
    )
    repo.event(catalog["node_id"], value)
    return value


def complete(
    pg: sessionmaker[Session],
    settings: Settings,
    repo: LeaseRepository,
    catalog: dict[str, Any],
    claim: dict[str, Any],
    tmp_path: Path,
) -> None:
    path = tmp_path / (str(uuid4()) + ".png")
    Image.new("RGB", (1024, 1024), "navy").save(path)
    spec = {
        "position": 0,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "media_type": "image/png",
    }
    repo.event(
        catalog["node_id"],
        event(
            catalog,
            claim,
            4,
            "outputs_collected",
            {"manifest_digest": fingerprint([spec]), "output_count": 1, "files_stable": True},
        ),
    )
    repo.event(
        catalog["node_id"],
        event(
            catalog,
            claim,
            5,
            "gpu_released",
            {
                "gpu_id": claim["channel"]["gpu_id"],
                "compute_stopped": True,
                "gpu_encoding_stopped": True,
                "evidence_digest": "e" * 64,
            },
        ),
    )
    outputs = OutputRepository(
        pg, LocalDevelopmentStore(settings.local_storage_root), repo, tmp_path / "spool"
    )
    aid = parse_id("att", claim["attempt_id"])
    auth = {
        "protocol_version": "0.4.0",
        "boot_id": claim["boot_id"],
        "fencing_token": claim["fencing_token"],
    }
    plan = outputs.authorize(catalog["node_id"], aid, UploadAuthorization(**auth, outputs=[spec]))
    outputs.accept(
        catalog["node_id"],
        parse_id("upl", plan["uploads"][0]["id"]),
        catalog["boot_id"],
        claim["fencing_token"],
        path,
    )
    result = Complete(
        **auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"]
    )
    outputs.complete(catalog["node_id"], aid, result)
    outputs.complete(catalog["node_id"], aid, result)


def isolate_job(
    pg: sessionmaker[Session], settings: Settings, actor: AdminIdentity, claim: dict[str, Any]
) -> dict[str, Any]:
    with pg() as session:
        revision = session.get(Job, parse_id("job", claim["job_id"])).revision
    return SpecIsolationAdministration(pg, settings).create(
        actor,
        SpecIsolationCreate(
            job_id=claim["job_id"], expected_revision=revision, evidence_id="incident-synthetic"
        ),
        str(uuid4()),
        "test",
    )


def current(pg: sessionmaker[Session]) -> SpecIsolation:
    with pg() as session:
        return session.scalar(select(SpecIsolation))


def test_two_independent_failures_dedupe_persist_and_block_both_channels(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    repo, first = running(pg, catalog, test_settings)
    duplicate = failure(repo, catalog, first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(
            value["accepted"]
            for value in pool.map(lambda _: repo.event(catalog["node_id"], duplicate), range(2))
        )
    assert current(pg).failure_count == 1 and not current(pg).is_active
    repo, second = running(pg, catalog, test_settings, parameters={"seed": 123})
    failure(repo, catalog, second, "comfy_output_invalid")
    row = current(pg)
    assert row.failure_count == 2 and row.is_active
    JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "test")
    assert leases(pg, test_settings).claim(catalog["node_id"], catalog["boot_id"]) is None
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]) is None
        assert session.scalar(select(func.count()).select_from(SpecIsolation)) == 1
    alerts = AlertQueries(pg, test_settings).snapshot(kind="spec_isolation")
    assert alerts["total"] == 1 and alerts["data"][0]["resource_id"] == public_id("iso", row.id)


@pytest.mark.parametrize(
    "code,stopped",
    [("comfy_execution_failed", True), ("preparation_failed", True), ("comfy_oom", False)],
)
def test_generic_preparation_and_unknown_failures_never_count_or_free_unknown_gpu(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    code: str,
    stopped: bool,
) -> None:
    repo, claim = running(pg, catalog, test_settings)
    failure(repo, catalog, claim, code, stopped=stopped)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(SpecIsolation)) == 0
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        assert (occupancy is not None) is not stopped
        if not stopped:
            assert occupancy.uncertain


def test_manual_isolation_keeps_unknown_gpu_hold_replay_and_renewal(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
) -> None:
    repo, claim = running(pg, catalog, test_settings)
    with pg.begin() as session:
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        occupancy.uncertain = True
        session.get(Gpu, catalog["gpu_id"]).management_hold = uuid4()
    result = isolate_job(pg, test_settings, actor, claim)
    assert result["is_active"]
    assert repo.renew(
        catalog["node_id"],
        catalog["boot_id"],
        parse_id("att", claim["attempt_id"]),
        claim["fencing_token"],
    )
    with pg() as session:
        assert session.get(GpuOccupancy, catalog["gpu_id"]).uncertain
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is not None


def test_verified_validation_bypasses_isolation_but_never_auto_resumes(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
    tmp_path: Path,
) -> None:
    repo, source = running(pg, catalog, test_settings)
    failure(repo, catalog, source)
    isolate_job(pg, test_settings, actor, source)
    repo, validation = running(pg, catalog, test_settings, validation=True, actor=actor)
    complete(pg, test_settings, repo, catalog, validation, tmp_path)
    row = current(pg)
    assert row.is_active and row.last_success_generation_at is not None
    admin = SpecIsolationAdministration(pg, test_settings)
    data = SpecResume(
        action="resume", expected_revision=row.revision, validation_job_id=validation["job_id"]
    )
    token = str(uuid4())
    resumed = admin.action(actor, row.id, data, token, "test")
    assert not resumed["is_active"] and resumed["failure_count"] == 0
    assert admin.action(actor, row.id, data, token, "test") == resumed
    repo, next_claim = running(pg, catalog, test_settings)
    assert not current(pg).is_active
    failure(repo, catalog, next_claim)
    assert current(pg).failure_count == 1 and not current(pg).is_active


@pytest.mark.parametrize(
    "change",
    [
        "ordinary",
        "wrong_spec",
        "old_boot",
        "channel_revision",
        "expired",
        "before_isolation",
        "missing_objects",
        "missing_manifest",
        "missing_stop",
        "wrong_gpu",
    ],
)
def test_resume_rejects_invalid_validation_proof(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
    tmp_path: Path,
    change: str,
) -> None:
    repo, source = running(pg, catalog, test_settings)
    failure(repo, catalog, source)
    isolate_job(pg, test_settings, actor, source)
    repo, validation = running(pg, catalog, test_settings, validation=True, actor=actor)
    complete(pg, test_settings, repo, catalog, validation, tmp_path)
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", validation["job_id"]))
        attempt = session.get(Attempt, parse_id("att", validation["attempt_id"]))
        row = session.scalar(select(SpecIsolation))
        if change == "ordinary":
            job.is_validation = False
        elif change == "wrong_spec":
            job.execution = {
                **job.execution,
                "parameters": {**job.execution["parameters"], "quality": "high"},
            }
        elif change == "old_boot":
            session.get(Node, catalog["node_id"]).current_boot_id = uuid4()
        elif change == "channel_revision":
            session.get(Channel, attempt.channel_id).desired_revision += 1
        elif change == "expired":
            job.terminal_at = utcnow() - timedelta(hours=25)
        elif change == "before_isolation":
            job.created_at = row.isolated_at - timedelta(seconds=1)
        elif change == "missing_manifest":
            job.result_manifest = None
        elif change == "missing_stop":
            attempt.generation_released_at = None
        elif change == "wrong_gpu":
            other = Gpu(node_id=catalog["node_id"], hardware_id="OTHER-GPU", name="Synthetic other")
            session.add(other)
            session.flush()
            attempt.gpu_id = other.id
        else:
            artifact = session.scalar(select(Artifact).where(Artifact.job_id == job.id))
            session.get(ObjectRecord, artifact.object_id).verified_at = None
        revision, identifier = row.revision, row.id
    with pytest.raises(DomainError) as exc:
        SpecIsolationAdministration(pg, test_settings).action(
            actor,
            identifier,
            SpecResume(
                action="resume", expected_revision=revision, validation_job_id=validation["job_id"]
            ),
            str(uuid4()),
            "test",
        )
    assert exc.value.code == "validation_proof_rejected"
    assert current(pg).is_active


def test_delayed_outcomes_do_not_override_newer_success_or_failure(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path
) -> None:
    repo, first = running(pg, catalog, test_settings)
    complete(pg, test_settings, repo, catalog, first, tmp_path)
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", first["job_id"]))
        attempt = session.get(Attempt, parse_id("att", first["attempt_id"]))
        session.execute(select(Gpu).where(Gpu.id == attempt.gpu_id).with_for_update()).scalar_one()
        job.state, job.error_code = "failed", "comfy_output_invalid"
        attempt.generation_released_at -= timedelta(seconds=1)
        confirmed_failure(session, job, attempt, "late-failure")
    assert current(pg).failure_count == 0
    repo, second = running(pg, catalog, test_settings)
    failure(repo, catalog, second)
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", first["job_id"]))
        attempt = session.get(Attempt, parse_id("att", first["attempt_id"]))
        session.execute(select(Gpu).where(Gpu.id == attempt.gpu_id).with_for_update()).scalar_one()
        job.state = "succeeded"
        # A success newer than the previous success, but older than latest failure.
        attempt.generation_released_at = current(pg).last_failure_at - timedelta(microseconds=1)
        verified_success(session, job, attempt, "late-success")
    assert current(pg).failure_count == 1


def test_failure_window_and_other_gpu_spec_are_independent(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    repo, first = running(pg, catalog, test_settings)
    failure(repo, catalog, first)
    with pg.begin() as session:
        session.scalar(select(SpecIsolation)).last_failure_at -= timedelta(seconds=601)
    repo, second = running(pg, catalog, test_settings)
    failure(repo, catalog, second)
    assert current(pg).failure_count == 1 and not current(pg).is_active
    repo, third = running(pg, catalog, test_settings, parameters={"size": "512x512"})
    failure(repo, catalog, third)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(SpecIsolation)) == 2


def test_more_than_two_hundred_isolated_jobs_do_not_hide_healthy_spec(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
) -> None:
    repo, first = running(pg, catalog, test_settings)
    failure(repo, catalog, first)
    isolate_job(pg, test_settings, actor, first)
    from generation_infrastructure.models import ApiKey, Project, Tenant

    with pg.begin() as session:
        for model in (Tenant, Project):
            row = session.scalar(select(model))
            row.limits = {name: {"mode": "bounded", "value": 10000} for name in row.limits}
        key = session.get(ApiKey, catalog["identity"].key_id)
        key.policy = {
            **key.policy,
            "limits": {name: {"mode": "bounded", "value": 10000} for name in key.policy["limits"]},
        }
    # Persist real queued jobs under the normal admission cap and accounting.
    for _ in range(201):
        JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "test")
    healthy = JobRepository(pg).create(
        catalog["identity"], {**REQUEST, "parameters": {"size": "512x512"}}, str(uuid4()), "test"
    )
    from conftest import capacity_sample

    repo.heartbeat(catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=1000))
    from threading import Event, Thread

    done = Event()

    def heartbeat() -> None:
        sequence = 1001
        while not done.wait(1):
            repo.heartbeat(
                catalog["node_id"], catalog["boot_id"], [], capacity_sample(sequence=sequence)
            )
            sequence += 1

    thread = Thread(target=heartbeat)
    thread.start()
    try:
        claim = repo.claim(catalog["node_id"], catalog["boot_id"])
    finally:
        done.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert claim and claim["job_id"] == healthy["id"]


def test_manual_isolation_and_claim_serialize_on_gpu_without_losing_occupancy(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
) -> None:
    repo, source = running(pg, catalog, test_settings)
    failure(repo, catalog, source)
    queued = JobRepository(pg).create(catalog["identity"], REQUEST, str(uuid4()), "test")
    with ThreadPoolExecutor(max_workers=2) as pool:
        manual = pool.submit(isolate_job, pg, test_settings, actor, source)
        claiming = pool.submit(repo.claim, catalog["node_id"], catalog["boot_id"])
        assert manual.result()["is_active"]
        claim = claiming.result()
    with pg() as session:
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        if claim:
            assert claim["job_id"] == queued["id"] and occupancy.attempt_id == parse_id(
                "att", claim["attempt_id"]
            )
        else:
            assert occupancy is None
    assert repo.claim(catalog["node_id"], catalog["boot_id"]) is None


def test_prune_never_removes_active_isolation_and_keeps_bounded_cleanup(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
) -> None:
    repo, claim = running(pg, catalog, test_settings)
    failure(repo, catalog, claim)
    isolate_job(pg, test_settings, actor, claim)
    with pg.begin() as session:
        session.scalar(select(SpecIsolation)).updated_at = utcnow() - timedelta(days=31)
    assert prune_inactive(pg) == 0 and current(pg).is_active
    with pg.begin() as session:
        session.scalar(select(SpecIsolation)).is_active = False
    assert prune_inactive(pg) == 1


@pytest.mark.parametrize("change", ["gpu", "release"])
def test_isolation_never_spills_into_other_gpu_or_release(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, change: str
) -> None:
    from generation_infrastructure.models import WorkflowBinding, WorkflowRelease

    for _ in range(2):
        repo, claim = running(pg, catalog, test_settings)
        failure(repo, catalog, claim)
    original = current(pg)
    assert original.is_active
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        if change == "gpu":
            gpu = Gpu(
                node_id=catalog["node_id"], hardware_id="HEALTHY-SYNTHETIC-GPU", name="Second GPU"
            )
            session.add(gpu)
            session.flush()
            channel = Channel(
                tenant_id=model.tenant_id,
                project_id=model.project_id,
                node_id=catalog["node_id"],
                gpu_id=gpu.id,
                name="Other GPU",
                base_url="http://127.0.0.1:19999",
                enabled=True,
                desired_revision=1,
                applied_revision=1,
                verified_revision=1,
            )
            session.add(channel)
            session.flush()
            digest = catalog["release_digest"]
            session.add(
                WorkflowBinding(
                    tenant_id=model.tenant_id,
                    project_id=model.project_id,
                    channel_id=channel.id,
                    release_id=model.release_id,
                    desired_digest=digest,
                    applied_digest=digest,
                    verified_digest=digest,
                )
            )
            model.channel_ids = [*model.channel_ids, public_id("ch", channel.id)]
        else:
            old = session.get(WorkflowRelease, model.release_id)
            manifest = {**old.manifest, "synthetic_version": 2}
            release = WorkflowRelease(
                tenant_id=model.tenant_id,
                project_id=model.project_id,
                workflow_id=old.workflow_id,
                version=2,
                digest=fingerprint(manifest),
                manifest=manifest,
                validated=True,
                validation={"test_only": True},
            )
            session.add(release)
            session.flush()
            model.release_id = release.id
            for channel_id in catalog["channel_ids"]:
                session.add(
                    WorkflowBinding(
                        tenant_id=model.tenant_id,
                        project_id=model.project_id,
                        channel_id=channel_id,
                        release_id=release.id,
                        desired_digest=release.digest,
                        applied_digest=release.digest,
                        verified_digest=release.digest,
                    )
                )
    repo, other = running(pg, catalog, test_settings)
    failure(repo, catalog, other)
    with pg() as session:
        rows = list(session.scalars(select(SpecIsolation)))
        assert len(rows) == 2
        assert session.get(SpecIsolation, original.id).is_active
        assert next(row for row in rows if row.id != original.id).failure_count == 1


def test_resume_never_releases_another_unknown_attempt_or_management_hold(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
    tmp_path: Path,
) -> None:
    repo, source = running(pg, catalog, test_settings)
    failure(repo, catalog, source)
    isolate_job(pg, test_settings, actor, source)
    repo, proof = running(pg, catalog, test_settings, validation=True, actor=actor)
    complete(pg, test_settings, repo, catalog, proof, tmp_path)
    repo, other = running(pg, catalog, test_settings, validation=True, actor=actor)
    with pg.begin() as session:
        session.get(GpuOccupancy, catalog["gpu_id"]).uncertain = True
        hold = uuid4()
        session.get(Gpu, catalog["gpu_id"]).management_hold = hold
    row = current(pg)
    SpecIsolationAdministration(pg, test_settings).action(
        actor,
        row.id,
        SpecResume(
            action="resume", expected_revision=row.revision, validation_job_id=proof["job_id"]
        ),
        str(uuid4()),
        "test",
    )
    with pg() as session:
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        assert occupancy.uncertain and occupancy.attempt_id == parse_id("att", other["attempt_id"])
        assert session.get(Gpu, catalog["gpu_id"]).management_hold == hold


def test_new_failure_and_resume_cannot_erase_each_other(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
    tmp_path: Path,
) -> None:
    repo, source = running(pg, catalog, test_settings)
    failure(repo, catalog, source)
    isolate_job(pg, test_settings, actor, source)
    repo, proof = running(pg, catalog, test_settings, validation=True, actor=actor)
    complete(pg, test_settings, repo, catalog, proof, tmp_path)
    repo, later = running(pg, catalog, test_settings, validation=True, actor=actor)
    row = current(pg)

    def resume() -> str:
        try:
            SpecIsolationAdministration(pg, test_settings).action(
                actor,
                row.id,
                SpecResume(
                    action="resume",
                    expected_revision=row.revision,
                    validation_job_id=proof["job_id"],
                ),
                str(uuid4()),
                "test",
            )
            return "resumed"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        resuming = pool.submit(resume)
        failing = pool.submit(failure, repo, catalog, later)
        result = resuming.result()
        failing.result()
    row = current(pg)
    assert row.last_failure_job_id == parse_id("job", later["job_id"])
    if result == "resumed":
        assert not row.is_active and row.failure_count == 1
    else:
        assert result == "config_revision_conflict" and row.is_active and row.failure_count == 2


@pytest.mark.parametrize(
    "role,expected",
    [("administrator", 201), ("operator", 201), ("workflow_maintainer", 403), ("observer", 403)],
)
def test_http_four_identities_roles_csrf_revision_and_idempotency(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    actor: AdminIdentity,
    role: str,
    expected: int,
) -> None:
    repo, claim = running(pg, catalog, test_settings)
    failure(repo, catalog, claim)
    with pg.begin() as session:
        session.get(AdminUser, actor.user_id).role = role
        revision = session.get(Job, parse_id("job", claim["job_id"])).revision
    login = AuthRepository(pg, test_settings).login(actor.username, PASSWORD, "127.0.0.1", "test")
    path = "/admin/api/v1/spec-isolations"
    body = {
        "job_id": claim["job_id"],
        "expected_revision": revision,
        "evidence_id": "incident-http",
    }
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        for token in ("synthetic-business", "worker_synthetic", "gateway_synthetic"):
            assert (
                client.post(
                    path, json=body, headers={"Authorization": "Bearer " + token}
                ).status_code
                == 401
            )
        assert client.get(path).status_code == 401
        client.cookies.set(COOKIE, login["token"])
        assert client.get(path).status_code == 200
        headers = {
            "Origin": test_settings.public_origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "spec-isolation-http-intent",
            "If-Match": '"' + str(revision) + '"',
        }
        assert (
            client.post(
                path, json=body, headers={"Origin": test_settings.public_origin}
            ).status_code
            == 403
        )
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == expected, response.text
        if expected == 201:
            assert client.post(path, json=body, headers=headers).json() == response.json()
            assert (
                client.post(
                    path,
                    json={**body, "gpu_id": public_id("gpu", catalog["gpu_id"])},
                    headers=headers,
                ).status_code
                == 422
            )
            assert (
                client.post(path, json=body, headers={**headers, "If-Match": '"0"'}).status_code
                == 409
            )
            identifier = response.json()["id"]
            result = client.post(
                path + "/" + identifier + "/actions",
                json={
                    "action": "resume",
                    "expected_revision": response.json()["revision"],
                    "validation_job_id": claim["job_id"],
                },
                headers={**headers, "If-Match": '"' + str(response.json()["revision"]) + '"'},
            )
            assert result.status_code == 409
