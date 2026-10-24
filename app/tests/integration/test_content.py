from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from generation_application.ports import BusinessIdentity
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from generation_infrastructure.content import ContentRepository, requested_range, validate_image
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import ApiKey, Artifact, ObjectRecord
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def image_bytes() -> BytesIO:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "teal").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def test_input_permissions_and_real_file_verification(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    repository = ContentRepository(pg, LocalDevelopmentStore(tmp_path))
    identity = catalog["identity"]
    asset = repository.upload_asset(identity, image_bytes(), "synthetic")
    assert asset["status"] == "ready" and asset["width"] == 32
    with pytest.raises(DomainError, match="敏感|content:sensitive"):
        repository.download(identity, parse_id("asset", asset["id"]), None, asset=True)
    policy = {**identity.policy, "scopes": [*identity.policy["scopes"], "content:sensitive"]}
    identity = BusinessIdentity(identity.tenant_id, identity.project_id, identity.key_id, policy)
    authorized = repository.download(identity, parse_id("asset", asset["id"]), "bytes=0-7", asset=True)
    assert authorized.status == 206
    assert b"".join(authorized.data) == b"\x89PNG\r\n\x1a\n"
    head = repository.download(identity, parse_id("asset", asset["id"]), None, asset=True, head=True)
    assert b"".join(head.data) == b"" and int(head.headers["Content-Length"]) > 0
    with pytest.raises(DomainError):
        validate_image(BytesIO(b"Synthetic file pretending to be a picture"))


def test_content_sharing_stays_inside_tenant_and_is_explicit(pg: sessionmaker[Session], catalog: dict[str, Any], tmp_path: Path) -> None:
    identity = catalog["identity"]
    jobs = JobRepository(pg)
    job = jobs.create(identity, {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "synthetic"}}, "same-synthetic-content-token", "synthetic")
    store = LocalDevelopmentStore(tmp_path)
    stored = store.put("outputs/synthetic", image_bytes(), "image/png", 1024 * 1024)
    with pg.begin() as session:
        obj = ObjectRecord(tenant_id=identity.tenant_id, project_id=identity.project_id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=stored.media_type, verified_at=utcnow(), expires_at=utcnow() + timedelta(days=7))
        session.add(obj)
        session.flush()
        artifact = Artifact(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, job_id=parse_id("job", job["id"]), object_id=obj.id, position=0)
        other_key = ApiKey(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Synthetic other key", policy=identity.policy)
        session.add_all([artifact, other_key])
        session.flush()
        identifier, other_id = artifact.id, other_key.id
    repository = ContentRepository(pg, store)
    other = BusinessIdentity(identity.tenant_id, identity.project_id, other_id, identity.policy)
    with pytest.raises(DomainError, match="未授权"):
        repository.download(other, identifier, None)
    shared_policy = {**identity.policy, "content": {"mode": "project_shared", "project_ids": [public_id("prj", identity.project_id)], "creator_key_ids": []}}
    shared = BusinessIdentity(identity.tenant_id, identity.project_id, other_id, shared_policy)
    assert repository.download(shared, identifier, "bytes=-8").status == 206
    shared_delete = BusinessIdentity(identity.tenant_id, identity.project_id, other_id, {**shared_policy, "scopes": [*shared_policy["scopes"], "content:delete"]})
    with pytest.raises(DomainError) as error:
        repository.delete(shared_delete, identifier, "shared-cannot-delete")
    assert error.value.status == 404
    revoked = {**shared_policy, "scopes": [s for s in shared_policy["scopes"] if s != "content:download"]}
    with pytest.raises(DomainError):
        repository.download(BusinessIdentity(identity.tenant_id, identity.project_id, other_id, revoked), identifier, None, head=True)


@pytest.mark.parametrize("header", ["bytes=0-1,3-4", "bytes=100-", "bytes=-0", "items=0-1", "bytes=5-2"])
def test_invalid_ranges_are_rejected(header: str) -> None:
    with pytest.raises(DomainError) as error:
        requested_range(header, 20)
    assert error.value.status == 416
