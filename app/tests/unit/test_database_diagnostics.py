from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from generation_contracts.validation import validate
from generation_infrastructure.database_diagnostics import (
    REASONS,
    capture_database_failure,
    database_operation,
    record_database_failure,
)
from sqlalchemy.exc import DBAPIError

from scripts.native_database_observer import DatabaseObserver
from scripts.native_database_probe import log_event


def failure(state: Any = "55P03", *, invalidated: bool = False) -> DBAPIError:
    driver = SimpleNamespace(sqlstate=state, detail="PRIVATE-DETAIL", hint="PRIVATE-HINT")
    return DBAPIError("PRIVATE-SQL", {"password": "PRIVATE-PARAMETER"}, driver, connection_invalidated=invalidated)


@pytest.mark.parametrize("state,reason", REASONS.items())
def test_only_frozen_sqlstate_pairs_are_exported(state: str, reason: str) -> None:
    with capture_database_failure() as capture:
        record_database_failure("gateway_auth", failure(state, invalidated=True))
    validate("database-failure", capture.value)
    assert capture.value == {"operation": "gateway_auth", "sqlstate": state, "reason": reason, "connection_invalidated": True}
    assert "PRIVATE" not in json.dumps(capture.value)


@pytest.mark.parametrize("state", [None, "", "PRIVATE-STATE", "55p03", "57014\nPRIVATE", 57014, ["55P03"], {"state": "55P03"}])
def test_unknown_sqlstates_cannot_create_log_fields(state: Any) -> None:
    with capture_database_failure() as capture:
        record_database_failure("http_request", failure(state))
    validate("database-failure", capture.value)
    assert capture.value and capture.value["sqlstate"] is None and capture.value["reason"] == "other"


def test_first_failure_and_nested_contexts_are_isolated() -> None:
    record_database_failure("http_request", failure())
    with capture_database_failure() as outer:
        with pytest.raises(DBAPIError), database_operation("gateway_auth"):
            raise failure()
        with capture_database_failure() as nested:
            record_database_failure("audit_snapshot", failure("57014"))
        record_database_failure("http_request", failure("08006"))
    assert outer.value and outer.value["operation"] == "gateway_auth"
    assert nested.value and nested.value["operation"] == "audit_snapshot"
    with capture_database_failure() as clean:
        assert clean.value is None


def test_broken_driver_metadata_cannot_replace_the_original_failure() -> None:
    class DriverError(Exception):
        @property
        def sqlstate(self) -> str:
            raise ValueError("PRIVATE-DRIVER")
    error = DBAPIError("PRIVATE-SQL", {}, DriverError("PRIVATE-TEXT"))
    with capture_database_failure() as capture:
        record_database_failure("http_request", error)
    assert capture.value and capture.value["reason"] == "other"
    assert "PRIVATE" not in json.dumps(capture.value)


def test_context_is_copied_to_thread_pool_and_reset_after_cancellation() -> None:
    async def request(state: str) -> dict[str, Any] | None:
        with capture_database_failure() as capture:
            await asyncio.to_thread(record_database_failure, "http_request", failure(state))
            await asyncio.sleep(0)
            return capture.value
    async def scenario() -> None:
        values = await asyncio.gather(*(request(state) for state in REASONS))
        assert [value["sqlstate"] for value in values if value] == list(REASONS)
        with pytest.raises(asyncio.CancelledError), capture_database_failure():
            raise asyncio.CancelledError
        with capture_database_failure() as clean:
            assert clean.value is None
    asyncio.run(scenario())


def test_parallel_threads_share_only_their_explicit_capture() -> None:
    from contextvars import copy_context
    with capture_database_failure() as capture, ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(copy_context().run, record_database_failure, "gateway_auth", failure()) for _ in range(20)]
        for future in futures:
            future.result(timeout=3)
    assert capture.value and capture.value["reason"] == "lock_unavailable"


def observer() -> DatabaseObserver:
    return DatabaseObserver({"host": "127.0.0.1", "port": 55438, "password": "PRIVATE-PASSWORD"}, ["render_control_native_" + "a"*12, "render_sub2api_test_" + "a"*12])


@pytest.mark.parametrize("host,port,databases", [("localhost", 55438, ["render_control_native_"+"a"*12, "render_sub2api_test_"+"a"*12]), ("127.0.0.1", 5432, []), ("127.0.0.1", 55438, ["render_control", "render_control_test"]), ("127.0.0.1", 55438, ["render_control_native_"+"a"*12, "render_sub2api_test_"+"b"*12])])
def test_observer_refuses_unowned_database_scope(host: str, port: int, databases: list[str]) -> None:
    with pytest.raises(ValueError, match="database_observer_scope_invalid"):
        DatabaseObserver({"host": host, "port": port}, databases)


def test_observer_aliases_blocking_chain_and_deduplicates_without_text() -> None:
    sample = observer()
    rows = [(1001, "idle in transaction", "Client", "PRIVATE-EVENT", 1200, 400, []), (1002, "active", "Lock", "tuple", 600, 550, [1001]), (1003, "PRIVATE-STATE", "PRIVATE-TYPE", "PRIVATE-EVENT", -1, -1, [9999])]
    sample.observe(rows)
    sample.observe(rows)
    report = sample.close()
    assert report["passed"] and report["samples"] == 2
    assert report["observations"][1]["blockers"] == ["backend-1"]
    assert report["observations"][1]["samples"] == 2
    assert report["observations"][2]["blockers"] == ["<outside-sample>"]
    assert "PRIVATE" not in repr(sample) + json.dumps(report)
    assert not any(str(pid) in json.dumps(report) for pid in (1001, 1002, 1003, 9999))


def test_observer_memory_limits_are_explicit() -> None:
    sample = observer()
    for group in range(3):
        sample.observe([(group*1000+i, "active", "Lock", "tuple", 0, 600, []) for i in range(129)])
    report = sample.close()
    assert report["backend_aliases"] == 256 and len(report["observations"]) == 128
    assert report["alias_limit_hits"] > 0 and report["observation_limit_hits"] > 0 and report["truncated_samples"] == 3


def test_observer_blocker_list_limit_and_unknown_names_are_explicit() -> None:
    sample = observer()
    sample.observe([(10001, "active", "PRIVATE-TYPE", "PRIVATE-EVENT", 0, 600, list(range(1000)))])
    report = sample.close()
    assert report["blocker_limit_hits"] == 1
    assert report["observations"][0]["blockers"] == ["<outside-sample>"]
    assert "PRIVATE" not in json.dumps(report)


def test_log_probe_ignores_partial_lines_and_reads_only_a_bounded_tail(tmp_path: Path) -> None:
    path = tmp_path / "api.log"
    expected = {"event": "http_request", "request_id": "synthetic-request"}
    path.write_bytes(b"x"*1100000 + b'\n{"unfinished"\n' + json.dumps(expected).encode() + b"\n")
    assert log_event(path, "synthetic-request") == expected


def test_observer_start_failure_does_not_publish_driver_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(**kwargs: Any) -> None:
        raise RuntimeError("PRIVATE-CONNECTION")
    monkeypatch.setattr("scripts.native_database_observer.psycopg.connect", reject)
    sample = observer()
    with pytest.raises(ValueError, match="database_observer_start_failed"):
        sample.start()
    report = sample.close()
    assert not report["passed"] and report["thread_closed"]
    assert "PRIVATE" not in json.dumps(report)
