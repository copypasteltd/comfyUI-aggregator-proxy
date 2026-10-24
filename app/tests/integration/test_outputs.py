from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_contracts.validation import WorkerEvent
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import Artifact, GpuOccupancy, Job, UsageLedger
from generation_infrastructure.outputs import OutputRepository
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def ready(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings, path: Path) -> tuple[OutputRepository, LeaseRepository, dict[str, Any], dict[str, Any]]:
    jobs = JobRepository(pg)
    jobs.create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "protocol test"}}, str(uuid4()), "test")
    leases = LeaseRepository(pg, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
    claim = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert claim
    spec = {"position": 0, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "media_type": "image/png"}
    events = [("preparing", {"execution_digest": claim["execution_digest"]}), ("submit_intent", {"association_id": str(uuid4()), "workflow_digest": claim["workflow_digest"], "journal_committed": True}), ("submitted", {"prompt_id": "protocol-test"}), ("outputs_collected", {"manifest_digest": fingerprint([spec]), "output_count": 1, "files_stable": True}), ("gpu_released", {"gpu_id": claim["channel"]["gpu_id"], "compute_stopped": True, "gpu_encoding_stopped": True, "evidence_digest": "e" * 64})]
    for sequence, (kind, payload) in enumerate(events, 1):
        leases.event(catalog["node_id"], WorkerEvent(protocol_version="0.4.0", event_id=str(uuid4()), node_id=claim["node_id"], boot_id=claim["boot_id"], job_id=claim["job_id"], attempt_id=claim["attempt_id"], fencing_token=claim["fencing_token"], journal_seq=sequence, observed_at=int(utcnow().timestamp()), type=kind, payload=payload))
    return OutputRepository(pg, LocalDevelopmentStore(settings.local_storage_root), leases, path.parent / "spool"), leases, claim, spec


def test_complete_is_once_and_does_not_release_reused_gpu(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (1024, 1024), "navy").save(path)
    outputs, leases, claim, spec = ready(pg, catalog, test_settings, path)
    aid = parse_id("att", claim["attempt_id"])
    auth = {"protocol_version": "0.4.0", "boot_id": claim["boot_id"], "fencing_token": claim["fencing_token"]}
    plan = outputs.authorize(catalog["node_id"], aid, UploadAuthorization(**auth, outputs=[spec]))
    complete = Complete(**auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"])
    with pytest.raises(DomainError, match="云端验证"):
        outputs.complete(catalog["node_id"], aid, complete)
    upload_id = parse_id("upl", complete.upload_ids[0])
    outputs.accept(catalog["node_id"], upload_id, catalog["boot_id"], claim["fencing_token"], path)
    # Identical upload retry does not create/publish a second immutable object.
    outputs.accept(catalog["node_id"], upload_id, catalog["boot_id"], claim["fencing_token"], path)
    JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "second"}}, str(uuid4()), "test")
    second = leases.claim(catalog["node_id"], catalog["boot_id"])
    assert second
    result = outputs.complete(catalog["node_id"], aid, complete)
    assert outputs.complete(catalog["node_id"], aid, complete) == result
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).state == "succeeded"
        assert session.get(GpuOccupancy, catalog["gpu_id"]).attempt_id == parse_id("att", second["attempt_id"])
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.scalar(select(UsageLedger.image_count)) == 1


def test_corrupt_upload_cannot_publish_success(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (1024, 1024)).save(path)
    outputs, _, claim, spec = ready(pg, catalog, test_settings, path)
    auth = {"protocol_version": "0.4.0", "boot_id": claim["boot_id"], "fencing_token": claim["fencing_token"]}
    plan = outputs.authorize(catalog["node_id"], parse_id("att", claim["attempt_id"]), UploadAuthorization(**auth, outputs=[spec]))
    path.write_bytes(b"corrupt")
    with pytest.raises(DomainError, match="摘要不符"):
        outputs.accept(catalog["node_id"], parse_id("upl", plan["uploads"][0]["id"]), catalog["boot_id"], claim["fencing_token"], path)
    with pg() as session:
        assert session.get(Job, parse_id("job", claim["job_id"])).state == "uploading"
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
