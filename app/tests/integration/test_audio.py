from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from generation_application.ports import AdminIdentity, BusinessIdentity, GenerationService
from generation_contracts.catalog import ValidationJob
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminUser,
    ApiKey,
    Artifact,
    GpuOccupancy,
    Job,
    Node,
    Project,
    PublicModel,
    QuotaBucket,
    Tenant,
    UsageLedger,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.outputs import OutputRepository
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from generation_infrastructure.validation_jobs import ValidationJobs
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture
def audio_catalog(pg: sessionmaker[Session], catalog: dict[str, Any], audio_manifest: dict[str, Any]) -> dict[str, Any]:
    identity = catalog["identity"]
    policy = deepcopy(identity.policy)
    policy["scopes"].append("audio:create")
    policy["spec_limits"]["audio_max_milliseconds"] = 2000
    policy["limits"]["daily_audio_milliseconds"] = {"mode": "bounded", "value": 5000}
    with pg.begin() as session:
        session.get(ApiKey, identity.key_id).policy = policy
        for entity, identifier in [(Tenant, identity.tenant_id), (Project, identity.project_id)]:
            parent = session.get(entity, identifier)
            parent.limits = {**parent.limits, "daily_audio_milliseconds": {"mode": "bounded", "value": 5000}}
        node = session.get(Node, catalog["node_id"])
        node.inventory = {**node.inventory, "features": ["audio-output-v1"]}
        workflow = Workflow(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Synthetic audio", task_type="text_to_audio", draft=audio_manifest)
        session.add(workflow)
        session.flush()
        release = WorkflowRelease(tenant_id=identity.tenant_id, project_id=identity.project_id, workflow_id=workflow.id, version=1, digest=fingerprint(audio_manifest), manifest=audio_manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        for channel in catalog["channel_ids"]:
            session.add(WorkflowBinding(tenant_id=identity.tenant_id, project_id=identity.project_id, channel_id=channel, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest, observed_at=utcnow()))
        session.add(PublicModel(tenant_id=identity.tenant_id, project_id=identity.project_id, alias="synthetic-audio", task_type="text_to_audio", release_id=release.id, channel_ids=[public_id("ch", identifier) for identifier in catalog["channel_ids"]], defaults={"quality": "standard", "seconds": 2}, published=True))
        return {**catalog, "identity": BusinessIdentity(identity.tenant_id, identity.project_id, identity.key_id, policy), "release_id": release.id}


def request() -> dict[str, Any]:
    return {"model": "synthetic-audio", "task_type": "text_to_audio", "input": {"prompt": "Synthetic 440 Hz sound"}}


def collected(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings, path: Path) -> tuple[OutputRepository, dict[str, Any], dict[str, Any]]:
    leases = LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    spec = {"position": 0, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "media_type": "audio/wav"}
    events = [("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": claim["workflow_digest"], "journal_committed": True}), ("submitted", {"prompt_id": "synthetic-audio"}), ("outputs_collected", {"manifest_digest": fingerprint([spec]), "output_count": 1, "files_stable": True}), ("gpu_released", {"gpu_id": claim["channel"]["gpu_id"], "compute_stopped": True, "gpu_encoding_stopped": True, "evidence_digest": "e" * 64})]
    for sequence, (kind, payload) in enumerate(events, 1):
        leases.event(catalog["node_id"], WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=claim["node_id"], boot_id=claim["boot_id"], job_id=claim["job_id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=sequence, observed_at=int(utcnow().timestamp()), type=kind, payload=payload))
    return OutputRepository(pg, LocalDevelopmentStore(settings.local_storage_root), leases, path.parent / "spool"), claim, spec


def test_verified_audio_refunds_reservation_difference_and_replay_is_once(pg: sessionmaker[Session], audio_catalog: dict[str, Any], test_settings: Settings, audio_wav: Path) -> None:
    identity = audio_catalog["identity"]
    jobs = JobRepository(pg)
    token = str(uuid4())
    job = jobs.create(identity, request(), token, "audio-test")
    assert jobs.create(identity, request(), token, "audio-replay")["id"] == job["id"]
    with pg() as session:
        assert all(bucket.daily_audio_milliseconds == 2000 for bucket in session.scalars(select(QuotaBucket).where(QuotaBucket.period != "active")))
        assert all(bucket.daily_image_count == bucket.daily_video_milliseconds == 0 for bucket in session.scalars(select(QuotaBucket)))
    outputs, claim, spec = collected(pg, audio_catalog, test_settings, audio_wav)
    aid = parse_id("att", claim["attempt_id"])
    auth = {"protocol_version": "0.4.0", "boot_id": claim["boot_id"], "fencing_token": claim["fencing_token"]}
    plan = outputs.authorize(audio_catalog["node_id"], aid, UploadAuthorization(**auth, outputs=[spec]))
    uid = parse_id("upl", plan["uploads"][0]["id"])
    outputs.accept(audio_catalog["node_id"], uid, audio_catalog["boot_id"], claim["fencing_token"], audio_wav)
    complete = Complete(**auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"])
    result = outputs.complete(audio_catalog["node_id"], aid, complete)
    assert outputs.complete(audio_catalog["node_id"], aid, complete) == result
    with pg() as session:
        ledger = session.scalar(select(UsageLedger))
        assert ledger.audio_milliseconds == 500 and ledger.image_count == ledger.video_milliseconds == 0
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.get(GpuOccupancy, audio_catalog["gpu_id"]) is None
        assert all(bucket.daily_audio_milliseconds == 500 and bucket.unfinished_jobs == 0 for bucket in session.scalars(select(QuotaBucket).where(QuotaBucket.period != "active")))
    content = ContentRepository(pg, outputs.store)
    cid = parse_id("content", result["artifacts"][0]["id"])
    metadata = content.metadata(identity, cid)
    assert metadata["metadata"] == {"container": "wav", "codec": "pcm_s16le", "sample_rate": 24000, "channels": 1, "duration_milliseconds": 500}
    assert b"".join(content.download(identity, cid, "bytes=0-3").data) == b"RIFF"
    assert b"".join(content.download(identity, cid, None, head=True).data) == b""


def test_audio_cancel_releases_all_audio_but_keeps_accepted_job_count(pg: sessionmaker[Session], audio_catalog: dict[str, Any]) -> None:
    identity = audio_catalog["identity"]
    jobs = JobRepository(pg)
    job = jobs.create(identity, request(), str(uuid4()), "audio-test")
    identifier = parse_id("job", job["id"])
    jobs.cancel(identity, identifier, "audio-cancel")
    jobs.cancel(identity, identifier, "audio-cancel-again")
    with pg() as session:
        assert all(bucket.daily_audio_milliseconds == 0 and bucket.daily_jobs == 1 for bucket in session.scalars(select(QuotaBucket).where(QuotaBucket.period != "active")))
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        ledger = session.scalar(select(UsageLedger))
        assert ledger.audio_milliseconds == ledger.image_count == ledger.video_milliseconds == 0


@pytest.mark.parametrize("scope", ["tenant", "project", "key"])
def test_audio_quota_is_checked_independently_at_every_parent(pg: sessionmaker[Session], audio_catalog: dict[str, Any], scope: str) -> None:
    identity = audio_catalog["identity"]
    with pg.begin() as session:
        if scope == "key":
            row = session.get(ApiKey, identity.key_id)
            policy = deepcopy(row.policy)
            policy["limits"]["daily_audio_milliseconds"]["value"] = 1000
            row.policy = policy
        else:
            row = session.get(Tenant if scope == "tenant" else Project, identity.tenant_id if scope == "tenant" else identity.project_id)
            row.limits = {**row.limits, "daily_audio_milliseconds": {"mode": "bounded", "value": 1000}}
    with pytest.raises(DomainError, match="daily_audio_milliseconds"):
        JobRepository(pg).create(identity, request(), str(uuid4()), "audio-over-quota")
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(QuotaBucket)) == 0


def test_old_worker_cannot_claim_audio_and_old_key_cannot_generate_it(pg: sessionmaker[Session], audio_catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = audio_catalog["identity"]
    jobs = JobRepository(pg)
    jobs.create(identity, request(), str(uuid4()), "audio-test")
    with pg.begin() as session:
        node = session.get(Node, audio_catalog["node_id"])
        node.inventory = {key: value for key, value in node.inventory.items() if key != "features"}
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    assert leases.claim(audio_catalog["node_id"], audio_catalog["boot_id"]) is None
    with pg.begin() as session:
        node = session.get(Node, audio_catalog["node_id"])
        node.inventory = {**node.inventory, "features": ["audio-output-v1"]}
        key = session.get(ApiKey, identity.key_id)
        key.policy = {**key.policy, "scopes": [scope for scope in key.policy["scopes"] if scope != "audio:create"]}
    with pytest.raises(DomainError, match="audio:create"):
        jobs.create(identity, request(), str(uuid4()), "audio-denied")
    assert leases.claim(audio_catalog["node_id"], audio_catalog["boot_id"])


def test_audio_capability_has_specs_without_image_size(pg: sessionmaker[Session], audio_catalog: dict[str, Any], test_settings: Settings) -> None:
    catalog = CatalogAdministration(pg, test_settings)
    result = catalog.public_models(audio_catalog["identity"], capabilities=True)
    audio = next(item for item in result["data"] if item["task_type"] == "text_to_audio")
    assert audio["presets"][0] == {"quality": "standard", "seconds": 2, "audio_format": "wav", "sample_rate": 24000, "channels": 1}


def test_audio_only_key_does_not_need_image_or_video_scope(pg: sessionmaker[Session], audio_catalog: dict[str, Any]) -> None:
    identity = audio_catalog["identity"]
    policy = {**identity.policy, "scopes": [scope for scope in identity.policy["scopes"] if scope not in ("images:create", "videos:create")]}
    with pg.begin() as session:
        session.get(ApiKey, identity.key_id).policy = policy
    audio_only = BusinessIdentity(identity.tenant_id, identity.project_id, identity.key_id, policy)
    result = GenerationService(JobRepository(pg)).create(audio_only, request(), str(uuid4()), "audio-only")
    assert result["task_type"] == "text_to_audio" and result["status"] == "queued"


@pytest.mark.parametrize("parent_quota", [0, 5000])
def test_admin_audio_validation_uses_internal_key_and_respects_parent_quota(pg: sessionmaker[Session], audio_catalog: dict[str, Any], test_settings: Settings, parent_quota: int) -> None:
    identity = audio_catalog["identity"]
    customer_policy = deepcopy(identity.policy)
    with pg.begin() as session:
        user = AdminUser(username="audio-validation-admin", password_hash="!", role="administrator")
        session.add(user)
        session.flush()
        actor = AdminIdentity(user.id, user.username, user.role, uuid4())
        model_id = session.scalar(select(PublicModel.id).where(PublicModel.alias == "synthetic-audio"))
        parent = session.get(Project, identity.project_id)
        parent.limits = {**parent.limits, "daily_audio_milliseconds": {"mode": "bounded", "value": parent_quota}}
    body = ValidationJob.model_validate({"channel_id": public_id("ch", audio_catalog["channel_ids"][0]), "request": request()})
    service = ValidationJobs(pg, test_settings)
    if parent_quota == 0:
        with pytest.raises(DomainError, match="daily_audio_milliseconds"):
            service.create(actor, model_id, body, "audio-validation", "audio-validation-test")
    else:
        result = service.create(actor, model_id, body, "audio-validation", "audio-validation-test")
        assert service.create(actor, model_id, body, "audio-validation", "audio-replay") == result
        with pg() as session:
            job = session.get(Job, parse_id("job", result["id"]))
            key = session.get(ApiKey, job.key_id)
            assert job.is_validation and key.issuer == "internal_validation" and "audio:create" in key.policy["scopes"]
            assert job.key_id != identity.key_id
    with pg() as session:
        assert session.get(ApiKey, identity.key_id).policy == customer_policy
