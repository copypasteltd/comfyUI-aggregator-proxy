from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from generation_contracts.collections import CollectionQuery
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.cursors import CursorCodec
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import ApiKey, Artifact, Job, ObjectRecord
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def seed_jobs(pg: sessionmaker[Session], catalog: dict[str, Any], count: int, *, moment: datetime | None = None, start: int = 1000) -> list[UUID]:
    identity = catalog["identity"]
    clock = moment or utcnow() - timedelta(hours=1)
    request = {"input": {"prompt": "Synthetic private collection prompt"}}
    identifiers = [UUID(int=start + index) for index in range(count)]
    with pg.begin() as session:
        session.add_all([Job(id=identifier, created_at=clock, tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, model_alias="synthetic-image", task_type="text_to_image", state="queued", release_id=catalog["release_id"], request=request, execution={}, request_fingerprint=fingerprint(request), execution_fingerprint=fingerprint({}), eligible_channels=[], deadline=utcnow() + timedelta(hours=1), accepted_period=clock.date().isoformat()) for identifier in identifiers])
    return identifiers


def seed_content(pg: sessionmaker[Session], catalog: dict[str, Any], count: int) -> list[UUID]:
    jobs = seed_jobs(pg, catalog, count)
    identity = catalog["identity"]
    identifiers = []
    with pg.begin() as session:
        for index, job in enumerate(jobs):
            obj = ObjectRecord(tenant_id=identity.tenant_id, project_id=identity.project_id, object_key="outputs/synthetic-list-" + str(index), version_id="synthetic-version", bytes=4096, sha256="a" * 64, media_type="image/png", verified_at=utcnow(), expires_at=utcnow() + timedelta(days=1), metadata_json={"private": "never-forward"})
            session.add(obj)
            session.flush()
            artifact = Artifact(id=UUID(int=10000 + index), tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, job_id=job, object_id=obj.id, position=0, created_at=utcnow() - timedelta(hours=1))
            session.add(artifact)
            identifiers.append(artifact.id)
    return identifiers


def test_jobs_keyset_keeps_ties_upper_bound_and_no_offset_drift(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    ids = seed_jobs(pg, catalog, 105)
    identity = catalog["identity"]
    repository, codec = JobRepository(pg), CursorCodec(test_settings.encryption_key.get_secret_value())
    query = CollectionQuery(limit=50)
    first = repository.list(identity, query, codec)
    assert len(first["data"]) == 50 and first["has_more"]
    assert "input" not in first["data"][0] and "Synthetic private" not in str(first)
    seed_jobs(pg, catalog, 1, moment=utcnow(), start=5000)
    with pg.begin() as session:
        session.execute(delete(Job).where(Job.id == ids[-1]))
    second = repository.list(identity, query.model_copy(update={"cursor": first["next_cursor"]}), codec)
    third = repository.list(identity, query.model_copy(update={"cursor": second["next_cursor"]}), codec)
    found = [row["id"] for page in (first, second, third) for row in page["data"]]
    assert found == [public_id("job", identifier) for identifier in reversed(ids)]
    assert len(found) == len(set(found)) == 105 and not third["has_more"] and third["next_cursor"] is None
    assert repository.list(identity, query, codec)["data"][0]["id"] == public_id("job", UUID(int=5000))


@pytest.mark.parametrize("change", ["key", "tenant", "policy", "limit", "model", "status", "purpose", "tamper"])
def test_collection_cursor_cannot_change_scope_or_query(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path, change: str) -> None:
    seed_jobs(pg, catalog, 2)
    identity = catalog["identity"]
    repository, codec = JobRepository(pg), CursorCodec(test_settings.encryption_key.get_secret_value())
    first = repository.list(identity, CollectionQuery(limit=1), codec)
    query = CollectionQuery(limit=1, cursor=first["next_cursor"])
    if change in {"key", "tenant"}:
        identity = replace(identity, **{change + "_id": UUID(int=999)})
    elif change == "policy":
        identity = replace(identity, policy={**identity.policy, "content": {"mode": "project_shared", "project_ids": [], "creator_key_ids": []}})
    elif change in {"limit", "model", "status"}:
        query = query.model_copy(update={change: {"limit": 2, "model": "different-model", "status": "running"}[change]})
    elif change == "tamper":
        query = query.model_copy(update={"cursor": first["next_cursor"][:-8] + "AAAAAAAA"})
    with pytest.raises(DomainError) as error:
        if change == "purpose":
            ContentRepository(pg, LocalDevelopmentStore(tmp_path)).list_content(identity, query, codec)
        else:
            repository.list(identity, query, codec)
    assert error.value.status == 400 and error.value.code == "invalid_cursor"


def test_cursor_fixed_expiry_does_not_extend_on_next_page(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_jobs(pg, catalog, 3)
    clock = utcnow()
    monkeypatch.setattr("generation_infrastructure.cursors.utcnow", lambda: clock)
    repository, codec = JobRepository(pg), CursorCodec(test_settings.encryption_key.get_secret_value())
    first = repository.list(catalog["identity"], CollectionQuery(limit=1), codec)
    clock += timedelta(seconds=800)
    second = repository.list(catalog["identity"], CollectionQuery(limit=1, cursor=first["next_cursor"]), codec)
    clock += timedelta(seconds=101)
    with pytest.raises(DomainError) as error:
        repository.list(catalog["identity"], CollectionQuery(limit=1, cursor=second["next_cursor"]), codec)
    assert error.value.status == 400 and error.value.code == "cursor_expired"


@pytest.mark.parametrize("mode", ["project_shared", "shared_creator_keys"])
def test_content_pages_share_explicitly_and_revoke_prior_cursor(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path, mode: str) -> None:
    identifiers = seed_content(pg, catalog, 3)
    creator = catalog["identity"]
    with pg.begin() as session:
        key = ApiKey(tenant_id=creator.tenant_id, project_id=creator.project_id, name="Synthetic list reader", policy=creator.policy)
        session.add(key)
        session.flush()
        key_id = key.id
    reader = replace(creator, key_id=key_id)
    repository, codec = ContentRepository(pg, LocalDevelopmentStore(tmp_path)), CursorCodec(test_settings.encryption_key.get_secret_value())
    query = CollectionQuery(limit=1)
    assert repository.list_content(reader, query, codec)["data"] == []
    policy = {**reader.policy, "content": {"mode": mode, "project_ids": [public_id("prj", creator.project_id)], "creator_key_ids": [public_id("key", creator.key_id)] if mode == "shared_creator_keys" else []}}
    shared = replace(reader, policy=policy)
    first = repository.list_content(shared, query, codec)
    assert first["has_more"] and first["data"][0]["id"] in [public_id("content", identifier) for identifier in identifiers]
    assert "never-forward" not in str(first) and "key_id" not in str(first) and "object_key" not in str(first)
    with pytest.raises(DomainError) as error:
        repository.list_content(reader, query.model_copy(update={"cursor": first["next_cursor"]}), codec)
    assert error.value.code == "invalid_cursor"
    assert repository.list_content(reader, query, codec)["data"] == []
    with pytest.raises(DomainError) as error:
        repository.delete(replace(shared, policy={**policy, "scopes": [*policy["scopes"], "content:delete"]}), identifiers[0], "shared-reader")
    assert error.value.status == 404
    assert repository.list_content(replace(shared, tenant_id=UUID(int=999)), query, codec)["data"] == []


def test_content_pages_exclude_unverified_expired_and_deleted_objects(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path) -> None:
    identifiers = seed_content(pg, catalog, 6)
    with pg.begin() as session:
        artifacts = [session.get(Artifact, identifier) for identifier in identifiers]
        objects = [session.get(ObjectRecord, row.object_id) for row in artifacts]
        objects[0].verified_at = None
        objects[1].expires_at = utcnow() - timedelta(seconds=1)
        objects[2].deleted_at = utcnow()
        objects[3].delete_requested_at = utcnow()
        artifacts[4].deleted_at = utcnow()
    repository, codec = ContentRepository(pg, LocalDevelopmentStore(tmp_path)), CursorCodec(test_settings.encryption_key.get_secret_value())
    page = repository.list_content(catalog["identity"], CollectionQuery(limit=1, model="synthetic-image"), codec)
    assert [row["id"] for row in page["data"]] == [public_id("content", identifiers[5])]
    assert not page["has_more"] and page["next_cursor"] is None
    assert repository.list_content(catalog["identity"], CollectionQuery(model="unmatched"), codec)["data"] == []


def test_job_filters_and_ownership_are_applied_before_page_limit(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identifiers = seed_jobs(pg, catalog, 105)
    with pg.begin() as session:
        for identifier in identifiers[:-1]:
            session.get(Job, identifier).model_alias = "other-model"
        session.get(Job, identifiers[-1]).state = "running"
    repository, codec = JobRepository(pg), CursorCodec(test_settings.encryption_key.get_secret_value())
    result = repository.list(catalog["identity"], CollectionQuery(limit=1, model="synthetic-image", status="running"), codec)
    assert len(result["data"]) == 1 and result["data"][0]["id"] == public_id("job", identifiers[-1]) and not result["has_more"]
    assert repository.list(replace(catalog["identity"], key_id=UUID(int=999)), CollectionQuery(), codec)["data"] == []
