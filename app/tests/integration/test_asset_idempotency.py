from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import pytest
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, utcnow
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.models import ApiKey, Asset, Idempotency, ObjectRecord
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def picture(color: str = "teal") -> BytesIO:
    source = BytesIO()
    Image.new("RGB", (32, 32), color).save(source, format="PNG")
    source.seek(0)
    return source


def test_asset_upload_concurrency_is_durable_without_storage_locks(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    identity = catalog["identity"]
    barrier = Barrier(8)
    probed = Barrier(8)

    class RacingStore(LocalDevelopmentStore):
        def put(self, *args: Any, **kwargs: Any) -> Any:
            # Probe only while all uploads are inside storage: preparation and
            # publication transactions must not contend with the lock probe.
            if barrier.wait(timeout=10) == 0:
                with pg.begin() as session:
                    session.execute(text("SET LOCAL lock_timeout='500ms'"))
                    session.execute(select(ApiKey.id).where(ApiKey.id == identity.key_id).with_for_update())
            probed.wait(timeout=10)
            return super().put(*args, **kwargs)

    store = RacingStore(tmp_path)
    repository = ContentRepository(pg, store)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: repository.upload_asset(identity, picture(), "synthetic-upload", "durable-same-upload"), range(8)))
    assert len({result["id"] for result in results}) == 1
    assert len({result["expires_at"] for result in results}) == 1
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 1
        assert session.scalar(select(func.count()).select_from(Idempotency).where(Idempotency.operation == "upload_asset")) == 1
    assert sum(path.is_file() for path in tmp_path.rglob("*")) == 1
    restarted = ContentRepository(pg, LocalDevelopmentStore(tmp_path))
    assert restarted.upload_asset(identity, picture(), "new-process", "durable-same-upload") == results[0]
    with pytest.raises(DomainError) as conflict:
        restarted.upload_asset(identity, picture("red"), "synthetic", "durable-same-upload")
    assert conflict.value.status == 409


@pytest.mark.parametrize("unavailable", ["expired", "deleted"])
def test_asset_upload_replay_does_not_extend_or_resurrect(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path, unavailable: str) -> None:
    repository = ContentRepository(pg, LocalDevelopmentStore(tmp_path))
    identity = catalog["identity"]
    result = repository.upload_asset(identity, picture(), "synthetic", "unavailable-upload")
    with pg.begin() as session:
        asset = session.get(Asset, parse_id("asset", result["id"]))
        assert asset is not None
        if unavailable == "expired":
            obj = session.get(ObjectRecord, asset.object_id)
            assert obj is not None
            obj.expires_at = utcnow() - timedelta(seconds=1)
        else:
            asset.deleted_at = utcnow()
    with pytest.raises(DomainError) as failure:
        repository.upload_asset(identity, picture(), "synthetic", "unavailable-upload")
    assert failure.value.status == 410
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
    fresh = repository.upload_asset(identity, picture(), "synthetic", "different-explicit-upload")
    assert fresh["id"] != result["id"]


def test_asset_upload_rechecks_revocation_after_storage(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    entered, proceed = Event(), Event()

    class PausedStore(LocalDevelopmentStore):
        def put(self, *args: Any, **kwargs: Any) -> Any:
            value = super().put(*args, **kwargs)
            entered.set()
            assert proceed.wait(timeout=10)
            return value

    identity = catalog["identity"]
    repository = ContentRepository(pg, PausedStore(tmp_path))
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(repository.upload_asset, identity, picture(), "synthetic", "revoked-upload")
        assert entered.wait(timeout=10)
        try:
            with pg.begin() as session:
                key = session.get(ApiKey, identity.key_id)
                assert key is not None
                key.enabled = False
        finally:
            proceed.set()
        with pytest.raises(DomainError) as failure:
            pending.result(timeout=10)
        assert failure.value.status == 403
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 0
        assert session.scalar(select(func.count()).select_from(ObjectRecord)) == 0
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_asset_upload_commit_ack_loss_preserves_published_version(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    class UncertainSession(Session):
        pass

    lost = False

    @event.listens_for(UncertainSession, "after_commit")
    def fail_ack(session: Session) -> None:
        nonlocal lost
        if not lost and any(isinstance(row, Asset) for row in session.identity_map.values()):
            lost = True
            raise RuntimeError("synthetic commit acknowledgement lost")

    sessions = sessionmaker(bind=pg.kw["bind"], class_=UncertainSession, expire_on_commit=False)
    store = LocalDevelopmentStore(tmp_path)
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        ContentRepository(sessions, store).upload_asset(catalog["identity"], picture(), "synthetic", "lost-commit-ack")
    assert lost and sum(path.is_file() for path in tmp_path.rglob("*")) == 1
    replay = ContentRepository(pg, store).upload_asset(catalog["identity"], picture(), "synthetic", "lost-commit-ack")
    assert replay["status"] == "ready"
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
