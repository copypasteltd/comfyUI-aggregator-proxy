from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.memory_reconciliation import KEY, PROOF, MemoryReconciler
from generation_infrastructure.models import (
    Attempt,
    AuditEvent,
    Channel,
    Gpu,
    GpuOccupancy,
    MemoryOperation,
    MemoryPolicy,
    Node,
    OriginReservation,
)
from sqlalchemy import select

from tests.integration.test_memory_reclamation import setup, step

pytestmark = pytest.mark.integration


def unknown(pg, catalog, settings):
    _, runner, stock, now = setup(pg, catalog, settings)
    step(pg, catalog, runner, now)
    step(pg, catalog, runner, now + timedelta(seconds=610))
    stock.fail_free = True
    step(pg, catalog, runner, now + timedelta(seconds=612))
    with pg.begin() as session:
        session.get(Node, catalog["node_id"]).observed_at = utcnow()
    with pg() as session:
        op = session.scalar(select(MemoryOperation))
        assert op.stage == "unknown"
        identifier = op.id
    service = MemoryReconciler(pg, settings, http_factory=lambda base, auth: stock)
    return service, stock, identifier


def finish(service, identifier):
    for _ in range(8):
        result = service.step(identifier)
        if result["stage"] == "skipped":
            return result
    pytest.fail("reconciliation did not finish")


def held(pg, catalog, identifier):
    with pg() as session:
        assert session.get(Gpu, catalog["gpu_id"]).management_hold == identifier
        assert session.scalar(select(MemoryPolicy)).active_operation_id == identifier
        assert session.get(MemoryOperation, identifier).stage == "unknown"


def test_unknown_release_reconciles_without_retrying_free_and_resets_idle(pg, catalog, test_settings):
    service, stock, identifier = unknown(pg, catalog, test_settings)
    before = utcnow()
    result = finish(service, identifier)
    assert result["reconciliation"]["proof"] == PROOF
    assert result["error_code"] == "http_write_outcome_unknown"
    assert stock.unloaded  # Original unknown /free flag is consumed by the serial loop.
    assert len([1 for method, path, _ in stock.calls if path == "/free"]) == 1
    assert len([1 for method, path, _ in stock.calls if method == "POST" and path == "/prompt"]) == 2
    with pg() as session:
        policy = session.scalar(select(MemoryPolicy))
        assert policy.active_operation_id is None and not policy.released
        assert policy.status == "idle_countdown" and policy.idle_since >= before
        assert session.get(Gpu, catalog["gpu_id"]).management_hold is None
        actions = list(session.scalars(select(AuditEvent.action)))
        assert actions.count("memory.reconcile_started") == actions.count("memory.reconcile_completed") == 1
    calls = len(stock.calls)
    assert service.step(identifier) == result
    assert len(stock.calls) == calls


def test_inspect_does_not_mutate_or_send_http(pg, catalog, test_settings):
    service, stock, identifier = unknown(pg, catalog, test_settings)
    calls = len(stock.calls)
    assert service.inspect(identifier)["reconciliation"] is None
    held(pg, catalog, identifier)
    with pg() as session:
        assert KEY not in session.get(MemoryOperation, identifier).evidence
    assert len(stock.calls) == calls


@pytest.mark.parametrize("blocker", ["other_hold", "stale", "group", "origin", "occupied", "business_job", "native", "queue", "version"])
def test_observation_and_ownership_blockers_keep_hold(pg, catalog, test_settings, blocker):
    service, stock, identifier = unknown(pg, catalog, test_settings)
    if blocker in {"occupied", "business_job"}:
        job = JobRepository(pg).create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "synthetic recovery blocker"}}, str(uuid4()), "recovery-blocker")
        job_id = parse_id("job", job["id"])
    with pg.begin() as session:
        if blocker == "other_hold":
            session.get(Gpu, catalog["gpu_id"]).management_hold = uuid4()
        elif blocker == "stale":
            session.get(Node, catalog["node_id"]).observed_at = utcnow() - timedelta(seconds=30)
        elif blocker == "origin":
            session.get(Channel, catalog["channel_ids"][0]).base_url = "http://127.0.0.1:8189"
        elif blocker == "group":
            op = session.get(MemoryOperation, identifier)
            op.evidence = {**op.evidence, "channel_ids": []}
        elif blocker == "occupied":
            attempt = Attempt(job_id=job_id, channel_id=catalog["channel_ids"][0], channel_snapshot={}, node_id=catalog["node_id"], boot_id=catalog["boot_id"], gpu_id=catalog["gpu_id"], fence=1, lease_expires_at=utcnow() + timedelta(seconds=60))
            session.add(attempt)
            session.flush()
            session.add(GpuOccupancy(gpu_id=catalog["gpu_id"], attempt_id=attempt.id, job_id=job_id, uncertain=True))
        elif blocker == "native":
            session.add(OriginReservation(key_id=catalog["identity"].key_id, channel_id=catalog["channel_ids"][0], state="unknown", config_digest="a" * 64, gpu_ids=[str(catalog["gpu_id"])], expires_at=utcnow() + timedelta(seconds=60), updated_at=utcnow()))
    if blocker == "queue":
        stock.busy = True
    elif blocker == "version":
        stock.version = "999.0"
    with pytest.raises(DomainError):
        service.step(identifier)
    if blocker != "other_hold":
        held(pg, catalog, identifier)
    assert not any(path == "/prompt" for _, path, _ in stock.calls)


def test_prompt_response_lost_is_found_by_original_intent_after_restart(pg, catalog, test_settings):
    service, stock, identifier = unknown(pg, catalog, test_settings)
    stock.lose_prompt = True
    with pytest.raises(TimeoutError):
        service.step(identifier)
    held(pg, catalog, identifier)
    service.relinquish(identifier)
    replacement = MemoryReconciler(pg, test_settings, http_factory=lambda base, auth: stock)
    finish(replacement, identifier)
    assert len([1 for method, path, _ in stock.calls if method == "POST" and path == "/prompt"]) == 2


def test_unobserved_prompt_intent_is_never_resubmitted(pg, catalog, test_settings):
    service, stock, identifier = unknown(pg, catalog, test_settings)
    stock.lose_prompt = True
    with pytest.raises(TimeoutError):
        service.step(identifier)
    stock.current = None
    calls = len(stock.calls)
    with pytest.raises(DomainError, match="维护核对") as caught:
        service.step(identifier)
    assert caught.value.code == "memory_reconcile_submission_unknown"
    assert not any(method == "POST" for method, _, _ in stock.calls[calls:])
    held(pg, catalog, identifier)


@pytest.mark.parametrize("change", ["boot", "owner", "history_graph", "history_error", "new_queue"])
def test_midflight_changes_and_bad_proof_never_release(pg, catalog, test_settings, change):
    service, stock, identifier = unknown(pg, catalog, test_settings)
    service.step(identifier)
    if change in {"history_graph", "history_error"}:
        prompt = stock.current[0]
        stock.call("GET", "/history/" + prompt)
        if change == "history_graph":
            stock.history[prompt]["prompt"][2] = {}
        else:
            stock.history[prompt]["status"]["status_str"] = "error"
    elif change == "new_queue":
        stock.busy = True
    else:
        with pg.begin() as session:
            if change == "boot":
                session.get(Node, catalog["node_id"]).current_boot_id = uuid4()
            else:
                session.get(MemoryOperation, identifier).owner = uuid4()
    with pytest.raises(DomainError):
        service.step(identifier)
    held(pg, catalog, identifier)


def test_new_boot_before_reconciliation_is_pinned_but_not_misreported_as_release(pg, catalog, test_settings):
    service, stock, identifier = unknown(pg, catalog, test_settings)
    with pg.begin() as session:
        session.get(Node, catalog["node_id"]).current_boot_id = uuid4()
    result = finish(service, identifier)
    assert result["reconciliation"]["release_verified"] is False
