from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from generation_application.ports import BusinessIdentity
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, public_id
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.models import PublicModel, WorkflowRelease
from generation_infrastructure.settings import Settings
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("task", ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video"])
def test_capability_projection_uses_published_contract_without_private_fields(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, task: str) -> None:
    video = task.endswith("video")
    with pg.begin() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        manifest = {**release.manifest, "task_type": task, "supported_parameters": ["seed"], "presets": [{"quality": "standard", "size": "1024x1024", "values": {"synthetic_private_node": "never-forward"}, "private_path": "never-forward"}], "output": {"mime_types": ["video/mp4" if video else "image/png"], "expected_count": 1, "max_bytes": 33554432, "final_node_ids": ["never-forward"], "adapter": "never-forward"}, "dependency_lock": {"models": ["never-forward"]}}
        frozen = WorkflowRelease(tenant_id=release.tenant_id, project_id=release.project_id, workflow_id=release.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"synthetic": True})
        session.add(frozen)
        session.flush()
        session.query(PublicModel).update({PublicModel.task_type: task, PublicModel.release_id: frozen.id})
    repository = CatalogAdministration(pg, test_settings)
    result = repository.public_models(catalog["identity"], capabilities=True)
    assert len(result["data"]) == 1
    item = result["data"][0]
    assert item["task_type"] == task and item["release"] == {"version": 2, "digest": fingerprint(manifest)}
    assert item["output"]["media_types"] == ["video/mp4" if video else "image/png"]
    assert item["supported_parameters"] == ["seed"] and "never-forward" not in str(result)
    assert set(item) == {"id", "object", "created", "owned_by", "task_type", "asynchronous", "release", "supported_parameters", "presets", "output"}


@pytest.mark.parametrize("selection", ["models", "workflow_releases", "channels"])
def test_discovery_visibility_selectors_filter_single_and_collection(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, selection: str) -> None:
    repository = CatalogAdministration(pg, test_settings)
    original = catalog["identity"]
    policy = deepcopy(original.policy)
    policy[selection] = {"mode": "selected", "selected_ids": []}
    identity = BusinessIdentity(original.tenant_id, original.project_id, original.key_id, policy)
    assert repository.public_models(identity)["data"] == []
    with pytest.raises(DomainError) as missing:
        repository.public_model(identity, "synthetic-image")
    assert missing.value.status == 404
    identifiers = {"models": "synthetic-image", "workflow_releases": public_id("wfr", catalog["release_id"]), "channels": public_id("ch", catalog["channel_ids"][1])}
    policy[selection]["selected_ids"] = [identifiers[selection]]
    assert repository.public_model(identity, "synthetic-image")["id"] == "synthetic-image"


def test_discovery_limits_after_permissions_and_never_silently_truncates(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identity = catalog["identity"]
    with pg.begin() as session:
        session.execute(insert(PublicModel), [{"tenant_id": identity.tenant_id, "project_id": identity.project_id, "alias": f"extra-{index:04d}", "task_type": "text_to_image", "release_id": catalog["release_id"], "channel_ids": [public_id("ch", catalog["channel_ids"][0])], "defaults": {}, "published": True} for index in range(4096)])
    repository = CatalogAdministration(pg, test_settings)
    with pytest.raises(DomainError) as limit:
        repository.public_models(identity)
    assert limit.value.code == "catalog_too_large" and limit.value.status == 503
    policy = deepcopy(identity.policy)
    policy["models"] = {"mode": "selected", "selected_ids": ["extra-4095"]}
    filtered = repository.public_models(BusinessIdentity(identity.tenant_id, identity.project_id, identity.key_id, policy))
    assert [item["id"] for item in filtered["data"]] == ["extra-4095"]


def test_discovery_rejects_unvalidated_and_corrupt_capabilities(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    repository = CatalogAdministration(pg, test_settings)
    with pg.begin() as session:
        session.get(WorkflowRelease, catalog["release_id"]).validated = False
    assert repository.public_models(catalog["identity"])["data"] == []
    with pg.begin() as session:
        release = session.get(WorkflowRelease, catalog["release_id"])
        frozen = WorkflowRelease(tenant_id=release.tenant_id, project_id=release.project_id, workflow_id=release.workflow_id, version=2, digest=release.digest, manifest={**release.manifest, "presets": [{"quality": "invalid"}]}, validated=True, validation={"synthetic_corrupt_legacy": True})
        session.add(frozen)
        session.flush()
        session.query(PublicModel).update({PublicModel.release_id: frozen.id})
    assert repository.public_models(catalog["identity"], capabilities=True)["data"] == []
