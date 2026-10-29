from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generation_application.ports import BusinessIdentity
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    ApiKey,
    GpuOccupancy,
    Job,
    Node,
    ObjectRecord,
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
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_http_auth import client as client
from test_http_auth import key_body, login

pytestmark = pytest.mark.integration


@pytest.fixture
def mesh_catalog(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    model3d_manifest: dict[str, Any],
    test_settings: Settings,
) -> dict[str, Any]:
    identity = catalog["identity"]
    policy = deepcopy(identity.policy)
    policy["scopes"].append("meshes:create")
    policy["limits"]["daily_mesh_count"] = {"mode": "bounded", "value": 5}
    with pg.begin() as session:
        session.get(ApiKey, identity.key_id).policy = policy
        for entity, identifier in ((Tenant, identity.tenant_id), (Project, identity.project_id)):
            parent = session.get(entity, identifier)
            parent.limits = {**parent.limits, "daily_mesh_count": {"mode": "bounded", "value": 5}}
        node = session.get(Node, catalog["node_id"])
        node.inventory = {**node.inventory, "features": ["model3d-output-v1"]}
        workflow = Workflow(
            tenant_id=identity.tenant_id,
            project_id=identity.project_id,
            name="Synthetic mesh",
            task_type="image_to_3d",
            draft=model3d_manifest,
        )
        session.add(workflow)
        session.flush()
        release = WorkflowRelease(
            tenant_id=identity.tenant_id,
            project_id=identity.project_id,
            workflow_id=workflow.id,
            version=1,
            digest=fingerprint(model3d_manifest),
            manifest=model3d_manifest,
            validated=True,
            validation={"test_only": True},
        )
        session.add(release)
        session.flush()
        for channel in catalog["channel_ids"]:
            session.add(
                WorkflowBinding(
                    tenant_id=identity.tenant_id,
                    project_id=identity.project_id,
                    channel_id=channel,
                    release_id=release.id,
                    desired_digest=release.digest,
                    applied_digest=release.digest,
                    verified_digest=release.digest,
                    observed_at=utcnow(),
                )
            )
        session.add(
            PublicModel(
                tenant_id=identity.tenant_id,
                project_id=identity.project_id,
                alias="synthetic-mesh",
                task_type="image_to_3d",
                release_id=release.id,
                channel_ids=[public_id("ch", value) for value in catalog["channel_ids"]],
                defaults={"quality": "standard", "n": 1},
                published=True,
            )
        )
    identity = BusinessIdentity(identity.tenant_id, identity.project_id, identity.key_id, policy)
    content = ContentRepository(pg, LocalDevelopmentStore(test_settings.local_storage_root))
    image = BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, format="PNG")
    image.seek(0)
    asset = content.upload_asset(identity, image, "mesh-synthetic-input")
    return {
        **catalog,
        "identity": identity,
        "asset_id": asset["id"],
        "release_id": release.id,
        "content": content,
    }


def request(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": "synthetic-mesh",
        "task_type": "image_to_3d",
        "input": {"source_image": {"asset_id": catalog["asset_id"]}},
    }


@pytest.mark.parametrize("mesh_permission", [True, False])
def test_mesh_http_requires_mesh_scope_without_image_permission(
    client: TestClient, pg: sessionmaker[Session], mesh_catalog: dict[str, Any], mesh_permission: bool
) -> None:
    login(client)
    body = key_body(mesh_catalog)
    body["policy"]["scopes"] = [
        "jobs:create", "jobs:read", "models:read", "assets:upload", "assets:read",
        "meshes:create" if mesh_permission else "images:create",
    ]
    body["policy"]["limits"]["daily_mesh_count"] = {"mode": "bounded", "value": 5}
    created = client.post("/admin/api/v1/api-keys", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert created.status_code == 201, created.text
    headers = {"Authorization": "Bearer " + created.json()["secret"], "Idempotency-Key": str(uuid4())}
    image = BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    uploaded = client.post("/v1/assets", files={"file": ("synthetic.png", image.getvalue(), "image/png")}, headers=headers)
    assert uploaded.status_code == 201, uploaded.text
    job_request = request({"asset_id": uploaded.json()["id"]})
    headers["Idempotency-Key"] = str(uuid4())
    response = client.post("/v1/jobs", json=job_request, headers=headers)
    key_id = parse_id("key", created.json()["key"]["id"])
    if mesh_permission:
        assert response.status_code == 202, response.text
        replay = client.post("/v1/jobs", json=job_request, headers=headers)
        assert replay.status_code == 202 and replay.json()["id"] == response.json()["id"]
    else:
        assert response.status_code == 403 and response.json()["error"]["code"] == "scope_denied"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.key_id == key_id)) == int(mesh_permission)


def collect(
    pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings, path: Path
) -> tuple[OutputRepository, dict[str, Any], dict[str, Any]]:
    leases = LeaseRepository(
        pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True
    )
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    spec = {
        "position": 0,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "media_type": "model/gltf-binary",
    }
    events = [
        ("preparing", {"execution_digest": claim["execution_digest"]}),
        (
            "submit_intent",
            {
                "association_id": str(uuid4()),
                "workflow_digest": claim["workflow_digest"],
                "journal_committed": True,
            },
        ),
        ("submitted", {"prompt_id": "synthetic-mesh"}),
        (
            "outputs_collected",
            {"manifest_digest": fingerprint([spec]), "output_count": 1, "files_stable": True},
        ),
        (
            "gpu_released",
            {
                "gpu_id": claim["channel"]["gpu_id"],
                "compute_stopped": True,
                "gpu_encoding_stopped": True,
                "evidence_digest": "e" * 64,
            },
        ),
    ]
    for sequence, (kind, payload) in enumerate(events, 1):
        leases.event(
            catalog["node_id"],
            WorkerEvent(
                protocol_version="0.4.0",
                event_id=str(uuid4()),
                node_id=claim["node_id"],
                boot_id=claim["boot_id"],
                job_id=claim["job_id"],
                attempt_id=claim["attempt_id"],
                fencing_token=claim["fencing_token"],
                journal_seq=sequence,
                observed_at=int(utcnow().timestamp()),
                type=kind,
                payload=payload,
            ),
        )
    return (
        OutputRepository(pg, catalog["content"].store, leases, path.parent / "spool"),
        claim,
        spec,
    )


def test_mesh_delivery_is_once_isolated_downloadable_and_expires_after_eight_hours(
    pg: sessionmaker[Session],
    mesh_catalog: dict[str, Any],
    test_settings: Settings,
    model3d_glb: Path,
) -> None:
    jobs, identity = JobRepository(pg), mesh_catalog["identity"]
    token = str(uuid4())
    job = jobs.create(identity, request(mesh_catalog), token, "mesh-test")
    assert jobs.create(identity, request(mesh_catalog), token, "mesh-replay")["id"] == job["id"]
    outputs, claim, spec = collect(pg, mesh_catalog, test_settings, model3d_glb)
    aid = parse_id("att", claim["attempt_id"])
    auth = {
        "protocol_version": "0.4.0",
        "boot_id": claim["boot_id"],
        "fencing_token": claim["fencing_token"],
    }
    plan = outputs.authorize(
        mesh_catalog["node_id"], aid, UploadAuthorization(**auth, outputs=[spec])
    )
    outputs.accept(
        mesh_catalog["node_id"],
        parse_id("upl", plan["uploads"][0]["id"]),
        mesh_catalog["boot_id"],
        claim["fencing_token"],
        model3d_glb,
    )
    completion = Complete(
        **auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"]
    )
    result = outputs.complete(mesh_catalog["node_id"], aid, completion)
    assert outputs.complete(mesh_catalog["node_id"], aid, completion) == result
    content = mesh_catalog["content"]
    cid = parse_id("content", result["artifacts"][0]["id"])
    metadata = content.metadata(identity, cid)
    assert metadata["metadata"]["triangle_count"] == 1
    assert b"".join(content.download(identity, cid, "bytes=0-3").data) == b"glTF"
    assert b"".join(content.download(identity, cid, None, head=True).data) == b""
    stranger = BusinessIdentity(identity.tenant_id, identity.project_id, uuid4(), identity.policy)
    with pytest.raises(DomainError):
        content.metadata(stranger, cid)
    with pg.begin() as session:
        ledger = session.scalar(select(UsageLedger))
        assert (
            ledger.mesh_count == 1
            and ledger.image_count == ledger.video_milliseconds == ledger.audio_milliseconds == 0
        )
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.get(GpuOccupancy, mesh_catalog["gpu_id"]) is None
        assert all(
            bucket.daily_mesh_count == 1
            and bucket.daily_image_count
            == bucket.daily_video_milliseconds
            == bucket.daily_audio_milliseconds
            == 0
            for bucket in session.scalars(select(QuotaBucket).where(QuotaBucket.period != "active"))
        )
        obj = session.scalar(select(ObjectRecord).where(ObjectRecord.sha256 == spec["sha256"]))
        terminal = session.get(Job, parse_id("job", job["id"])).terminal_at
        assert obj.expires_at == terminal + timedelta(hours=8)
        obj.expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(DomainError):
        content.download(identity, cid, None)


@pytest.mark.parametrize("scope", ["tenant", "project", "key"])
def test_mesh_quota_missing_or_zero_rejects_every_parent(
    pg: sessionmaker[Session], mesh_catalog: dict[str, Any], scope: str
) -> None:
    identity = mesh_catalog["identity"]
    with pg.begin() as session:
        row = session.get(
            ApiKey if scope == "key" else Tenant if scope == "tenant" else Project,
            identity.key_id
            if scope == "key"
            else identity.tenant_id
            if scope == "tenant"
            else identity.project_id,
        )
        if scope == "key":
            policy = deepcopy(row.policy)
            policy["limits"].pop("daily_mesh_count")
            row.policy = policy
        else:
            row.limits = {
                name: limit for name, limit in row.limits.items() if name != "daily_mesh_count"
            }
    with pytest.raises(DomainError, match="daily_mesh_count"):
        JobRepository(pg).create(identity, request(mesh_catalog), str(uuid4()), "mesh-quota")
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_mesh_concurrent_admission_honors_last_slot_and_cancel_refunds(
    pg: sessionmaker[Session], mesh_catalog: dict[str, Any]
) -> None:
    identity = mesh_catalog["identity"]
    with pg.begin() as session:
        row = session.get(ApiKey, identity.key_id)
        policy = deepcopy(row.policy)
        policy["limits"]["daily_mesh_count"]["value"] = 1
        row.policy = policy

    def create(_: int) -> dict[str, Any] | str:
        try:
            return JobRepository(pg).create(
                identity, request(mesh_catalog), str(uuid4()), "mesh-concurrent"
            )
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (1, 2)))
    accepted = [result for result in results if isinstance(result, dict)]
    assert len(accepted) == 1 and "quota_exceeded" in results
    JobRepository(pg).cancel(identity, parse_id("job", accepted[0]["id"]), "mesh-cancel")
    with pg() as session:
        assert all(bucket.daily_mesh_count == 0 for bucket in session.scalars(select(QuotaBucket)))
        assert session.scalar(select(UsageLedger)).mesh_count == 0


def test_legacy_worker_and_legacy_key_cannot_run_mesh(
    pg: sessionmaker[Session], mesh_catalog: dict[str, Any], test_settings: Settings
) -> None:
    identity = mesh_catalog["identity"]
    jobs = JobRepository(pg)
    jobs.create(identity, request(mesh_catalog), str(uuid4()), "mesh-create")
    leases = LeaseRepository(
        pg,
        SecretVault(test_settings.encryption_key.get_secret_value()),
        allow_synthetic_catalog=True,
    )
    with pg.begin() as session:
        node = session.get(Node, mesh_catalog["node_id"])
        node.inventory = {**node.inventory, "features": ["audio-output-v1"]}
    assert leases.claim(mesh_catalog["node_id"], mesh_catalog["boot_id"]) is None
    with pg.begin() as session:
        key = session.get(ApiKey, identity.key_id)
        key.policy = {
            **key.policy,
            "scopes": [scope for scope in key.policy["scopes"] if scope != "meshes:create"],
        }
    with pytest.raises(DomainError, match="meshes:create"):
        jobs.create(identity, request(mesh_catalog), str(uuid4()), "mesh-denied")
