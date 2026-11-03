from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generation_application.ports import GenerationService
from generation_contracts.submission_lookup import SubmissionLookup
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, request_fingerprint
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import (
    ApiKey,
    Idempotency,
    Job,
    OutboxEvent,
    PublicModel,
    UsageLedger,
)
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration import test_http_auth as http_auth

lookup_client = http_auth.client

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "synthetic lookup, never a GPU request"}, "client_request_id": "original-client-reference"}
TOKEN = "studio-lookup-original-token"


def create(pg: sessionmaker[Session], catalog: dict[str, Any]) -> tuple[JobRepository, dict[str, Any]]:
    repository = JobRepository(pg)
    return repository, repository.create(catalog["identity"], REQUEST, TOKEN, "synthetic-lookup", expected_release_digest=catalog["release_digest"])


def counts(pg: sessionmaker[Session]) -> tuple[int, ...]:
    with pg() as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in (Job, Idempotency, UsageLedger, OutboxEvent))


def test_lookup_is_read_only_even_when_alias_is_unpublished(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    repository, created = create(pg, catalog)
    with pg.begin() as session:
        session.scalar(select(PublicModel)).published = False
    before = counts(pg)
    statements: list[str] = []

    def observe(_connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement.upper())

    engine = pg.kw["bind"]
    event.listen(engine, "before_cursor_execute", observe)
    try:
        # The lookup needs no media generation permission, including after policy changes.
        identity = replace(catalog["identity"], policy={**catalog["identity"].policy, "scopes": ["jobs:read"]})
        result = GenerationService(repository).lookup(identity, REQUEST, TOKEN, expected_release_digest=catalog["release_digest"])
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert result["outcome"] == "found" and result["job"]["id"] == created["id"]
    assert "input" not in result["job"] and "artifacts" not in result["job"]
    SubmissionLookup.model_validate(result)
    assert counts(pg) == before
    assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert all(statement.startswith(("SET ", "SELECT ")) and "FOR UPDATE" not in statement and "FOR SHARE" not in statement for statement in statements)
    assert not any("FROM ASSETS" in statement or "FROM PUBLIC_MODELS" in statement for statement in statements)


@pytest.mark.parametrize("change", ["prompt", "client_reference", "parameters_presence", "model", "release", "release_absent"])
def test_original_submission_must_match_every_frozen_component(pg: sessionmaker[Session], catalog: dict[str, Any], change: str) -> None:
    repository, _ = create(pg, catalog)
    request, digest = deepcopy(REQUEST), catalog["release_digest"]
    if change == "prompt":
        request["input"]["prompt"] = "different"
    elif change == "client_reference":
        request["client_request_id"] = "different"
    elif change == "parameters_presence":
        request["parameters"] = {}
    elif change == "model":
        request["model"] = "different"
    elif change == "release":
        digest = "0" * 64
    else:
        digest = None
    before = counts(pg)
    with pytest.raises(DomainError) as caught:
        repository.lookup(catalog["identity"], request, TOKEN, expected_release_digest=digest)
    assert caught.value.code == "submission_mismatch" and counts(pg) == before


@pytest.mark.parametrize("task_type", ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"])
def test_frozen_history_lookup_survives_removed_input_rows_for_every_media_type(pg: sessionmaker[Session], catalog: dict[str, Any], task_type: str) -> None:
    repository, created = create(pg, catalog)
    request = deepcopy(REQUEST)
    request["task_type"] = task_type
    request["parameters"] = {"seconds": 1.625, "size": "512x288"} if task_type.endswith("video") else {}
    asset_id = "asset_" + "a" * 32
    reference = {"asset_id": asset_id}
    if task_type in {"image_to_image", "image_to_3d"}:
        request["input"]["source_image"] = reference
    if task_type.startswith("references_"):
        request["input"]["references"] = [reference]
    hashes = {asset_id: "b" * 64} if task_type in {"image_to_image", "image_to_3d", "references_to_image", "references_to_video"} else {}
    # Historical rows deliberately have no surviving live Asset; this is a native
    # protocol fixture, not a claim of seven GPU generation runs.
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", created["id"]))
        job.request, job.task_type = request, task_type
        job.execution = {**job.execution, "asset_hashes": hashes, "parameters": request["parameters"]}
        job.request_fingerprint = request_fingerprint(request, hashes, expected_release_digest=catalog["release_digest"])
        session.scalar(select(Idempotency)).request_fingerprint = job.request_fingerprint
        session.scalar(select(PublicModel)).published = False
    result = repository.lookup(catalog["identity"], request, TOKEN, expected_release_digest=catalog["release_digest"])
    assert result["job"]["id"] == created["id"] and result["job"]["task_type"] == task_type
    assert "input" not in result["job"]
    SubmissionLookup.model_validate(result)
    if task_type.endswith("video"):
        assert result["job"]["seconds"] == "1.625" and result["job"]["size"] == "512x288"
    if hashes:
        changed = deepcopy(request)
        if "source_image" in changed["input"]:
            changed["input"]["source_image"]["asset_id"] = "asset_" + "c" * 32
        else:
            changed["input"]["references"][0]["asset_id"] = "asset_" + "c" * 32
        with pytest.raises(DomainError, match="不匹配"):
            repository.lookup(catalog["identity"], changed, TOKEN, expected_release_digest=catalog["release_digest"])


@pytest.mark.parametrize("scope", ["key_id", "tenant_id", "project_id"])
def test_mapping_cannot_be_discovered_outside_original_identity(pg: sessionmaker[Session], catalog: dict[str, Any], scope: str) -> None:
    repository, _ = create(pg, catalog)
    identity = replace(catalog["identity"], **{scope: uuid4()})
    assert repository.lookup(identity, REQUEST, TOKEN, expected_release_digest=catalog["release_digest"]) == {"object": "generation.submission_lookup", "outcome": "not_observed", "job": None}


def test_uncommitted_submission_is_not_observed_and_never_replayed(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    repository = JobRepository(pg)
    with pg.begin() as session:
        created = repository.create(catalog["identity"], REQUEST, TOKEN, "synthetic-uncommitted", expected_release_digest=catalog["release_digest"], _session=session)
        assert repository.lookup(catalog["identity"], REQUEST, TOKEN, expected_release_digest=catalog["release_digest"])["outcome"] == "not_observed"
    assert repository.lookup(catalog["identity"], REQUEST, TOKEN, expected_release_digest=catalog["release_digest"])["job"]["id"] == created["id"]
    assert counts(pg)[0:2] == (1, 1)


def test_legacy_submission_without_release_digest_stays_queryable(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    repository = JobRepository(pg)
    created = repository.create(catalog["identity"], REQUEST, TOKEN, "synthetic-legacy")
    assert repository.lookup(catalog["identity"], REQUEST, TOKEN)["job"]["id"] == created["id"]
    with pytest.raises(DomainError, match="不匹配"):
        repository.lookup(catalog["identity"], REQUEST, TOKEN, expected_release_digest=catalog["release_digest"])


@pytest.mark.parametrize("token", ["short", "x" * 129, "space in the original token", "非ASCII令牌" * 4])
def test_lookup_uses_existing_token_validation(pg: sessionmaker[Session], catalog: dict[str, Any], token: str) -> None:
    with pytest.raises(DomainError) as caught:
        GenerationService(JobRepository(pg)).lookup(catalog["identity"], REQUEST, token)
    assert caught.value.code == "invalid_idempotency_key" and counts(pg)[0:2] == (0, 0)


def test_lookup_requires_jobs_read_even_with_create_permission(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    identity = replace(catalog["identity"], policy={**catalog["identity"].policy, "scopes": ["jobs:create", "images:create"]})
    with pytest.raises(DomainError) as caught:
        GenerationService(JobRepository(pg)).lookup(identity, REQUEST, TOKEN)
    assert caught.value.code == "scope_denied" and counts(pg)[0:2] == (0, 0)


@pytest.mark.parametrize("damage", ["missing_hashes", "invalid_hash", "idem_fingerprint", "job_fingerprint", "missing_job"])
def test_unverifiable_or_damaged_history_fails_closed(pg: sessionmaker[Session], catalog: dict[str, Any], damage: str) -> None:
    repository, created = create(pg, catalog)
    with pg.begin() as session:
        job, idem = session.get(Job, parse_id("job", created["id"])), session.scalar(select(Idempotency))
        if damage == "missing_hashes":
            job.execution = {k: v for k, v in job.execution.items() if k != "asset_hashes"}
        elif damage == "invalid_hash":
            job.execution = {**job.execution, "asset_hashes": {"asset": "corrupt"}}
        elif damage == "idem_fingerprint":
            idem.request_fingerprint = "0" * 64
        elif damage == "job_fingerprint":
            job.request_fingerprint = "0" * 64
        else:
            idem.resource_id = uuid4()
    with pytest.raises(DomainError) as caught:
        repository.lookup(catalog["identity"], REQUEST, TOKEN, expected_release_digest=catalog["release_digest"])
    assert caught.value.code == ("not_found" if damage == "missing_job" else "submission_unverifiable" if damage in {"missing_hashes", "invalid_hash"} else "submission_mismatch")


def test_http_lookup_is_explicit_scoped_advertised_and_revocable(lookup_client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    client = lookup_client
    http_auth.login(client)
    issued = client.post("/admin/api/v1/api-keys", json=http_auth.key_body(catalog), headers={"Idempotency-Key": str(uuid4())}).json()
    headers = {"Authorization": "Bearer " + issued["secret"], "Idempotency-Key": TOKEN, "X-Expected-Release-Digest": catalog["release_digest"]}
    policy = client.get("/v1/access-policy", headers=headers)
    assert policy.headers["X-Studio-Submission-Lookup"] == "1"
    created = client.post("/v1/jobs", json=REQUEST, headers=headers)
    assert created.status_code == 202
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", issued["key"]["id"]))
        key.policy = {**key.policy, "scopes": ["jobs:read"]}
    before = counts(pg)
    found = client.post("/v1/jobs/lookup", json=REQUEST, headers=headers)
    assert found.status_code == 200 and found.headers["Retry-After"] == "2", found.text
    assert found.json()["job"]["id"] == created.json()["id"] and "input" not in found.json()["job"] and "artifacts" not in found.json()["job"]
    assert counts(pg) == before
    assert client.post("/v1/jobs", json=REQUEST, headers=headers).status_code == 403
    unknown = client.post("/v1/jobs/lookup", json=REQUEST, headers={**headers, "Idempotency-Key": "studio-other-unknown-token"})
    assert unknown.status_code == 200 and unknown.json()["outcome"] == "not_observed" and unknown.json()["job"] is None
    assert "Retry-After" not in unknown.headers and "Location" not in unknown.headers
    assert client.post("/v1/jobs/lookup", json=REQUEST, headers={"Authorization": headers["Authorization"]}).status_code == 422
    assert client.post("/v1/jobs/lookup", json=REQUEST, headers={**headers, "Idempotency-Key": "short"}).status_code == 400
    assert client.post("/v1/jobs/lookup", json=REQUEST, headers={**headers, "X-Expected-Release-Digest": "bad"}).status_code == 422
    assert client.post("/v1/jobs/lookup", json={**REQUEST, "client_request_id": "different"}, headers=headers).json()["error"]["code"] == "submission_mismatch"
    with pg.begin() as session:
        session.get(ApiKey, parse_id("key", issued["key"]["id"])).enabled = False
    assert client.post("/v1/jobs/lookup", json=REQUEST, headers=headers).status_code == 401
    assert counts(pg) == before
