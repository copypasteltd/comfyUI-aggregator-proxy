from __future__ import annotations

from typing import Any

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id
from generation_infrastructure.admin_workflows import WorkflowAdministration
from generation_infrastructure.models import (
    AdminMutation,
    AuditEvent,
    OutboxEvent,
    Workflow,
    WorkflowRelease,
)
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import PASSWORD, admin_context
from tests.integration.test_workflow_imports import preview, source
from tests.unit.test_workflow_scalar_targets import overwrite_edge, scalar_manifest

pytestmark = pytest.mark.integration


def mutation_counts(pg: sessionmaker[Session]) -> tuple[int, ...]:
    with pg() as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in (WorkflowRelease, AdminMutation, AuditEvent, OutboxEvent))


@pytest.mark.parametrize("task_type", ["text_to_image", "text_to_video", "text_to_audio"])
@pytest.mark.parametrize("kind", ["binding", "preset"])
def test_admin_http_freeze_rejects_edge_writes_without_persistent_side_effects(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, task_type: str, kind: str) -> None:
    admin_context(pg, test_settings)
    manifest = scalar_manifest(task_type)
    overwrite_edge(manifest, kind)
    with pg.begin() as session:
        identity = catalog["identity"]
        workflow = Workflow(tenant_id=identity.tenant_id, project_id=identity.project_id, name="Synthetic unsafe scalar target", task_type=task_type, draft=manifest)
        session.add(workflow)
        session.flush()
        identifier = workflow.id
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        client.headers["Origin"] = test_settings.public_origin
        login = client.post("/admin/api/v1/session", json={"username": "test-admin", "password": PASSWORD})
        assert login.status_code == 200
        headers = {"Origin": test_settings.public_origin, "X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "synthetic-scalar-freeze-104", "If-Match": '"1"'}
        before = mutation_counts(pg)
        for _ in range(2):
            response = client.post(f"/admin/api/v1/workflows/{public_id('wf', identifier)}/releases", json={"expected_revision": 1}, headers=headers)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == ("invalid_binding" if kind == "binding" else "invalid_preset_target")
            assert "SyntheticGenerator" not in response.text
            assert mutation_counts(pg) == before
    with pg() as session:
        stored = session.get(Workflow, identifier)
        assert stored.revision == 1 and stored.draft == manifest


@pytest.mark.parametrize("kind", ["binding", "preset"])
def test_channel_import_keeps_unsafe_graph_editable_but_never_publishable(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, kind: str) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    manifest = scalar_manifest("text_to_image")
    overwrite_edge(manifest, kind)
    service, source_data, _, _ = source(pg, catalog, actor, test_settings, manifest)
    selected = preview(service, actor, source_data)
    assert selected["validation"]["publishable"] is False
    imported = service.commit(actor, parse_id("wip", selected["id"]), selected["candidate_digest"], "synthetic-import-draft-104", "test")
    identifier = parse_id("wf", imported["workflow"]["id"])
    before = mutation_counts(pg)
    with pytest.raises(DomainError) as rejected:
        WorkflowAdministration(pg, test_settings).release(actor, identifier, 1, "synthetic-import-freeze-104", "test")
    assert rejected.value.code == ("invalid_binding" if kind == "binding" else "invalid_preset_target")
    assert mutation_counts(pg) == before
    with pg() as session:
        assert session.get(Workflow, identifier).draft == manifest
