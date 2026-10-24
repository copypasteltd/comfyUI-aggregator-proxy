from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from generation_application.ports import BusinessIdentity
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, utcnow
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.garbage_collection import ObjectGarbageCollector
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import (
    ApiKey,
    Asset,
    JobAssetReference,
    ObjectRecord,
    PublicModel,
    WorkflowRelease,
)
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def image() -> BytesIO:
    source = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(source, "PNG")
    source.seek(0)
    return source


def deletion_identity(pg: sessionmaker[Session], catalog: dict[str, Any]) -> BusinessIdentity:
    original = catalog["identity"]
    policy = {**original.policy, "scopes": [*original.policy["scopes"], "content:delete"]}
    with pg.begin() as session:
        session.get(ApiKey, original.key_id).policy = policy
        model = session.scalar(select(PublicModel))
        old_release = session.get(WorkflowRelease, model.release_id)
        manifest = {**old_release.manifest, "task_type": "image_to_image"}
        release = WorkflowRelease(tenant_id=model.tenant_id, project_id=model.project_id, workflow_id=old_release.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        model.task_type, model.release_id = "image_to_image", release.id
    return BusinessIdentity(original.tenant_id, original.project_id, original.key_id, policy)


def input_request(identifier: str) -> dict[str, Any]:
    return {"model": "synthetic-image", "task_type": "image_to_image", "input": {"prompt": "Synthetic retention test", "source_image": {"asset_id": identifier}}}


def test_active_inputs_stay_pinned_and_terminal_retention_is_extended(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    identity = deletion_identity(pg, catalog)
    store = LocalDevelopmentStore(tmp_path)
    contents, jobs, collector = ContentRepository(pg, store), JobRepository(pg), ObjectGarbageCollector(pg, store)
    asset = contents.upload_asset(identity, image(), "retention")
    aid = parse_id("asset", asset["id"])
    job = jobs.create(identity, input_request(asset["id"]), str(uuid4()), "retention")
    with pg.begin() as session:
        obj = session.get(ObjectRecord, session.get(Asset, aid).object_id)
        oid = obj.id
        obj.expires_at = utcnow() - timedelta(seconds=1)
        assert session.scalar(select(func.count()).select_from(JobAssetReference)) == 1
    assert collector.tick()["deleted"] == 0
    with pytest.raises(DomainError) as error:
        contents.delete(identity, aid, "retention", asset=True)
    assert error.value.code == "content_in_use"
    jobs.cancel(identity, parse_id("job", job["id"]), "retention")
    with pg() as session:
        assert session.get(ObjectRecord, oid).expires_at >= utcnow() + timedelta(hours=23, minutes=59)
    assert contents.metadata(identity, aid, asset=True)["id"] == asset["id"]
    contents.delete(identity, aid, "retention", asset=True)
    with pytest.raises(DomainError):
        contents.metadata(identity, aid, asset=True)
    with pg.begin() as session:
        session.get(ObjectRecord, oid).gc_claim_until = None
    assert collector.tick()["deleted"] == 1
    with pg() as session:
        assert session.get(ObjectRecord, oid).deleted_at


def test_delete_and_new_admission_cannot_both_succeed(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    identity = deletion_identity(pg, catalog)
    contents, jobs = ContentRepository(pg, LocalDevelopmentStore(tmp_path)), JobRepository(pg)
    for _ in range(8):
        asset = contents.upload_asset(identity, image(), "race")
        barrier = Barrier(2)

        def admit(sync: Barrier, identifier: str) -> dict[str, Any] | None:
            sync.wait(timeout=5)
            try:
                return jobs.create(identity, input_request(identifier), str(uuid4()), "race")
            except DomainError as error:
                assert error.code == "asset_not_available"
                return None

        def remove(sync: Barrier, identifier: str) -> bool:
            sync.wait(timeout=5)
            try:
                contents.delete(identity, parse_id("asset", identifier), "race", asset=True)
                return True
            except DomainError as error:
                assert error.code == "content_in_use"
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted, removed = pool.submit(admit, barrier, asset["id"]), pool.submit(remove, barrier, asset["id"])
            job, deleted = admitted.result(timeout=20), removed.result(timeout=20)
        assert (job is not None) != deleted
        if job:
            jobs.cancel(identity, parse_id("job", job["id"]), "race")


def test_lost_delete_ack_retries_only_the_claimed_version(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    class LostAcknowledgementStore(LocalDevelopmentStore):
        calls: list[tuple[str, str]] = []

        def delete(self, key: str, version: str) -> None:
            self.calls.append((key, version))
            super().delete(key, version)
            if len(self.calls) == 1:
                raise OSError("Synthetic lost acknowledgement")

    identity = deletion_identity(pg, catalog)
    store = LostAcknowledgementStore(tmp_path)
    contents, collector = ContentRepository(pg, store), ObjectGarbageCollector(pg, store)
    asset = contents.upload_asset(identity, image(), "lost-ack")
    with pg() as session:
        original = session.get(ObjectRecord, session.get(Asset, parse_id("asset", asset["id"])).object_id)
    newer = store.put(original.object_key, image(), "image/png", 100000)
    contents.delete(identity, parse_id("asset", asset["id"]), "lost-ack", asset=True)
    assert collector.tick() == {"claimed": 1, "deleted": 0, "retry": 1}
    with pg.begin() as session:
        obj = session.get(ObjectRecord, original.id)
        assert obj.deleted_at is None
        obj.gc_claim_until = utcnow() - timedelta(seconds=1)
    assert collector.tick()["deleted"] == 1
    assert store.calls == [(original.object_key, original.version_id)] * 2
    assert b"".join(store.read(newer.key, newer.version, 0, newer.bytes - 1)).startswith(b"\x89PNG")


def test_parallel_gc_claims_do_not_overlap(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    identity = deletion_identity(pg, catalog)
    store = LocalDevelopmentStore(tmp_path)
    contents, collector = ContentRepository(pg, store), ObjectGarbageCollector(pg, store)
    for _ in range(12):
        asset = contents.upload_asset(identity, image(), "parallel-gc")
        contents.delete(identity, parse_id("asset", asset["id"]), "parallel-gc", asset=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(collector.claim, 10), pool.submit(collector.claim, 10)
        claims = a.result(timeout=10) + b.result(timeout=10)
    assert len(claims) == len({claim.object_id for claim in claims}) == 12
    assert all(collector.collect(claim) for claim in claims)


def test_input_reference_scope_is_also_enforced_by_postgres(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    identity = deletion_identity(pg, catalog)
    contents = ContentRepository(pg, LocalDevelopmentStore(tmp_path))
    asset = contents.upload_asset(identity, image(), "scope")
    job = JobRepository(pg).create(identity, input_request(asset["id"]), str(uuid4()), "scope")
    with pg.begin() as session:
        other = ApiKey(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Synthetic other owner", policy=identity.policy)
        session.add(other)
        session.flush()
        obj_id = session.get(Asset, parse_id("asset", asset["id"])).object_id
        foreign = Asset(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=other.id, object_id=obj_id)
        session.add(foreign)
        session.flush()
        foreign_id = foreign.id
    with pytest.raises(IntegrityError), pg.begin() as session:
        session.add(JobAssetReference(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, job_id=parse_id("job", job["id"]), asset_id=foreign_id))
