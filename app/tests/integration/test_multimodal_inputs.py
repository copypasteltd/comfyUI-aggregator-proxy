from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generation_application.ports import BusinessIdentity
from generation_contracts.validation import JobCreate
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id
from generation_domain.workflow_inputs import INPUT_FEATURE
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    ApiKey,
    Job,
    JobAssetReference,
    Node,
    PublicModel,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_http_auth import client as client
from test_http_auth import key_body, login

pytestmark = pytest.mark.integration


def publish(pg: sessionmaker[Session], catalog: dict[str, Any], manifest: dict[str, Any]) -> None:
    with pg.begin() as session:
        old = session.get(WorkflowRelease, catalog["release_id"])
        assert old
        release = WorkflowRelease(tenant_id=old.tenant_id, project_id=old.project_id, workflow_id=old.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        model = session.scalar(select(PublicModel))
        assert model
        model.task_type, model.release_id = manifest["task_type"], release.id
        model.defaults = {"size": "320x240", "quality": "standard", "seconds": 0.5}
        for identifier in catalog["channel_ids"]:
            session.add(WorkflowBinding(tenant_id=old.tenant_id, project_id=old.project_id, channel_id=identifier, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest))


def uploaded(repository: ContentRepository, identity: BusinessIdentity, audio: Path, video: Path, mesh: Path) -> dict[str, str]:
    image = BytesIO()
    Image.new("RGB", (32, 32), "lime").save(image, "PNG")
    image.seek(0)
    result = {"first_frame": repository.upload_asset(identity, image, "synthetic")["id"]}
    for name, kind, path in (("voice", "audio", audio), ("source_video", "video", video), ("coarse_mesh", "model", mesh)):
        with path.open("rb") as source:
            result[name] = repository.upload_asset(identity, source, "synthetic", kind=kind)["id"]
    return result


def payload(assets: dict[str, str]) -> dict[str, Any]:
    value = {"model": "synthetic-image", "task_type": "references_to_video", "input": {"prompt": "Synthetic motion", "media": [{"slot": name, "asset_id": identifier} for name, identifier in assets.items()], "texts": {"script": "A: Synthetic speech.\nB: Synthetic reply."}}, "parameters": {"seed": 11}}
    JobCreate.model_validate(value)
    return value


def test_all_media_inputs_are_owned_hashed_linked_and_idempotent(pg: sessionmaker[Session], catalog: dict[str, Any], multimodal_manifest: dict[str, Any], tmp_path: Path, audio_wav: Path, input_video: Path, model3d_glb: Path) -> None:
    publish(pg, catalog, multimodal_manifest)
    identity = catalog["identity"]
    repository = ContentRepository(pg, LocalDevelopmentStore(tmp_path / "objects"))
    assets = uploaded(repository, identity, audio_wav, input_video, model3d_glb)
    jobs = JobRepository(pg)
    value = payload(assets)
    first = jobs.create(identity, value, "all-media-idempotency", "synthetic")
    again = jobs.create(identity, value, "all-media-idempotency", "synthetic")
    assert first["id"] == again["id"]
    with pg() as session:
        job = session.get(Job, parse_id("job", first["id"]))
        assert job and set(job.execution["asset_hashes"]) == set(assets.values())
        assert session.scalar(select(func.count()).select_from(JobAssetReference)) == 4
    changed = {**value, "input": {**value["input"], "texts": {"script": "Changed semantic script"}}}
    with pytest.raises(DomainError) as error:
        jobs.create(identity, changed, "all-media-idempotency", "synthetic")
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize("failure", ["wrong_type", "slot_budget", "cross_key"])
def test_wrong_type_budget_and_cross_key_never_admit_a_job(pg: sessionmaker[Session], catalog: dict[str, Any], multimodal_manifest: dict[str, Any], tmp_path: Path, audio_wav: Path, input_video: Path, model3d_glb: Path, failure: str) -> None:
    if failure == "slot_budget":
        multimodal_manifest["input_slots"][2]["max_bytes"] = 10
    publish(pg, catalog, multimodal_manifest)
    identity = catalog["identity"]
    assets = uploaded(ContentRepository(pg, LocalDevelopmentStore(tmp_path / "objects")), identity, audio_wav, input_video, model3d_glb)
    if failure == "wrong_type":
        assets["voice"] = assets["first_frame"]
    if failure == "cross_key":
        with pg.begin() as session:
            other = ApiKey(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Synthetic distinct owner", policy=identity.policy)
            session.add(other)
            session.flush()
            identity = BusinessIdentity(identity.tenant_id, identity.project_id, other.id, identity.policy)
    with pytest.raises(DomainError) as error:
        JobRepository(pg).create(identity, payload(assets), "reject-all-media", "synthetic")
    assert error.value.code == ("asset_not_available" if failure == "cross_key" else "input_type_mismatch")
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(JobAssetReference)) == 0


def test_legacy_worker_cannot_claim_until_new_feature_is_registered(pg: sessionmaker[Session], catalog: dict[str, Any], multimodal_manifest: dict[str, Any], tmp_path: Path, audio_wav: Path, input_video: Path, model3d_glb: Path, test_settings: Settings) -> None:
    publish(pg, catalog, multimodal_manifest)
    assets = uploaded(ContentRepository(pg, LocalDevelopmentStore(tmp_path / "objects")), catalog["identity"], audio_wav, input_video, model3d_glb)
    job = JobRepository(pg).create(catalog["identity"], payload(assets), "feature-gated-inputs", "synthetic")
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    assert leases.claim(catalog["node_id"], catalog["boot_id"]) is None
    with pg.begin() as session:
        node = session.get(Node, catalog["node_id"])
        assert node
        node.inventory = {**node.inventory, "features": [INPUT_FEATURE]}
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim and claim["job_id"] == job["id"] and len(claim["execution"]["asset_hashes"]) == 4


def test_http_upload_kind_is_explicit_and_real_content_wins_over_filename(client: TestClient, catalog: dict[str, Any], audio_wav: Path) -> None:
    login(client)
    created = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())})
    assert created.status_code == 201, created.text
    headers = {"Authorization": "Bearer " + created.json()["secret"], "Idempotency-Key": "explicit-audio-input"}
    data = audio_wav.read_bytes()
    response = client.post("/v1/assets", headers=headers, data={"kind": "audio"}, files={"file": ("wrong-image-name.png", data, "image/png")})
    assert response.status_code == 201, response.text
    assert response.json()["media_type"] == "audio/wav" and response.json()["sample_rate"] == 24000
    replay = client.post("/v1/assets", headers=headers, data={"kind": "audio"}, files={"file": ("another.wav", data, "audio/wav")})
    assert replay.status_code == 201 and replay.json()["id"] == response.json()["id"]
    wrong = client.post("/v1/assets", headers={**headers, "Idempotency-Key": "wrong-kind"}, files={"file": ("wrong.png", data, "image/png")})
    assert wrong.status_code == 422


@pytest.mark.parametrize("shape", ["two_files", "duplicate_kind", "unknown_field"])
def test_upload_rejects_unbounded_or_ambiguous_multipart(client: TestClient, catalog: dict[str, Any], audio_wav: Path, shape: str) -> None:
    login(client)
    created = client.post("/admin/api/v1/api-keys", json=key_body(catalog), headers={"Idempotency-Key": str(uuid4())})
    assert created.status_code == 201
    parts = [("file", ("voice.wav", audio_wav.read_bytes(), "audio/wav")), ("kind", (None, "audio"))]
    if shape == "two_files":
        parts.append(("file", ("second.wav", audio_wav.read_bytes(), "audio/wav")))
    elif shape == "duplicate_kind":
        parts.append(("kind", (None, "video")))
    else:
        parts = [("file", ("voice.wav", audio_wav.read_bytes(), "audio/wav")), ("extra", (None, "unregistered"))]
    response = client.post("/v1/assets", files=parts, headers={"Authorization": "Bearer " + created.json()["secret"], "Idempotency-Key": str(uuid4())})
    assert response.status_code in (400, 422), response.text
