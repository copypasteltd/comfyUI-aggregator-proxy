from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import Channel
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def test_queue_counts_require_recent_matching_revision_and_reachable_observation(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    leases = LeaseRepository(pg, SecretVault(test_settings.encryption_key.get_secret_value()))
    queries = AdminQueries(pg, test_settings)
    identifier = catalog["channel_ids"][0]
    observation = {"channel_id": public_id("ch", identifier), "revision": 1, "observed_ago_ms": 1, "observation": {"reachable": True, "queue_running": 1, "queue_pending": 2}}
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [observation])
    row = queries.channel_queue(identifier)
    assert row["comfy_queue_running"] == 1 and row["comfy_queue_pending"] == 2
    with pg.begin() as session:
        channel = session.get(Channel, identifier)
        channel.observation = {**channel.observation, "queue_observed_at": utcnow().timestamp() - 16}
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [{**observation, "observed_ago_ms": 20000}])
    assert queries.channel_queue(identifier)["comfy_queue_running"] is None
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [{**observation, "revision": 2}])
    assert queries.channel_queue(identifier)["observation_status"] == "unknown"
    leases.heartbeat(catalog["node_id"], catalog["boot_id"], [{**observation, "observation": {"reachable": False, "error_code": "timeout"}}])
    assert queries.channel_queue(identifier)["comfy_queue_pending"] is None
    leases.register(catalog["node_id"], uuid4(), {"gpus": []})
    assert queries.channel_queue(identifier)["observation_status"] == "unknown"
    with pytest.raises(DomainError):
        leases.heartbeat(catalog["node_id"], catalog["boot_id"], [])
