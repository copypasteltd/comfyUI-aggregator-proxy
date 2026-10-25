"""Direct HTTP projection over owned PG state fixtures, not real GPU transitions."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generation_contracts.validation import validate
from generation_domain.values import fingerprint, parse_id, utcnow
from generation_infrastructure.models import (
    ApiKey,
    Attempt,
    Job,
    Project,
    PublicModel,
    Tenant,
    UsageLedger,
    Workflow,
    WorkflowRelease,
)
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from test_compatibility_inputs import authorization
from test_http_auth import client as client

pytestmark = pytest.mark.integration
BODY = {"model": "synthetic-image", "prompt": "SYNTHETIC-PRIVATE-VIDEO-PROMPT"}


def video_catalog(pg: sessionmaker[Session], catalog: dict[str, Any], *, seconds: float = 1, size: str = "1024x1024") -> None:
    with pg.begin() as session:
        base = session.get(WorkflowRelease, catalog["release_id"])
        workflow = Workflow(tenant_id=base.tenant_id, project_id=base.project_id, name="Synthetic video projection", task_type="text_to_video", draft={})
        session.add(workflow)
        session.flush()
        manifest = {**base.manifest, "task_type": "text_to_video", "supported_parameters": ["seed", "negative_prompt", "fps"], "presets": [{"size": size, "seconds": seconds, "quality": "standard", "values": {}}]}
        release = WorkflowRelease(tenant_id=base.tenant_id, project_id=base.project_id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        model = session.scalar(select(PublicModel))
        model.task_type, model.release_id = "text_to_video", release.id
        model.defaults = {"seconds": seconds, "size": size, "quality": "standard"}


def test_video_projection_freezes_defaults_across_replay_and_reads(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    video_catalog(pg, catalog)
    headers, _ = authorization(client, catalog)
    created = client.post("/v1/videos", json=BODY, headers=headers)
    assert created.status_code == 202, created.text
    initial = created.json()
    assert initial.get("seconds") == "1" and initial.get("size") == "1024x1024"
    assert created.headers["location"] == initial["poll_url"] == "/v1/videos/" + initial["id"]
    validate("control-video-response", initial)
    with pg.begin() as session:
        model = session.scalar(select(PublicModel))
        prior = session.get(WorkflowRelease, model.release_id)
        manifest = {**prior.manifest, "presets": [{"size": "512x512", "seconds": 9, "quality": "high", "values": {}}]}
        replacement = WorkflowRelease(tenant_id=prior.tenant_id, project_id=prior.project_id, workflow_id=prior.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(replacement)
        session.flush()
        model.defaults = {"seconds": 9, "size": "512x512", "quality": "high"}
        model.release_id = replacement.id
    replay = client.post("/v1/videos", json=BODY, headers=headers)
    assert replay.status_code == 202, replay.text
    polled = client.get(initial["poll_url"], headers=headers)
    assert polled.status_code == 200, polled.text
    for value in (replay.json(), polled.json()):
        validate("control-video-response", value)
        assert (value["id"], value["seconds"], value["size"]) == (initial["id"], "1", "1024x1024")
        assert "progress" not in value and "error" not in value and "input" not in value
    with pg() as session:
        job = session.get(Job, parse_id("job", initial["id"]))
        assert "seconds" not in job.request.get("parameters", {}) and job.execution["parameters"]["seconds"] == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


@pytest.mark.parametrize(("seconds", "expected"), [(0.001, "0.001"), (0.01, "0.01"), (0.1, "0.1"), (5.125, "5.125"), (5.0, "5"), (15.0, "15")])
def test_video_projection_preserves_exact_milliseconds(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any], seconds: float, expected: str) -> None:
    video_catalog(pg, catalog, seconds=seconds)
    headers, _ = authorization(client, catalog)
    created = client.post("/v1/videos", json={**BODY, "seconds": str(seconds)}, headers=headers)
    assert created.status_code == 202, created.text
    result = client.get("/v1/videos/" + created.json()["id"], headers=headers)
    assert result.status_code == 200 and result.json().get("seconds") == expected, result.text
    validate("control-video-response", result.json())


def test_video_duration_rejection_keeps_public_and_key_limits(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    video_catalog(pg, catalog, seconds=120)
    headers, _ = authorization(client, catalog)
    over_key = client.post("/v1/videos", json={**BODY, "seconds": "120"}, headers=headers)
    assert over_key.status_code == 403 and over_key.json()["error"]["code"] == "spec_denied"
    over_public = client.post("/v1/videos", json={**BODY, "seconds": "120.001"}, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert over_public.status_code == 422
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_video_projection_all_states_use_safe_errors_without_mutation(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    video_catalog(pg, catalog)
    headers, _ = authorization(client, catalog)
    created = client.post("/v1/videos", json=BODY, headers=headers).json()
    identifier = parse_id("job", created["id"])
    with pg.begin() as session:
        attempt = Attempt(job_id=identifier, channel_id=catalog["channel_ids"][0], channel_snapshot={}, node_id=catalog["node_id"], boot_id=catalog["boot_id"], gpu_id=catalog["gpu_id"], fence=1, lease_expires_at=utcnow(), closed_at=utcnow())
        session.add(attempt)
        session.flush()
        attempt_id = attempt.id
    errors = {"failed": ("generation_failed", "Generation did not complete"), "cancelled": ("task_cancelled", "Generation was cancelled"), "expired": ("task_expired", "Generation expired")}
    states = ("admission_pending", "queued", "leased", "preparing", "submitting", "running", "collecting", "uploading", "verifying", "reconciling", "retry_wait", "cancel_requested", "succeeded", "failed", "cancelled", "expired")
    for state in states:
        with pg.begin() as session:
            job = session.get(Job, identifier)
            job.state, job.error_code = state, "SYNTHETIC-PRIVATE-DRIVER-TEXT"
            job.retry_after = utcnow() + timedelta(seconds=30) if state == "retry_wait" else None
            if state == "retry_wait":
                job.retry_count, job.retry_error_code, job.retry_source_attempt_id = 1, "comfy_oom", attempt_id
            job.terminal_at = utcnow() if state in (*errors, "succeeded") else None
        response = client.get("/v1/videos/" + created["id"], headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        validate("control-video-response", result)
        expected = "failed" if state in errors else "completed" if state == "succeeded" else "queued" if state in ("admission_pending", "queued", "leased") else "in_progress"
        assert result["status"] == expected and result["job_status"] == state
        assert "SYNTHETIC-PRIVATE" not in response.text and "progress" not in result
        if state in errors:
            assert result["error"] == dict(zip(("code", "message"), errors[state], strict=True))
        else:
            assert "error" not in result
        with pg() as session:
            job = session.get(Job, identifier)
            assert job.state == state and job.error_code == "SYNTHETIC-PRIVATE-DRIVER-TEXT" and job.revision == 1
            assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


@pytest.mark.parametrize("parameters", [{}, {"size": "1024x1024"}, {"seconds": 1}])
def test_video_projection_legacy_unknown_fields_are_not_guessed(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any], parameters: dict[str, Any]) -> None:
    video_catalog(pg, catalog)
    headers, _ = authorization(client, catalog)
    created = client.post("/v1/videos", json=BODY, headers=headers).json()
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", created["id"]))
        job.execution = {**job.execution, "parameters": parameters}
    response = client.get("/v1/videos/" + created["id"], headers=headers)
    assert response.status_code == 200, response.text
    value = response.json()
    validate("control-video-response", value)
    for field in ("seconds", "size"):
        assert (field in value) == (field in parameters)


@pytest.mark.parametrize("parameters", [{"seconds": 0}, {"seconds": -1}, {"seconds": True}, {"seconds": None}, {"seconds": "NaN"}, {"seconds": 0.0011}, {"seconds": 86400.001}, {"size": "SYNTHETIC-PRIVATE-PATH"}, {"size": None}, {"size": "1x1"}])
def test_video_projection_corrupt_known_metadata_fails_closed(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any], parameters: dict[str, Any]) -> None:
    video_catalog(pg, catalog)
    headers, _ = authorization(client, catalog)
    created = client.post("/v1/videos", json=BODY, headers=headers).json()
    with pg.begin() as session:
        job = session.get(Job, parse_id("job", created["id"]))
        job.execution = {**job.execution, "parameters": parameters}
    response = client.get("/v1/videos/" + created["id"], headers=headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == "invalid_video_metadata", response.text
    assert "SYNTHETIC-PRIVATE" not in response.text
    with pg() as session:
        assert session.get(Job, parse_id("job", created["id"])).state == "queued"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


@pytest.mark.parametrize("execution", [[], None, {"parameters": []}, {"parameters": None}])
def test_video_projection_corrupt_metadata_objects_fail_closed(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any], execution: Any) -> None:
    video_catalog(pg, catalog)
    headers, _ = authorization(client, catalog)
    created = client.post("/v1/videos", json=BODY, headers=headers).json()
    with pg.begin() as session:
        session.get(Job, parse_id("job", created["id"])).execution = execution
    response = client.get("/v1/videos/" + created["id"], headers=headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == "invalid_video_metadata", response.text
    with pg() as session:
        assert session.get(Job, parse_id("job", created["id"])).state == "queued"
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_video_projection_current_permissions_and_resource_scope(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    video_catalog(pg, catalog)
    owner, owner_key = authorization(client, catalog)
    other, _ = authorization(client, catalog)
    created = client.post("/v1/videos", json=BODY, headers=owner).json()
    path = "/v1/videos/" + created["id"]
    assert client.get(path, headers=other).status_code == 404
    assert client.get(path + "/content", headers=other).status_code == 404
    assert client.head(path + "/content", headers=other).status_code == 404
    with pg.begin() as session:
        original = session.get(Tenant, catalog["identity"].tenant_id)
        tenant = Tenant(name="Synthetic foreign video tenant", limits=original.limits)
        session.add(tenant)
        session.flush()
        project = Project(tenant_id=tenant.id, name="Synthetic foreign video project", limits=original.limits)
        session.add(project)
        session.flush()
        foreign_catalog = {**catalog, "identity": replace(catalog["identity"], tenant_id=tenant.id, project_id=project.id)}
    foreign, _ = authorization(client, foreign_catalog)
    assert client.get(path, headers=foreign).status_code == 404
    assert client.get(path + "/content", headers=foreign).status_code == 404
    assert client.head(path + "/content", headers=foreign).status_code == 404
    assert client.get(path + "/content", headers=owner).status_code == 409
    assert "input" not in client.get(path, headers=owner).json()
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", owner_key))
        key.policy = {**key.policy, "scopes": [*key.policy["scopes"], "content:sensitive"]}
    sensitive = client.get(path, headers=owner)
    assert sensitive.json()["input"]["prompt"] == BODY["prompt"]
    validate("control-video-response", sensitive.json())
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", owner_key))
        key.policy = {**key.policy, "scopes": [scope for scope in key.policy["scopes"] if scope not in ("jobs:read", "content:sensitive")]}
    assert client.get(path, headers=owner).status_code == 403
    replay = client.post("/v1/videos", json=BODY, headers=owner)
    assert replay.status_code == 202 and replay.json()["seconds"] == "1" and "input" not in replay.json()
    with pg.begin() as session:
        key = session.get(ApiKey, parse_id("key", owner_key))
        key.enabled = False
    assert client.get(path, headers=owner).status_code in (401, 403)


def test_native_video_list_keeps_json_deferred_and_nonvideo_routes_unchanged(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    headers, _ = authorization(client, catalog)
    image = client.post("/v1/jobs", json={"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic image"}}, headers=headers)
    assert image.status_code == 202, image.text
    assert "seconds" not in image.json() and "size" not in image.json()
    assert client.get("/v1/videos/" + image.json()["id"], headers=headers).status_code == 404
    video_catalog(pg, catalog)
    for _ in range(4):
        result = client.post("/v1/videos", json=BODY, headers={**headers, "Idempotency-Key": str(uuid4())})
        assert result.status_code == 202, result.text
    statements: list[str] = []
    def observed(_conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _many: Any) -> None:
        if "from jobs" in statement.lower():
            statements.append(statement.lower())

    event.listen(Engine, "before_cursor_execute", observed)
    try:
        response = client.get("/v1/jobs?limit=3", headers=headers)
    finally:
        event.remove(Engine, "before_cursor_execute", observed)
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]) == 3 and response.json()["has_more"]
    assert len(statements) == 1 and all(not re.search(r"\bjobs\.(?:execution|request)\b", sql) for sql in statements)
    assert all("seconds" not in row and "size" not in row and "SYNTHETIC-PRIVATE" not in json.dumps(row) for row in response.json()["data"])
