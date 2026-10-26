from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from generation_application.ports import BusinessIdentity
from generation_domain.values import parse_id
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.models import ApiKey, Asset, Idempotency, ObjectRecord
from generation_infrastructure.storage import LocalDevelopmentStore
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_asset_idempotency import picture

pytestmark = pytest.mark.integration


@pytest.fixture
def asset_identity(pg: sessionmaker[Session], catalog: dict[str, Any]) -> BusinessIdentity:
    identity = catalog["identity"]
    policy = deepcopy(identity.policy)
    policy["scopes"] = sorted(set(policy["scopes"]) | {"assets:read", "content:read", "content:download", "content:sensitive"})
    with pg.begin() as session:
        session.get(ApiKey, identity.key_id).policy = policy
    return replace(identity, policy=policy)


@pytest.mark.parametrize("token", [None, "synthetic-inflight-asset"])
def test_asset_cleanup_waits_for_publication_commit(pg: sessionmaker[Session], asset_identity: BusinessIdentity, tmp_path: Path, token: str | None) -> None:
    class TrackedStore(LocalDevelopmentStore):
        def put(self, *args: Any, **kwargs: Any) -> Any:
            value = super().put(*args, **kwargs)
            writes.append(value)
            return value

        def delete(self, key: str, version: str) -> None:
            deletes.append((key, version))
            super().delete(key, version)

    class PublisherSession(Session):
        pass

    class CleanupSession(Session):
        pass

    publishing, release, checking = Event(), Event(), Event()
    writes: list[Any] = []
    deletes: list[tuple[str, str]] = []
    cleanup_pid: list[int] = []

    @event.listens_for(PublisherSession, "before_commit")
    def pause_publication(session: Session) -> None:
        if any(isinstance(row, Asset) for row in session.identity_map.values()):
            publishing.set()
            assert release.wait(10), "Synthetic publication must be released"

    @event.listens_for(CleanupSession, "do_orm_execute")
    def observe_lookup(state: Any) -> None:
        if state.is_select and not cleanup_pid:
            cleanup_pid.append(state.session.connection().connection.driver_connection.info.backend_pid)
            checking.set()

    store = TrackedStore(tmp_path)
    publishing_repo = ContentRepository(sessionmaker(bind=pg.kw["bind"], class_=PublisherSession, expire_on_commit=False), store)
    cleanup_repo = ContentRepository(sessionmaker(bind=pg.kw["bind"], class_=CleanupSession, expire_on_commit=False), store)
    identity = asset_identity
    original = picture().getvalue()
    blocked = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(publishing_repo.upload_asset, identity, picture(), "synthetic-publish", token)
        try:
            assert publishing.wait(5)
            stored = writes[0]
            cleaner = pool.submit(cleanup_repo._discard_unpublished, stored.key, stored.version, identity.key_id)
            assert checking.wait(5)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with pg() as session:
                    blocked = bool(session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"), {"pid": cleanup_pid[0]}))
                if blocked or cleaner.done():
                    break
                time.sleep(0.02)
        finally:
            release.set()
        result = writer.result(timeout=10)
        cleaner.result(timeout=10)

    healthy = ContentRepository(pg, store)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 1
    download = healthy.download(identity, parse_id("asset", result["id"]), None, asset=True)
    assert b"".join(download.data) == original
    assert blocked, "PostgreSQL must show cleanup waiting for original publication"
    assert not deletes
    if token is not None:
        assert healthy.upload_asset(identity, picture(), "synthetic-replay", token) == result
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 1


@pytest.mark.parametrize("lookup_available", [True, False])
def test_asset_rollback_cleanup_requires_verified_absence(pg: sessionmaker[Session], asset_identity: BusinessIdentity, tmp_path: Path, caplog: pytest.LogCaptureFixture, lookup_available: bool) -> None:
    class PublicationRolledBack(RuntimeError):
        pass

    class FaultSession(Session):
        pass

    class TrackedStore(LocalDevelopmentStore):
        def put(self, *args: Any, **kwargs: Any) -> Any:
            value = super().put(*args, **kwargs)
            writes.append(value)
            return value

        def delete(self, key: str, version: str) -> None:
            assert pg.kw["bind"].pool.checkedout() == 0
            deletes.append((key, version))
            super().delete(key, version)

    failed = False
    writes: list[Any] = []
    deletes: list[tuple[str, str]] = []
    private_failure = "synthetic-private-asset-cleanup-failure"

    @event.listens_for(FaultSession, "before_commit")
    def rollback_publication(session: Session) -> None:
        nonlocal failed
        if not failed and any(isinstance(row, Asset) for row in session.identity_map.values()):
            failed = True
            raise PublicationRolledBack("synthetic publication rollback")

    @event.listens_for(FaultSession, "do_orm_execute")
    def fail_cleanup_query(state: Any) -> None:
        if failed and state.is_select and not lookup_available:
            raise RuntimeError(private_failure)

    store = TrackedStore(tmp_path)
    repo = ContentRepository(sessionmaker(bind=pg.kw["bind"], class_=FaultSession, expire_on_commit=False), store)
    identity = asset_identity
    with pytest.raises(PublicationRolledBack, match="synthetic publication rollback"):
        repo.upload_asset(identity, picture(), "synthetic-rollback", "synthetic-rollback-token")
    original = writes[0]
    assert store._path(original.key, original.version).exists() is (not lookup_available)
    assert deletes == ([(original.key, original.version)] if lookup_available else [])
    assert private_failure not in caplog.text
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 0
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 0
    healthy = ContentRepository(pg, store)
    result = healthy.upload_asset(identity, picture(), "synthetic-retry", "synthetic-rollback-token")
    assert b"".join(healthy.download(identity, parse_id("asset", result["id"]), None, asset=True).data) == picture().getvalue()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 1


@pytest.mark.parametrize("delete_available", [True, False])
def test_asset_duplicate_cleanup_releases_database_before_delete(pg: sessionmaker[Session], asset_identity: BusinessIdentity, tmp_path: Path, caplog: pytest.LogCaptureFixture, delete_available: bool) -> None:
    writes: list[Any] = []
    deletes: list[tuple[str, str]] = []
    private_failure = "synthetic-private-asset-delete-failure"

    class TrackedStore(LocalDevelopmentStore):
        def put(self, *args: Any, **kwargs: Any) -> Any:
            value = super().put(*args, **kwargs)
            writes.append(value)
            return value

        def delete(self, key: str, version: str) -> None:
            assert pg.kw["bind"].pool.checkedout() == 0
            deletes.append((key, version))
            if not delete_available:
                raise RuntimeError(private_failure)
            super().delete(key, version)

    store = TrackedStore(tmp_path)
    repo = ContentRepository(pg, store)
    identity = asset_identity
    first = repo.upload_asset(identity, picture(), "synthetic-first", "synthetic-duplicate-token")
    # Force the public upload's publication-time duplicate branch. The first
    # lookup succeeds before storage in normal replay, so hide only that lookup.
    class MissPreflight(Session):
        pass

    reads = 0

    @event.listens_for(MissPreflight, "do_orm_execute", retval=True)
    def hide_preflight(state: Any) -> Any:
        nonlocal reads
        if state.is_select and state.bind_mapper is not None and state.bind_mapper.class_ is Idempotency:
            reads += 1
            if reads == 1:
                return state.invoke_statement(statement=state.statement.where(Idempotency.id.is_(None)))
        return state.invoke_statement()

    duplicate_repo = ContentRepository(sessionmaker(bind=pg.kw["bind"], class_=MissPreflight, expire_on_commit=False), store)
    assert duplicate_repo.upload_asset(identity, picture(), "synthetic-duplicate", "synthetic-duplicate-token") == first
    original, duplicate = writes
    assert deletes == [(duplicate.key, duplicate.version)]
    assert store._path(original.key, original.version).exists()
    assert store._path(duplicate.key, duplicate.version).exists() is (not delete_available)
    assert private_failure not in caplog.text
    assert b"".join(repo.download(identity, parse_id("asset", first["id"]), None, asset=True).data) == picture().getvalue()
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 1
