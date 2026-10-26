from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO

import pytest
from generation_application.storage import StoredObject
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, utcnow
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.models import (
    Artifact,
    Attempt,
    Job,
    ObjectRecord,
    OutputUpload,
    UsageLedger,
)
from generation_infrastructure.outputs import OutputRepository
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_outputs import ready

pytestmark = pytest.mark.integration
PRIVATE_FAILURE = "synthetic-private-cleanup-diagnostic"


class LostCommitAcknowledgement(RuntimeError):
    pass


class PublicationRolledBack(RuntimeError):
    pass


class TrackedStore(LocalDevelopmentStore):
    def __init__(self, root: Path, pg: sessionmaker[Session]) -> None:
        super().__init__(root)
        self.pg = pg
        self.writes: list[StoredObject] = []
        self.deletes: list[tuple[str, str]] = []
        self.fail_delete = False
        self.bad_digest = False

    def put(self, key: str, source: BinaryIO, media_type: str, maximum: int) -> StoredObject:
        stored = super().put(key, source, media_type, maximum)
        self.writes.append(stored)
        return replace(stored, sha256="0" * 64) if self.bad_digest else stored

    def delete(self, key: str, version: str) -> None:
        # External I/O may not keep the publication-check connection checked out.
        assert self.pg.kw["bind"].pool.checkedout() == 0
        self.deletes.append((key, version))
        if self.fail_delete:
            raise OSError(PRIVATE_FAILURE)
        super().delete(key, version)


@pytest.fixture
def pending(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "output.png"
    Image.new("RGB", (1024, 1024), "navy").save(path)
    repo, _, claim, spec = ready(pg, catalog, test_settings, path)
    store = TrackedStore(tmp_path / "objects", pg)
    repo.store = store
    auth = {"protocol_version": "0.4.0", "boot_id": claim["boot_id"], "fencing_token": claim["fencing_token"]}
    aid = parse_id("att", claim["attempt_id"])
    plan = repo.authorize(catalog["node_id"], aid, UploadAuthorization(**auth, outputs=[spec]))
    return {"repo":repo, "store":store, "path":path, "claim":claim, "aid":aid, "upload":parse_id("upl", plan["uploads"][0]["id"]), "complete":Complete(**auth, upload_ids=[plan["uploads"][0]["id"]], manifest_digest=plan["manifest_digest"])}


def accept(state: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    repo: OutputRepository = state["repo"]
    return repo.accept(catalog["node_id"], state["upload"], catalog["boot_id"], state["claim"]["fencing_token"], state["path"])


def fault_sessions(pg: sessionmaker[Session], *, after_commit: bool, lookup_available: bool) -> tuple[sessionmaker[Session], dict[str, Any]]:
    class FaultSession(Session):
        pass

    observed: dict[str, Any] = {"fired":False, "failed_lookups":0}

    def fail_publication(session: Session) -> None:
        if not observed["fired"] and any(isinstance(row, ObjectRecord) for row in session.identity_map.values()):
            observed["fired"] = True
            if after_commit:
                raise LostCommitAcknowledgement("synthetic commit acknowledgement lost")
            raise PublicationRolledBack("synthetic publication rolled back")

    event.listen(FaultSession, "after_commit" if after_commit else "before_commit", fail_publication)

    @event.listens_for(FaultSession, "do_orm_execute")
    def fail_lookup(state: Any) -> None:
        if observed["fired"] and not lookup_available and state.is_select:
            observed["failed_lookups"] += 1
            raise OSError(PRIVATE_FAILURE)

    return sessionmaker(bind=pg.kw["bind"], class_=FaultSession, expire_on_commit=False), observed


def finish_once(pg: sessionmaker[Session], state: dict[str, Any], catalog: dict[str, Any]) -> None:
    state["repo"].sessions = pg
    state["store"].fail_delete = state["store"].bad_digest = False
    assert accept(state, catalog)["verified"] is True
    manifest = state["repo"].complete(catalog["node_id"], state["aid"], state["complete"])
    assert state["repo"].complete(catalog["node_id"], state["aid"], state["complete"]) == manifest
    content_id = parse_id("content", manifest["artifacts"][0]["id"])
    download = ContentRepository(pg, state["store"]).download(catalog["identity"], content_id, None)
    assert b"".join(download.data) == state["path"].read_bytes()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(Attempt)) == 1
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 1
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert session.scalar(select(UsageLedger.image_count)) == 1


@pytest.mark.parametrize("lookup_available", [True, False])
def test_committed_output_survives_lost_commit_ack(pg: sessionmaker[Session], catalog: dict[str, Any], pending: dict[str, Any], caplog: pytest.LogCaptureFixture, lookup_available: bool) -> None:
    sessions, observed = fault_sessions(pg, after_commit=True, lookup_available=lookup_available)
    pending["repo"].sessions = sessions
    with pytest.raises(LostCommitAcknowledgement):
        accept(pending, catalog)
    assert observed["fired"]
    stored = pending["store"].writes[0]
    with pg() as session:
        upload = session.get(OutputUpload, pending["upload"])
        assert upload and upload.object_id
        obj = session.get(ObjectRecord, upload.object_id)
        assert obj and (obj.object_key, obj.version_id) == (stored.key, stored.version)
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0
    assert b"".join(pending["store"].read(stored.key, stored.version, 0, stored.bytes - 1)) == pending["path"].read_bytes()
    assert not pending["store"].deletes
    if not lookup_available:
        assert observed["failed_lookups"] == 1
        assert "output_cleanup_deferred_pending_publication_verification" in caplog.text
    assert PRIVATE_FAILURE not in caplog.text
    finish_once(pg, pending, catalog)
    assert (stored.key, stored.version) not in pending["store"].deletes
    assert sum(p.is_file() for p in pending["store"].root.rglob("*")) == 1


@pytest.mark.parametrize("lookup_available", [True, False])
def test_rollback_cleanup_requires_successful_absence_read(pg: sessionmaker[Session], catalog: dict[str, Any], pending: dict[str, Any], caplog: pytest.LogCaptureFixture, lookup_available: bool) -> None:
    sessions, observed = fault_sessions(pg, after_commit=False, lookup_available=lookup_available)
    pending["repo"].sessions = sessions
    with pytest.raises(PublicationRolledBack):
        accept(pending, catalog)
    assert observed["fired"]
    stored = pending["store"].writes[0]
    with pg() as session:
        assert session.get(OutputUpload, pending["upload"]).object_id is None
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 0
    assert pending["store"]._path(stored.key, stored.version).exists() is (not lookup_available)
    assert pending["store"].deletes == ([(stored.key, stored.version)] if lookup_available else [])
    assert PRIVATE_FAILURE not in caplog.text
    finish_once(pg, pending, catalog)


@pytest.mark.parametrize("integrity_failure", [False, True])
def test_cleanup_delete_failure_preserves_original_error(pg: sessionmaker[Session], catalog: dict[str, Any], pending: dict[str, Any], caplog: pytest.LogCaptureFixture, integrity_failure: bool) -> None:
    pending["store"].fail_delete = True
    if integrity_failure:
        pending["store"].bad_digest = True
        expected = DomainError
    else:
        pending["repo"].sessions, _ = fault_sessions(pg, after_commit=False, lookup_available=True)
        expected = PublicationRolledBack
    with pytest.raises(expected) as failed:
        accept(pending, catalog)
    if integrity_failure:
        assert failed.value.code == "cloud_object_mismatch"
    assert len(pending["store"].deletes) == 1
    assert "output_cleanup_deferred_pending_publication_verification" in caplog.text
    assert PRIVATE_FAILURE not in caplog.text
    stored = pending["store"].writes[0]
    assert pending["store"]._path(stored.key, stored.version).exists()
    finish_once(pg, pending, catalog)


@pytest.mark.parametrize("delete_available", [True, False])
def test_duplicate_cleanup_keeps_original_published_version(pg: sessionmaker[Session], catalog: dict[str, Any], pending: dict[str, Any], caplog: pytest.LogCaptureFixture, delete_available: bool) -> None:
    assert accept(pending, catalog)["verified"]
    original = pending["store"].writes[0]
    pending["store"].fail_delete = not delete_available
    assert accept(pending, catalog)["verified"]
    duplicate = pending["store"].writes[1]
    assert pending["store"].deletes == [(duplicate.key, duplicate.version)]
    assert original.version != duplicate.version
    assert b"".join(pending["store"].read(original.key, original.version, 0, original.bytes - 1)) == pending["path"].read_bytes()
    assert pending["store"]._path(duplicate.key, duplicate.version).exists() is (not delete_available)
    assert PRIVATE_FAILURE not in caplog.text
    finish_once(pg, pending, catalog)


def test_cleanup_waits_for_inflight_publication_before_absence_read(pg: sessionmaker[Session], catalog: dict[str, Any], pending: dict[str, Any]) -> None:
    with pg() as session:
        key = session.get(OutputUpload, pending["upload"]).object_key
    with pending["path"].open("rb") as source:
        stored = pending["store"].put(key, source, "image/png", pending["path"].stat().st_size)
    publishing, release, checking = Event(), Event(), Event()
    cleanup_pid: list[int] = []

    class CleanupSession(Session):
        pass

    @event.listens_for(CleanupSession, "do_orm_execute")
    def observed_lookup(state: Any) -> None:
        if state.is_select and not cleanup_pid:
            cleanup_pid.append(state.session.connection().connection.driver_connection.info.backend_pid)
            checking.set()

    pending["repo"].sessions = sessionmaker(bind=pg.kw["bind"], class_=CleanupSession, expire_on_commit=False)

    def publish() -> None:
        with pg.begin() as session:
            upload = session.scalar(select(OutputUpload).where(OutputUpload.id == pending["upload"]).with_for_update())
            identity = catalog["identity"]
            obj = ObjectRecord(tenant_id=identity.tenant_id, project_id=identity.project_id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=stored.media_type, verified_at=utcnow(), metadata_json={"media_type":"image/png", "width":1024, "height":1024}, expires_at=utcnow()+timedelta(days=7))
            session.add(obj)
            session.flush()
            upload.object_id = obj.id
            session.flush()
            publishing.set()
            assert release.wait(10), "Synthetic publisher must be released by the test"

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(publish)
        try:
            assert publishing.wait(5)
            cleaner = pool.submit(pending["repo"]._discard_unpublished, pending["upload"], stored.key, stored.version)
            assert checking.wait(5)
            blocked = False
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with pg() as session:
                    blocked = bool(session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"), {"pid":cleanup_pid[0]}))
                if blocked:
                    break
                assert not cleaner.done(), "Cleanup finished before the publisher's transaction"
                time.sleep(0.02)
            assert blocked, "PostgreSQL must show cleanup waiting for the original publication lock"
        finally:
            release.set()
        writer.result(timeout=10)
        cleaner.result(timeout=10)
    assert not pending["store"].deletes
    assert b"".join(pending["store"].read(stored.key, stored.version, 0, stored.bytes-1)) == pending["path"].read_bytes()
    finish_once(pg, pending, catalog)
