from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_contracts.channel_queries import ChannelQuery
from generation_contracts.defaults import default_limits
from generation_contracts.responses import ChannelDetailView
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from generation_infrastructure.admin_channels import ChannelAdministration, begin_channel_read
from generation_infrastructure.models import (
    AdminUser,
    AuditEvent,
    Channel,
    ChannelRevision,
    OutboxEvent,
    Project,
    Tenant,
)
from generation_infrastructure.security import issue_secret, password_hash
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-channel-query-password"


def seed(pg: sessionmaker[Session], catalog: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    with pg.begin() as session:
        base = session.get(Channel, catalog["channel_ids"][0])
        assert base is not None
        tenant = Tenant(name="Lookup other tenant", limits=default_limits())
        session.add(tenant)
        session.flush()
        project = Project(tenant_id=tenant.id, name="Lookup other project", limits=default_limits())
        session.add(project)
        session.flush()
        rows = []
        for index, status in enumerate(["enabled", "disabled", "draining", "deleted", "other"]):
            row = Channel(
                tenant_id=tenant.id if status == "other" else base.tenant_id,
                project_id=project.id if status == "other" else base.project_id,
                node_id=base.node_id,
                gpu_id=base.gpu_id,
                name="Lookup AlPhA%_" + chr(92) + "-" + status,
                base_url="http://127.0.0.1:8188/LOOKUP-" + str(index),
                enabled=status != "disabled",
                draining=status in ("draining", "deleted"),
                deleted_at=now if status == "deleted" else None,
                encrypted_auth="SYNTHETIC-ENCRYPTED-PRIVATE",
                created_at=now,
            )
            session.add(row)
            rows.append(row)
        session.flush()
        return {
            "rows": [row.id for row in rows],
            "tenant": base.tenant_id,
            "project": base.project_id,
            "node": base.node_id,
            "other_project": project.id,
        }


def administration(pg: sessionmaker[Session], settings: Settings) -> ChannelAdministration:
    return ChannelAdministration(pg, settings)


def identifiers(result: dict[str, Any]) -> set[str]:
    return {row["id"] for row in result["data"]}


@pytest.mark.parametrize("query", ["alpha", "ALPHA", "%_", chr(92), "lookup-"])
def test_literal_case_insensitive_name_or_address(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, query: str
) -> None:
    rows = seed(pg, catalog)["rows"]
    result = administration(pg, test_settings).list_channels(query=ChannelQuery(q=query))
    assert identifiers(result) == {public_id("ch", identifier) for identifier in rows}
    assert set(result) == {"data", "page", "has_more"} and not result["has_more"]
    assert all("authorization" not in row and row["has_authorization"] for row in result["data"])


@pytest.mark.parametrize(
    ("status", "indices"),
    [("enabled", [0, 2, 4]), ("disabled", [1]), ("draining", [2]), ("deleted", [3])],
)
def test_status_uses_live_facts_and_excludes_deleted_from_live_filters(
    pg: sessionmaker[Session],
    catalog: dict[str, Any],
    test_settings: Settings,
    status: str,
    indices: list[int],
) -> None:
    values = seed(pg, catalog)
    result = administration(pg, test_settings).list_channels(
        query=ChannelQuery.model_validate({"q": "Lookup", "status": status})
    )
    assert identifiers(result) == {public_id("ch", values["rows"][index]) for index in indices}


def test_intersection_exact_ids_unknown_ids_and_cross_project(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    values = seed(pg, catalog)
    admin = administration(pg, test_settings)
    common = {
        "q": "lookup",
        "tenant_id": public_id("ten", values["tenant"]),
        "project_id": public_id("prj", values["project"]),
        "node_id": public_id("node", values["node"]),
        "status": "draining",
    }
    wanted = public_id("ch", values["rows"][2])
    assert identifiers(admin.list_channels(query=ChannelQuery.model_validate(common))) == {wanted}
    assert identifiers(
        admin.list_channels(query=ChannelQuery.model_validate({**common, "channel_id": wanted}))
    ) == {wanted}
    for replacement in [
        {"channel_id": public_id("ch", uuid4())},
        {"tenant_id": public_id("ten", uuid4())},
        {"project_id": public_id("prj", values["other_project"])},
        {"node_id": public_id("node", uuid4())},
        {"q": "no-such-name"},
    ]:
        assert (
            admin.list_channels(query=ChannelQuery.model_validate({**common, **replacement}))[
                "data"
            ]
            == []
        )


def test_stable_fifty_row_pages_and_legacy_page_argument(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    now = utcnow()
    with pg.begin() as session:
        base = session.get(Channel, catalog["channel_ids"][0])
        assert base is not None
        for index in range(1, 102):
            session.add(
                Channel(
                    id=UUID(int=index),
                    tenant_id=base.tenant_id,
                    project_id=base.project_id,
                    node_id=base.node_id,
                    gpu_id=base.gpu_id,
                    name="Paging-group-" + str(index),
                    base_url="http://127.0.0.1:8188",
                    created_at=now + timedelta(days=1),
                )
            )
    admin = administration(pg, test_settings)
    pages = [
        admin.list_channels(query=ChannelQuery(q="paging-group-", page=page)) for page in [1, 2, 3]
    ]
    assert [len(page["data"]) for page in pages] == [50, 50, 1]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert [row["id"] for page in pages for row in page["data"]] == [
        public_id("ch", UUID(int=index)) for index in range(1, 102)
    ]
    assert [row["id"] for row in admin.list_channels(2)["data"]] == [
        row["id"] for row in pages[1]["data"]
    ]


def test_detail_history_is_bounded_and_never_exposes_ciphertext(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    identifier = seed(pg, catalog)["rows"][3]
    with pg.begin() as session:
        for revision in range(1, 62):
            session.add(
                ChannelRevision(
                    channel_id=identifier,
                    revision=revision,
                    config={
                        "base_url": "http://127.0.0.1:8188",
                        "encrypted_auth": "SYNTHETIC-PRIVATE",
                    },
                )
            )
        before = (
            session.scalar(select(func.count()).select_from(AuditEvent)),
            session.scalar(select(func.count()).select_from(OutboxEvent)),
        )
    admin = administration(pg, test_settings)
    detail = admin.get_channel(identifier)
    assert ChannelDetailView.model_validate(detail).id == public_id("ch", identifier)
    assert detail["deleted_at"] is not None and len(detail["revisions"]) == 50
    assert [row["revision"] for row in detail["revisions"]] == list(range(61, 11, -1))
    assert "PRIVATE" not in str(detail) and "encrypted_auth" not in str(detail)
    admin.list_channels(query=ChannelQuery(q="Lookup"))
    with pg.begin() as session:
        begin_channel_read(session)
        assert before == (
            session.scalar(select(func.count()).select_from(AuditEvent)),
            session.scalar(select(func.count()).select_from(OutboxEvent)),
        )
        assert session.scalar(text("SHOW transaction_read_only")) == "on"
        assert session.scalar(text("SHOW statement_timeout")) == "5s"
        assert session.scalar(text("SHOW lock_timeout")) == "1s"


@pytest.mark.parametrize("detail", [False, True])
def test_locked_table_fails_with_fixed_error_and_recovers(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, detail: bool
) -> None:
    admin = administration(pg, test_settings)
    with pg.begin() as blocking:
        blocking.execute(text("LOCK TABLE channels IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(DomainError) as rejected:
            admin.get_channel(catalog["channel_ids"][0]) if detail else admin.list_channels()
        assert rejected.value.status == 503 and rejected.value.code == "channel_query_unavailable"
    assert admin.list_channels()["data"]


@pytest.mark.parametrize("role", ["administrator", "operator", "workflow_maintainer", "observer"])
def test_http_guards_typed_history_and_passive_session(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, role: str
) -> None:
    identifier = seed(pg, catalog)["rows"][0]
    with pg.begin() as session:
        session.add(
            AdminUser(
                username="channel-query-" + role, password_hash=password_hash(PASSWORD), role=role
            )
        )
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        login = client.post(
            "/admin/api/v1/session",
            headers={"Origin": test_settings.public_origin},
            json={"username": "channel-query-" + role, "password": PASSWORD},
        )
        assert login.status_code == 200
        result = client.get(
            "/admin/api/v1/channels",
            params={"q": "alpha%_" + chr(92), "channel_id": public_id("ch", identifier)},
        )
        assert result.status_code == 200 and identifiers(result.json()) == {
            public_id("ch", identifier)
        }
        detail = client.get("/admin/api/v1/channels/" + public_id("ch", identifier))
        assert detail.status_code == 200 and ChannelDetailView.model_validate(
            detail.json()
        ).id == public_id("ch", identifier)
        assert (
            detail.headers["etag"]
            and client.get("/admin/api/v1/channels/ch_" + "f" * 32).status_code == 404
        )


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": 10001},
        {"page": "1.0"},
        {"page": "1e1"},
        {"page": "true"},
        {"q": ""},
        {"q": " a"},
        {"q": "a "},
        {"q": "a\nb"},
        {"q": "\x85"},
        {"q": "a" * 129},
        {"channel_id": "bad"},
        {"tenant_id": "bad"},
        {"project_id": "bad"},
        {"node_id": "bad"},
        {"status": "all"},
        {"unknown": "PRIVATE"},
    ],
)
def test_http_rejects_ambiguous_query_without_echoing_input(
    pg: sessionmaker[Session], test_settings: Settings, params: dict[str, Any]
) -> None:
    with pg.begin() as session:
        session.add(
            AdminUser(
                username="channel-query-validation",
                password_hash=password_hash(PASSWORD),
                role="administrator",
            )
        )
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        assert (
            client.post(
                "/admin/api/v1/session",
                headers={"Origin": test_settings.public_origin},
                json={"username": "channel-query-validation", "password": PASSWORD},
            ).status_code
            == 200
        )
        result = client.get("/admin/api/v1/channels", params=params)
        assert result.status_code == 422 and "PRIVATE" not in result.text


@pytest.mark.parametrize("prefix", ["sk-gen", "worker", "gateway"])
def test_other_identities_cannot_read_channels(
    pg: sessionmaker[Session], test_settings: Settings, prefix: str
) -> None:
    _, token = issue_secret(prefix)
    with TestClient(create_app(test_settings), base_url=test_settings.public_origin) as client:
        for path in ["/admin/api/v1/channels", "/admin/api/v1/channels/ch_" + "a" * 32]:
            assert client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 401
