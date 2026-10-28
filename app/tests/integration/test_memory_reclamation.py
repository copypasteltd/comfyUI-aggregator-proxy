from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from generation_contracts.memory import MemoryPolicyPatch
from generation_domain.values import utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.memory_reclamation import MemoryAdministration, MemoryReclaimer
from generation_infrastructure.models import Channel, Gpu, MemoryOperation, MemoryPolicy, Node
from generation_infrastructure.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_admin_controls import admin_context

pytestmark = pytest.mark.integration


class StockLoop:
    """Models the important upstream order: history -> free flags -> next item."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.history: dict[str, Any] = {}
        self.current: tuple[str, dict[str, Any]] | None = None
        self.free_flag = False
        self.unloaded = False
        self.busy = False
        self.fail_free = False
        self.lose_prompt = False
        self.version = "0.42.1"
        self.cache_free = 1

    def call(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if path == "/free":
            self.free_flag = True
            if self.fail_free:
                raise TimeoutError("response lost after acceptance")
            return {}
        if path == "/prompt":
            # Crossing into a NEW execution consumes earlier free flags.
            if self.current:
                raise AssertionError("barriers must be ordered")
            if self.free_flag:
                self.unloaded, self.free_flag, self.cache_free = True, False, 100
            prompt = str(uuid4())
            self.current = (prompt, body)
            if self.lose_prompt:
                self.lose_prompt = False
                raise TimeoutError("prompt response lost")
            return {"prompt_id": prompt}
        if path.startswith("/history/"):
            prompt = path.rsplit("/", 1)[-1]
            if self.current and self.current[0] == prompt:
                _, body = self.current
                self.history[prompt] = {"prompt": [1, prompt, body["prompt"], body["extra_data"]], "status": {"completed": True, "status_str": "success"}}
                self.current = None
                # Completion precedes flag processing; test never equates history1 with full proof.
            return {prompt: self.history[prompt]} if prompt in self.history else {}
        if path.startswith("/history?"):
            maximum = 1 if path.endswith("=1") else 100
            return dict(list(self.history.items())[-maximum:])
        if path == "/queue":
            row = [[1, self.current[0], self.current[1]["prompt"], self.current[1]["extra_data"]]] if self.current else []
            return {"queue_running": row, "queue_pending": [[1, "external", {}, {}]] if self.busy else []}
        if path.startswith("/object_info/"):
            return {path.rsplit("/", 1)[-1]: {}}
        if path == "/system_stats":
            return {"system": {"comfyui_version": self.version, "ram_total": 1000, "ram_free": self.cache_free}, "devices": [{"vram_total": 1000, "vram_free": self.cache_free}]}
        raise AssertionError(path)


def setup(pg: sessionmaker[Session], catalog: dict[str, Any], settings: Settings) -> tuple[MemoryAdministration, MemoryReclaimer, StockLoop, Any]:
    actor, _, _ = admin_context(pg, settings)
    admin = MemoryAdministration(pg, settings)
    admin.patch(actor, catalog["channel_ids"][0], MemoryPolicyPatch(expected_revision=1, enabled=True), str(uuid4()), "memory-test")
    stock = StockLoop()
    runner = MemoryReclaimer(pg, settings, http_factory=lambda base, auth: stock)
    return admin, runner, stock, utcnow()


def step(pg: sessionmaker[Session], catalog: dict[str, Any], runner: MemoryReclaimer, now: Any) -> None:
    with pg.begin() as session:
        session.get(Node, catalog["node_id"]).observed_at = now
    runner.tick(now)


def test_600_seconds_two_barriers_holds_all_channels_and_no_repeated_release(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    admin, runner, stock, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    step(pg, catalog, runner, start + timedelta(seconds=599))
    assert not any(path == "/free" for _, path, _ in stock.calls)
    # Observation interval can delay past the threshold, never release early.
    step(pg, catalog, runner, start + timedelta(seconds=610))
    with pg() as session:
        operation = session.scalar(select(MemoryOperation))
        assert operation.stage == "preflight"
        assert session.get(Gpu, catalog["gpu_id"]).management_hold == operation.id
    assert len(admin.get(catalog["channel_ids"][0])["channel_ids"]) == 2
    for i in range(1, 12):
        step(pg, catalog, runner, start + timedelta(seconds=610 + i * 2))
        with pg() as session:
            operation = session.scalar(select(MemoryOperation))
            if operation.evidence.get("barrier1_completed") and not operation.evidence.get("barrier2_completed"):
                assert session.get(Gpu, catalog["gpu_id"]).management_hold == operation.id
            if operation.stage == "completed":
                break
    assert stock.unloaded
    assert admin.get(catalog["channel_ids"][0])["status"] == "released"
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is None
        assert session.scalar(select(MemoryOperation)).evidence["proof"] == "free_ack_then_two_ordered_cpu_barriers"
    before = len([path for _, path, _ in stock.calls if path == "/free"])
    step(pg, catalog, runner, start + timedelta(seconds=1300))
    step(pg, catalog, runner, start + timedelta(seconds=2000))
    assert len([path for _, path, _ in stock.calls if path == "/free"]) == before == 1


@pytest.mark.parametrize("condition", ["busy", "unsupported", "stale", "boot", "other_hold"])
def test_blockers_never_send_free(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, condition: str) -> None:
    _, runner, stock, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    if condition == "busy":
        stock.busy = True
    elif condition == "unsupported":
        stock.version = "999.1"
    elif condition == "other_hold":
        with pg.begin() as session:
            session.get(Gpu, catalog["gpu_id"]).management_hold = uuid4()
    elif condition == "boot":
        with pg.begin() as session:
            session.get(Node, catalog["node_id"]).current_boot_id = uuid4()
    if condition == "stale":
        runner.tick(start + timedelta(seconds=610))
    else:
        step(pg, catalog, runner, start + timedelta(seconds=610))
    assert not any(path == "/free" for _, path, _ in stock.calls)


def test_lost_free_response_survives_restart_and_disable_keeps_hold(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    admin, runner, stock, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    step(pg, catalog, runner, start + timedelta(seconds=610))
    stock.fail_free = True
    step(pg, catalog, runner, start + timedelta(seconds=612))
    with pg() as session:
        op = session.scalar(select(MemoryOperation))
        assert op.stage == "unknown"
        identifier = op.id
    replacement = MemoryReclaimer(pg, test_settings, http_factory=lambda base, auth: stock)
    for i in range(3):
        step(pg, catalog, replacement, start + timedelta(seconds=720 + i * 10))
    actor, _, _ = admin_context(pg, test_settings, name="second-admin")
    admin.patch(actor, catalog["channel_ids"][0], MemoryPolicyPatch(expected_revision=2, enabled=False), str(uuid4()), "disable")
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]).management_hold == identifier
    assert len([path for _, path, _ in stock.calls if path == "/free"]) == 1


def test_external_activity_resets_idle_and_group_spans_same_origin_gpu(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    _, runner, stock, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    stock.history["new-direct-job"] = {"prompt": [], "status": {"completed": True}}
    step(pg, catalog, runner, start + timedelta(seconds=610))
    with pg() as session:
        assert session.scalar(select(MemoryPolicy)).idle_since == start + timedelta(seconds=610)
        assert session.scalar(select(MemoryOperation)) is None


def test_restart_after_free_intent_never_resubmits(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    _, runner, stock, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    step(pg, catalog, runner, start + timedelta(seconds=610))
    with pg.begin() as session:
        op = session.scalar(select(MemoryOperation))
        op.stage, op.lease_until = "free_intent", start + timedelta(seconds=620)
    step(pg, catalog, runner, start + timedelta(seconds=621))
    with pg() as session:
        assert session.scalar(select(MemoryOperation)).stage == "unknown"
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is not None
    assert not any(path == "/free" for _, path, _ in stock.calls)


def test_two_runner_claims_have_single_owner(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    _, runner, _, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    step(pg, catalog, runner, start + timedelta(seconds=610))
    other = MemoryReclaimer(pg, test_settings)
    assert runner._claim(start + timedelta(seconds=611)) is not None
    assert other._claim(start + timedelta(seconds=611)) is None


def test_new_queued_request_before_free_wins_and_releases_only_owned_hold(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    _, runner, stock, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    step(pg, catalog, runner, start + timedelta(seconds=610))
    JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "new work"}}, str(uuid4()), "new-job")
    step(pg, catalog, runner, start + timedelta(seconds=612))
    with pg() as session:
        assert session.scalar(select(MemoryOperation)).stage == "skipped"
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is None
    assert not any(path == "/free" for _, path, _ in stock.calls)


def test_lost_barrier_response_is_found_without_resubmission(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    _, runner, stock, start = setup(pg, catalog, test_settings)
    step(pg, catalog, runner, start)
    step(pg, catalog, runner, start + timedelta(seconds=610))
    step(pg, catalog, runner, start + timedelta(seconds=612))
    stock.lose_prompt = True
    for i in range(1, 14):
        step(pg, catalog, runner, start + timedelta(seconds=612 + i * 2))
        with pg() as session:
            if session.scalar(select(MemoryOperation)).stage == "completed":
                break
    with pg() as session:
        assert session.scalar(select(MemoryOperation)).stage == "completed"
    assert len([path for _, path, _ in stock.calls if path == "/prompt"]) == 2


def test_changed_origin_requires_explicit_policy_update_without_unique_conflict(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings) -> None:
    admin, _, _, _ = setup(pg, catalog, test_settings)
    with pg.begin() as session:
        session.get(Channel, catalog["channel_ids"][0]).base_url = "http://127.0.0.1:19188"
    view = admin.get(catalog["channel_ids"][0])
    assert view["status"] == "channel_changed" and not view["enabled"]
    actor, _, _ = admin_context(pg, test_settings, name="origin-editor")
    result = admin.patch(actor, catalog["channel_ids"][0], MemoryPolicyPatch(expected_revision=view["revision"], enabled=True), str(uuid4()), "changed-origin")
    assert result["enabled"] and admin.get(catalog["channel_ids"][0])["status"] == "observing"
