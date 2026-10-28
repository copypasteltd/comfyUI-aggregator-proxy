from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_application.ports import BusinessIdentity
from generation_contracts.model_usage import ModelDocumentationPatch
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.models import AuditEvent, PublicModel, WorkflowRelease
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, admin_context
from tests.unit.test_model_usage import usage_manifest

pytestmark = pytest.mark.integration


def complete_catalog(pg: sessionmaker[Session], catalog: dict[str, Any]) -> str:
    manifest, defaults = usage_manifest()
    with pg.begin() as session:
        original = session.get(WorkflowRelease, catalog["release_id"])
        release = WorkflowRelease(tenant_id=original.tenant_id, project_id=original.project_id, workflow_id=original.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        model = session.scalar(select(PublicModel))
        model.release_id = release.id
        model.defaults = defaults
        return public_id("mdl", model.id)


def test_documentation_is_audited_idempotent_and_preserves_published_mapping(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identifier = complete_catalog(pg, catalog)
    actor, _, _ = admin_context(pg, test_settings)
    service = CatalogAdministration(pg, test_settings)
    before = service.models()["data"][0]
    data = ModelDocumentationPatch.model_validate({"expected_revision": before["revision"], "documentation": {"display_name": "Readable model", "description": "Published ability", "notes": ["Exact presets only"]}})
    token = str(uuid4())
    updated = service.patch_documentation(actor, parse_id("mdl", identifier), data, token, "docs-update")
    assert service.patch_documentation(actor, parse_id("mdl", identifier), data, token, "replay") == updated
    assert updated["published"] and updated["revision"] == before["revision"] + 1
    assert all(updated[key] == before[key] for key in ("release_id", "channel_ids", "defaults", "task_type"))
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "model.documentation_updated")) == 1
    with pytest.raises(DomainError) as stale:
        service.patch_documentation(actor, parse_id("mdl", identifier), data, str(uuid4()), "stale")
    assert stale.value.code == "config_revision_conflict"


def test_public_list_detail_and_capabilities_share_safe_usage_and_key_visibility(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    identifier = complete_catalog(pg, catalog)
    service = CatalogAdministration(pg, test_settings)
    models = service.public_models(catalog["identity"])["data"]
    capabilities = service.public_models(catalog["identity"], capabilities=True)["data"]
    detail = service.public_model(catalog["identity"], "synthetic-image")
    assert len(models) == len(capabilities) == 1
    assert models[0]["usage"] == detail["usage"] == capabilities[0]["usage"]
    assert "PRIVATE-" not in str(models)
    assert service.usage(parse_id("mdl", identifier)) == detail["usage"]
    original = catalog["identity"]
    for field in ("models", "workflow_releases", "channels"):
        policy = deepcopy(original.policy)
        policy[field] = {"mode": "selected", "selected_ids": []}
        hidden = BusinessIdentity(original.tenant_id, original.project_id, original.key_id, policy)
        assert service.public_models(hidden)["data"] == []


@pytest.mark.parametrize("role,allowed", [("administrator", True), ("observer", False)])
def test_http_documentation_requires_admin_permission_csrf_and_revision(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str, allowed: bool) -> None:
    identifier = complete_catalog(pg, catalog)
    admin_context(pg, test_settings, role=role)
    path = "/admin/api/v1/public-models/" + identifier
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert client.get(path + "/usage").status_code == 401
        login = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD}, headers={"Origin": test_settings.public_origin})
        assert login.status_code == 200
        revision = CatalogAdministration(pg, test_settings).models()["data"][0]["revision"]
        data = {"expected_revision": revision, "documentation": {"description": "Updated safely"}}
        headers = {"Origin": test_settings.public_origin, "X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": str(uuid4()), "If-Match": '"' + str(revision) + '"'}
        assert client.patch(path + "/documentation", json=data).status_code == 403
        response = client.patch(path + "/documentation", json=data, headers=headers)
        assert response.status_code == (200 if allowed else 403)
        if allowed:
            assert response.json()["published"]
            assert client.get(path + "/usage").json()["description"] == "Updated safely"
        client.delete("/admin/api/v1/session", headers=headers)
