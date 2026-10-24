from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generation_domain.values import fingerprint, parse_id
from generation_infrastructure.models import (
    ApiKey,
    Asset,
    Job,
    PublicModel,
    QuotaBucket,
    WorkflowRelease,
)
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_http_auth import client as client
from test_http_auth import key_body, login

pytestmark = pytest.mark.integration
_image = BytesIO()
Image.new("RGB", (32, 32), "blue").save(_image, "PNG")
IMAGE = _image.getvalue()
_image.close()
DATA_URL = "data:image/png;base64," + base64.b64encode(IMAGE).decode()


def publish(pg: sessionmaker[Session], catalog: dict[str, Any], task: str, slots: int = 0) -> None:
    with pg.begin() as session:
        old = session.get(WorkflowRelease, catalog["release_id"])
        manifest = {**old.manifest, "task_type": task, "supported_parameters": ["seed", "negative_prompt", "fps", "strength"], "bindings": [{"source": f"reference_{i}", "node_id": "1", "input_name": f"image_{i}"} for i in range(slots)], "presets": [{"size": "1024x1024", "quality": "standard", **({"seconds": 1} if task.endswith("video") else {}), "values": {}}]}
        release = WorkflowRelease(tenant_id=old.tenant_id, project_id=old.project_id, workflow_id=old.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        model = session.scalar(select(PublicModel))
        model.task_type, model.release_id = task, release.id
        model.defaults = {**model.defaults, **({"seconds": 1} if task.endswith("video") else {})}


def authorization(client: TestClient, catalog: dict[str, Any]) -> tuple[dict[str, str], str]:
    login(client)
    body = key_body(catalog)
    body["policy"]["limits"]["burst"]["value"] = 100
    response = client.post("/admin/api/v1/api-keys", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 201, response.text
    data = response.json()
    return {"Authorization": "Bearer " + data["secret"], "Idempotency-Key": str(uuid4())}, data["key"]["id"]


def test_video_json_multipart_and_native_share_ready_asset_and_job(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    publish(pg, catalog, "references_to_video", 1)
    headers, key = authorization(client, catalog)
    options = {"seed": 17, "fps": 24, "negative_prompt": "blur"}
    body = {"model": "synthetic-image", "prompt": "Synthetic first frame", "seconds": "1", "input_reference": {"image_url": DATA_URL}, "workflow_options": options}
    schema = client.get("/openapi.json").json()["paths"]["/v1/videos"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert all(schema["properties"][name]["type"] == "object" for name in ("input_reference", "workflow_options"))
    first = client.post("/v1/videos", json=body, headers=headers)
    assert first.status_code == 202, first.text
    second = client.post("/v1/videos", data={"model": body["model"], "prompt": body["prompt"], "seconds": "1", "workflow_options": json.dumps(options)}, files={"input_reference": ("untrusted.png", IMAGE, "image/png")}, headers=headers)
    assert second.status_code == 202 and second.json()["id"] == first.json()["id"], second.text
    with pg() as session:
        job = session.get(Job, parse_id("job", first.json()["id"]))
        native = job.request
        assert native["input"]["references"][0]["role"] == "first_frame"
        assert native["input"]["negative_prompt"] == "blur" and native["parameters"]["fps"] == 24 and native["parameters"]["seed"] == 17
        assert session.scalar(select(func.count()).select_from(Asset).where(Asset.key_id == parse_id("key", key))) == 1
        asset = native["input"]["references"][0]["asset_id"]
    replay = client.post("/v1/jobs", json=native, headers=headers)
    assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"], replay.text
    url_body = {**body, "input_reference": {"image_url": str(client.base_url).rstrip("/") + "/v1/assets/" + asset + "/content"}}
    assert client.post("/v1/videos", json=url_body, headers=headers).json()["id"] == first.json()["id"]
    conflict = client.post("/v1/videos", json={**body, "workflow_options": {**options, "seed": 18}}, headers=headers)
    assert conflict.status_code == 409


def test_image_standard_sources_roles_and_options_reach_native_job(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    publish(pg, catalog, "references_to_image", 2)
    headers, _ = authorization(client, catalog)
    roles = [{"index": 1, "role": "scene"}, {"index": 0, "role": "character"}]
    options = {"seed": 12, "negative_prompt": "blur", "image_roles": roles}
    body = {"model": "synthetic-image", "prompt": "Synthetic two references", "images": [{"image_url": DATA_URL}, {"image_url": DATA_URL}], "workflow_options": options}
    result = client.post("/v1/images/edits/async", json=body, headers=headers)
    assert result.status_code == 202, result.text
    multipart = client.post("/v1/images/edits/async", data={"model": body["model"], "prompt": body["prompt"], "workflow_options": json.dumps(options)}, files=[("image[]", ("first.png", IMAGE, "image/png")), ("image[]", ("second.png", IMAGE, "image/png"))], headers=headers)
    assert multipart.status_code == 202 and multipart.json()["id"] == result.json()["id"], multipart.text
    with pg() as session:
        job = session.get(Job, parse_id("job", result.json()["id"]))
        assert [ref["role"] for ref in job.request["input"]["references"]] == ["character", "scene"]
        assert job.request["input"]["negative_prompt"] == "blur" and job.request["parameters"]["seed"] == 12


def test_video_invalid_json_and_multipart_leave_no_persistent_inputs(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    publish(pg, catalog, "references_to_video", 1)
    headers, key = authorization(client, catalog)
    body = {"model": "synthetic-image", "prompt": "Synthetic first frame", "input_reference": {"image_url": DATA_URL}}
    for extra in ({"workflow_options": None}, {"workflow_options": {"seed": -1}}, {"workflow_options": {"fps": 121}}, {"workflow_options": {"strength": 0.5}}, {"workflow_options": {"node_id": "1"}}, {"input_reference": {"file_id": "file_unmapped"}}, {"input_reference": {"image_url": "http://127.0.0.1/private.png"}}, {"input_reference": None}, {"workflow_options": {"references": [{"asset_id": "asset_reference1", "role": "character"}]}}):
        response = client.post("/v1/videos", json={**body, **extra}, headers=headers)
        assert response.status_code == 422, response.text
    duplicate = client.post("/v1/videos", content='{"model":"synthetic-image","prompt":"x","workflow_options":{"seed":1,"seed":2}}', headers={**headers, "Content-Type": "application/json"})
    assert duplicate.status_code == 422
    response = client.post("/v1/videos", data={"model": "synthetic-image", "prompt": "x"}, files=[("input_reference", ("one.png", IMAGE)), ("input_reference", ("two.png", IMAGE))], headers=headers)
    assert response.status_code == 422
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset).where(Asset.key_id == parse_id("key", key))) == 0
        assert session.scalar(select(func.count()).select_from(Job).where(Job.key_id == parse_id("key", key))) == 0


def test_single_video_cannot_enter_a_multiple_reference_workflow_or_consume_quota(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    publish(pg, catalog, "references_to_video", 2)
    headers, key = authorization(client, catalog)
    response = client.post("/v1/videos", json={"model": "synthetic-image", "prompt": "Synthetic mismatch", "input_reference": {"image_url": DATA_URL}}, headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "reference_count_mismatch", response.text
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.key_id == parse_id("key", key))) == 0
        assert session.scalar(select(func.count()).select_from(Asset).where(Asset.key_id == parse_id("key", key))) == 0
        assert not list(session.scalars(select(QuotaBucket).where(QuotaBucket.daily_jobs > 0)))


def test_corrupt_checksum_image_returns_controlled_error_without_publication(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    publish(pg, catalog, "references_to_video", 1)
    headers, key = authorization(client, catalog)
    corrupt = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jX1sAAAAASUVORK5CYII=")
    result = client.post("/v1/videos", data={"model": "synthetic-image", "prompt": "Synthetic corrupt PNG"}, files={"input_reference": ("corrupt.png", corrupt)}, headers=headers)
    assert result.status_code == 422 and result.json()["error"]["code"] == "invalid_image", result.text
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Asset).where(Asset.key_id == parse_id("key", key))) == 0


def test_video_existing_asset_current_owner_and_upload_scope_are_enforced(client: TestClient, pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    publish(pg, catalog, "references_to_video", 1)
    headers, _ = authorization(client, catalog)
    asset = client.post("/v1/assets", files={"file": ("source.png", IMAGE, "image/png")}, headers=headers)
    assert asset.status_code == 201, asset.text
    other, key = authorization(client, catalog)
    body = {"model": "synthetic-image", "prompt": "Synthetic ownership", "input_reference": {"asset_id": asset.json()["id"]}}
    denied = client.post("/v1/videos", json=body, headers=other)
    assert denied.status_code == 404, denied.text
    with pg.begin() as session:
        row = session.get(ApiKey, parse_id("key", key))
        row.policy = {**row.policy, "scopes": [scope for scope in row.policy["scopes"] if scope != "assets:upload"]}
    denied = client.post("/v1/videos", json={**body, "input_reference": {"image_url": DATA_URL}}, headers=other)
    assert denied.status_code == 403
    allowed = client.post("/v1/videos", json=body, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert allowed.status_code == 202, allowed.text
