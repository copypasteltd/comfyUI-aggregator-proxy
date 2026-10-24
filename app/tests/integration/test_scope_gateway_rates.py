from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from generation_domain.values import parse_id
from generation_infrastructure.models import ApiKey, Project, RateState, Tenant
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_gateway import admission_body, recovery_body, service_headers
from test_gateway import gateway_client as gateway_client

pytestmark = pytest.mark.integration


def business_windows(pg: sessionmaker[Session]) -> dict[str, Any]:
    with pg() as session:
        return {row.identity: (row.tokens, row.updated_at, row.recent) for row in session.scalars(select(RateState)) if row.identity.startswith(("tenant:", "project:", "business:"))}


@pytest.mark.parametrize("level", ["tenant", "project", "key"])
def test_gateway_hierarchy_rejection_is_atomic_and_recovery_survives_scope_disable(gateway_client: tuple[TestClient, dict[str, str], dict[str, Any]], pg: sessionmaker[Session], catalog: dict[str, Any], level: str) -> None:
    client, headers, info = gateway_client
    identity = catalog["identity"]
    with pg.begin() as session:
        row = session.get(Tenant, identity.tenant_id) if level == "tenant" else session.get(Project, identity.project_id) if level == "project" else session.get(ApiKey, parse_id("key", info["binding"]["key_id"]))
        if level == "key":
            policy = deepcopy(row.policy)
            policy["limits"]["rpm"]["value"] = 1
            policy["limits"]["burst"]["value"] = 1
            row.policy = policy
        else:
            limits = deepcopy(row.limits)
            limits["rpm"]["value"] = 1
            limits["burst"]["value"] = 1
            row.limits = limits
    body = admission_body()
    result = client.post("/gateway/v1/admissions", headers=headers, json=body)
    assert result.status_code == 202, result.text
    before = business_windows(pg)
    refused = client.post("/gateway/v1/admissions", headers=headers, json=admission_body())
    assert refused.status_code == 429 and business_windows(pg) == before
    with pg.begin() as session:
        session.get(Tenant, identity.tenant_id).enabled = False
        session.get(Project, identity.project_id).enabled = False
    assert client.get("/gateway/v1/jobs", headers=headers).status_code == 403
    recovered = client.post("/gateway/v1/admission-requests/lookup", headers=service_headers(headers), json=recovery_body(body, headers))
    assert recovered.status_code == 200 and recovered.json()["status"] == "admitted" and recovered.json()["admission"]["status"] == "pending", recovered.text
    assert business_windows(pg) == before
