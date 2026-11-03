from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from generation_application.ports import BusinessIdentity
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_contracts.validation import JobCreate, WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, utcnow
from generation_domain.workflow_inputs import INPUT_FEATURE
from generation_domain.workflows import validate_manifest
from generation_infrastructure.content import object_in_use
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    ApiKey,
    Job,
    JobContextReference,
    Node,
    ObjectRecord,
    PublicModel,
    WorkflowBinding,
    WorkflowContext,
    WorkflowRelease,
)
from generation_infrastructure.outputs import OutputRepository
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from generation_infrastructure.workflow_contexts import ContextRepository
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.unit.test_workflow_context_contract import context_bytes

pytestmark = pytest.mark.integration


def publish(pg: sessionmaker[Session], catalog: dict[str, Any], manifest: dict[str, Any]) -> None:
    validate_manifest(manifest, manifest["task_type"])
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        assert model
        old = session.get(WorkflowRelease, model.release_id)
        assert old
        version = session.scalar(select(func.max(WorkflowRelease.version)).where(WorkflowRelease.workflow_id == old.workflow_id))
        release = WorkflowRelease(tenant_id=old.tenant_id, project_id=old.project_id, workflow_id=old.workflow_id, version=version + 1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        model.task_type, model.release_id = manifest["task_type"], release.id
        model.defaults = {"size": "320x240", "seconds": 0.5, "quality": "standard"}
        for identifier in catalog["channel_ids"]:
            session.add(WorkflowBinding(tenant_id=old.tenant_id, project_id=old.project_id, channel_id=identifier, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest))
        node = session.get(Node, catalog["node_id"])
        assert node
        node.inventory = {**node.inventory, "features": [INPUT_FEATURE]}


def stage(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings, path: Path) -> tuple[LeaseRepository, dict[str, Any], dict[str, Any]]:
    leases = LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    spec = {"position": 0, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "media_type": "video/mp4"}
    events = [("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": claim["workflow_digest"], "journal_committed": True}), ("submitted", {"prompt_id": "context-protocol-test"}), ("outputs_collected", {"manifest_digest": fingerprint([spec]), "output_count": 1, "files_stable": True}), ("gpu_released", {"gpu_id": claim["channel"]["gpu_id"], "compute_stopped": True, "gpu_encoding_stopped": True, "evidence_digest": "e" * 64})]
    for sequence, (kind, payload) in enumerate(events, 1):
        leases.event(catalog["node_id"], WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=claim["node_id"], boot_id=claim["boot_id"], job_id=claim["job_id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=sequence, observed_at=int(utcnow().timestamp()), type=kind, payload=payload))
    return leases, claim, spec


def producer(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings, manifest: dict[str, Any], video: Path, tmp_path: Path) -> tuple[ContextRepository, dict[str, Any], ObjectRecord]:
    publish(pg, catalog, manifest)
    jobs = JobRepository(pg)
    jobs.create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_video", "input": {"prompt": "Synthetic context producer"}}, str(uuid4()), "test")
    leases, claim, spec = stage(pg, catalog, settings, video)
    store = LocalDevelopmentStore(settings.local_storage_root)
    contexts = ContextRepository(pg, store, leases)
    outputs = OutputRepository(pg, store, leases, tmp_path / "spool")
    attempt = parse_id("att", claim["attempt_id"])
    auth = {"protocol_version": "0.4.0", "boot_id": claim["boot_id"], "fencing_token": claim["fencing_token"]}
    plan = outputs.authorize(catalog["node_id"], attempt, UploadAuthorization(**auth, outputs=[spec]))
    completion = Complete(**auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"])
    outputs.accept(catalog["node_id"], parse_id("upl", completion.upload_ids[0]), catalog["boot_id"], claim["fencing_token"], video)
    with pytest.raises(DomainError) as missing:
        outputs.complete(catalog["node_id"], attempt, completion)
    assert missing.value.code == "workflow_context_not_verified"
    path = tmp_path / "synthetic.ctx"
    path.write_bytes(context_bytes())
    first = contexts.accept(catalog["node_id"], attempt, catalog["boot_id"], claim["fencing_token"], path)
    assert contexts.accept(catalog["node_id"], attempt, catalog["boot_id"], claim["fencing_token"], path) == first
    with pytest.raises(DomainError):
        contexts.accept(catalog["node_id"], attempt, catalog["boot_id"], claim["fencing_token"] + 1, path)
    outputs.complete(catalog["node_id"], attempt, completion)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(WorkflowContext)) == 1
        obj = session.scalar(select(ObjectRecord).join(WorkflowContext, WorkflowContext.object_id == ObjectRecord.id))
        assert obj
        session.expunge(obj)
    assert jobs.get(catalog["identity"], parse_id("job", claim["job_id"]))["context"]["available"]
    return contexts, claim, obj


def continuation(manifest: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(manifest)
    result["context"]["load"] = True
    result["workflow_api"]["30"] = {"class_type": "MiniMaxH3MotionContextLoadLatent", "inputs": {"latent_path": "", "clip_index": 1}}
    result["workflow_api"]["1"]["inputs"]["previous"] = ["30", 0]
    result["bindings"].append({"source": "context_latent", "node_id": "30", "input_name": "latent_path"})
    return result


def test_owned_context_freezes_survives_expiry_while_in_use_and_fences_download(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, context_manifest: dict[str, Any], input_video: Path, tmp_path: Path) -> None:
    contexts, parent, obj = producer(pg, catalog, test_settings, context_manifest, input_video, tmp_path)
    publish(pg, catalog, continuation(context_manifest))
    payload = {"model": "synthetic-image", "task_type": "text_to_video", "input": {"prompt": "Continue the synthetic clip", "context": {"job_id": parent["job_id"]}}}
    JobCreate.model_validate(payload)
    jobs = JobRepository(pg)
    created = jobs.create(catalog["identity"], payload, "child-idempotent", "test")
    assert jobs.create(catalog["identity"], payload, "child-idempotent", "test")["id"] == created["id"]
    with pg.begin() as session:
        child = session.get(Job, parse_id("job", created["id"]))
        assert child and child.execution["context"]["sha256"] == obj.sha256
        assert session.get(JobContextReference, child.id)
        assert object_in_use(session, obj.id)
        session.get(ObjectRecord, obj.id).expires_at = utcnow() - timedelta(seconds=1)
    _, claim, _ = stage(pg, catalog, test_settings, input_video)
    downloaded = contexts.input_object(catalog["node_id"], parse_id("att", claim["attempt_id"]), catalog["boot_id"], claim["fencing_token"])
    assert downloaded.sha256 == obj.sha256
    with pytest.raises(DomainError):
        contexts.input_object(catalog["node_id"], parse_id("att", claim["attempt_id"]), catalog["boot_id"], claim["fencing_token"] + 1)
    with pytest.raises(DomainError) as expired:
        jobs.create(catalog["identity"], payload, "new-child-after-expiry", "test")
    assert expired.value.code == "workflow_context_unavailable"


@pytest.mark.parametrize("failure", ["other_key", "incompatible", "missing", "pickle"])
def test_parent_context_rejects_cross_key_incompatible_missing_or_executable_input(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, context_manifest: dict[str, Any], input_video: Path, tmp_path: Path, failure: str) -> None:
    contexts, parent, _ = producer(pg, catalog, test_settings, context_manifest, input_video, tmp_path)
    manifest = continuation(context_manifest)
    if failure == "incompatible":
        manifest["context"]["compatibility"] = "f" * 64
    publish(pg, catalog, manifest)
    identity = catalog["identity"]
    if failure == "other_key":
        with pg.begin() as session:
            other = ApiKey(tenant_id=catalog["identity"].tenant_id, project_id=catalog["identity"].project_id, name="Other synthetic key", policy=catalog["identity"].policy)
            session.add(other)
            session.flush()
            identity = BusinessIdentity(other.tenant_id, other.project_id, other.id, other.policy)
    payload = {"model": "synthetic-image", "task_type": "text_to_video", "input": {"prompt": "Continue", **({} if failure == "missing" else {"context": {"job_id": parent["job_id"]}})}}
    if failure == "pickle":
        path = tmp_path / "pickle.ctx"
        path.write_bytes(b"\x80\x04cos\nsystem\n.")
        with pytest.raises(DomainError):
            contexts.accept(catalog["node_id"], parse_id("att", parent["attempt_id"]), catalog["boot_id"], parent["fencing_token"], path)
        return
    with pytest.raises(DomainError):
        JobRepository(pg).create(identity, payload, str(uuid4()), "test")
