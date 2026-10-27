from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, utcnow
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.garbage_collection import ObjectGarbageCollector
from generation_infrastructure.models import (
    Artifact,
    Asset,
    AuditEvent,
    Job,
    ObjectRecord,
    UsageLedger,
)
from generation_infrastructure.output_retention import shorten_existing_outputs
from generation_infrastructure.settings import Settings
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_outputs import ready

pytestmark = pytest.mark.integration


@pytest.fixture
def delivered(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "retention.png"
    Image.new("RGB", (1024, 1024), "green").save(path)
    repo, _, claim, spec = ready(pg, catalog, test_settings, path)
    aid = parse_id("att", claim["attempt_id"])
    auth = {"protocol_version": "0.4.0", "boot_id": claim["boot_id"], "fencing_token": claim["fencing_token"]}
    plan = repo.authorize(catalog["node_id"], aid, UploadAuthorization(**auth, outputs=[spec]))
    repo.accept(catalog["node_id"], parse_id("upl", plan["uploads"][0]["id"]), catalog["boot_id"], claim["fencing_token"], path)
    result = repo.complete(catalog["node_id"], aid, Complete(**auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"]))
    cid = parse_id("content", result["artifacts"][0]["id"])
    with pg() as session:
        artifact = session.get(Artifact, cid)
        job = session.get(Job, artifact.job_id)
        return {"repo": repo, "content_id": cid, "object_id": artifact.object_id, "job_id": job.id, "completed_at": job.terminal_at, "path": path}


def test_output_access_and_gc_switch_at_eight_hours(pg: sessionmaker[Session], catalog: dict[str, Any], delivered: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import generation_infrastructure.content as content
    import generation_infrastructure.garbage_collection as gc

    store = delivered["repo"].store
    contents = ContentRepository(pg, store)
    collector = ObjectGarbageCollector(pg, store)
    expires = delivered["completed_at"] + timedelta(hours=8)
    clock = expires - timedelta(microseconds=1)
    monkeypatch.setattr(content, "utcnow", lambda: clock)
    monkeypatch.setattr(gc, "utcnow", lambda: clock)
    assert contents.metadata(catalog["identity"], delivered["content_id"])["expires_at"] == int(expires.timestamp())
    assert collector.tick()["deleted"] == 0
    clock = expires
    with pytest.raises(DomainError) as error:
        contents.metadata(catalog["identity"], delivered["content_id"])
    assert error.value.status == 404
    assert collector.tick() == {"claimed": 1, "deleted": 1, "retry": 0}
    assert collector.tick()["deleted"] == 0
    with pg() as session:
        obj = session.get(ObjectRecord, delivered["object_id"])
        assert obj.deleted_at == expires
        assert session.get(Job, delivered["job_id"]).state == "succeeded"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
    with pytest.raises(FileNotFoundError):
        b"".join(store.read(obj.object_key, obj.version_id, 0, obj.bytes - 1))


@pytest.mark.parametrize("task_type", ["text_to_image", "text_to_video", "text_to_audio"])
def test_backfill_is_audited_idempotent_and_preserves_inputs_and_unpublished_objects(pg: sessionmaker[Session], catalog: dict[str, Any], delivered: dict[str, Any], task_type: str) -> None:
    contents = ContentRepository(pg, delivered["repo"].store)
    with delivered["path"].open("rb") as source:
        input_asset = contents.upload_asset(catalog["identity"], source, "retention-input")
    with delivered["path"].open("rb") as source:
        stored = delivered["repo"].store.put("unpublished/retention", source, "image/png", 32 * 1024 * 1024)
    legacy_expiry = utcnow() + timedelta(days=7)
    with pg.begin() as session:
        session.get(Job, delivered["job_id"]).task_type = task_type
        session.get(ObjectRecord, delivered["object_id"]).expires_at = legacy_expiry
        pending = ObjectRecord(tenant_id=catalog["identity"].tenant_id, project_id=catalog["identity"].project_id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=stored.media_type, verified_at=utcnow(), metadata_json={}, expires_at=legacy_expiry)
        session.add(pending)
        session.flush()
        pending_id = pending.id
    assert shorten_existing_outputs(pg)["selected"] == 1
    with pg() as session:
        assert session.get(ObjectRecord, delivered["object_id"]).expires_at == legacy_expiry
    assert shorten_existing_outputs(pg, apply=True, maximum=1)["updated"] == 1
    assert shorten_existing_outputs(pg, apply=True)["updated"] == 0
    with pg() as session:
        assert session.get(ObjectRecord, delivered["object_id"]).expires_at == delivered["completed_at"] + timedelta(hours=8)
        assert session.get(ObjectRecord, pending_id).expires_at == legacy_expiry
        audit = session.scalars(select(AuditEvent).where(AuditEvent.action == "content.retention_shortened")).one()
        assert audit.resource == str(delivered["object_id"])
        assert audit.detail["cutoff"] == int((delivered["completed_at"] + timedelta(hours=8)).timestamp())
    assert contents.metadata(catalog["identity"], parse_id("asset", input_asset["id"]), asset=True)["expires_at"] == input_asset["expires_at"]


@pytest.mark.parametrize("input_backed", [False, True])
def test_backfill_never_extends_earlier_expiry_or_shortens_input_backed_output(pg: sessionmaker[Session], catalog: dict[str, Any], delivered: dict[str, Any], input_backed: bool) -> None:
    expiry = delivered["completed_at"] + timedelta(hours=24 if input_backed else 2)
    with pg.begin() as session:
        session.get(ObjectRecord, delivered["object_id"]).expires_at = expiry
        if input_backed:
            session.add(Asset(tenant_id=catalog["identity"].tenant_id, project_id=catalog["identity"].project_id, key_id=catalog["identity"].key_id, object_id=delivered["object_id"]))
    assert shorten_existing_outputs(pg, apply=True)["updated"] == 0
    with pg() as session:
        assert session.get(ObjectRecord, delivered["object_id"]).expires_at == expiry
