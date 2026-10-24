from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from windows_worker.journal import Journal
from windows_worker.runtime import Worker

CID, DIGEST = "cmd_" + "1" * 32, "d" * 64


def worker(journal: Journal, post: Any) -> Worker:
    value = object.__new__(Worker)
    value.journal, value.boot, value.post = journal, str(uuid4()), post
    return value


def test_command_cursor_and_durable_unknown_survive_reopen(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    journal.command_intent(CID, DIGEST)
    second = "cmd_" + "2" * 32
    journal.command_intent(second, DIGEST)
    journal.command_result(second, {"success": False, "error_code": "synthetic"})
    journal.close()
    with_journal = Journal(tmp_path)
    try:
        assert with_journal.pending_command()["result"] is None
        assert with_journal.pending_command(CID)["id"] == second
        assert with_journal.pending_command(second)["id"] == CID
        with_journal.command_ack(CID)
        assert with_journal.pending_command(second)["id"] == second
    finally:
        with_journal.close()


@pytest.mark.parametrize("error_code", ["command_expired", "stale_boot"])
async def test_pending_finished_result_precedes_claim_and_ack_requires_reconciliation(
    tmp_path: Path, error_code: str
) -> None:
    journal = Journal(tmp_path)
    result = {"success": True, "applied_revision": 1}
    journal.command_intent(CID, DIGEST)
    journal.command_result(CID, result)
    calls = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append(path)
        assert journal.command_record(CID)["cloud_ack"] == 0
        if path.endswith("receipts"):
            response = httpx.Response(
                409,
                json={"error": {"code": error_code}},
                request=httpx.Request("POST", "https://synthetic.test" + path),
            )
            response.raise_for_status()
        assert body["result"] == result and body["observation"] == "completed"
        return {
            "protocol": "command-reconciliation-v1",
            "command_id": CID,
            "acknowledged": True,
            "resolution": "result_requires_revalidation",
            "command_status": "failed",
            "server_time": 1,
        }

    try:
        await worker(journal, post).management_step()
        assert calls == [
            f"/internal/v1/commands/{CID}/receipts",
            f"/internal/v1/commands/{CID}/reconciliations",
        ]
        assert journal.command_record(CID)["cloud_ack"] == 1
    finally:
        journal.close()


@pytest.mark.parametrize("local_intent", [True, False])
async def test_unknown_intent_never_executes_or_releases(
    tmp_path: Path, local_intent: bool
) -> None:
    journal = Journal(tmp_path)
    if local_intent:
        journal.command_intent(CID, DIGEST)
    calls = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append(path)
        if path.endswith("claims"):
            return {
                "command": None,
                "reconciliation": {
                    "command_id": CID,
                    "command_digest": DIGEST,
                    "reason": "previous_boot",
                },
            }
        assert body["observation"] == "unknown" and body["result"] is None
        return {
            "protocol": "command-reconciliation-v1",
            "command_id": CID,
            "acknowledged": False,
            "resolution": "evidence_required",
            "command_status": "running",
            "server_time": 1,
        }

    try:
        value = worker(journal, post)
        await value.management_step()
        await value.management_step()
        assert all(not path.endswith("receipts") for path in calls)
        assert journal.command_record(CID) is None or not journal.command_record(CID)["cloud_ack"]
    finally:
        journal.close()


async def test_actual_management_loop_executes_once_when_receipt_reply_is_lost(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path)
    executed, receipts = 0, 0
    done = asyncio.Event()
    command = {"command_id": CID, "command_digest": DIGEST}
    value: Worker

    class Executor:
        def execute(self, _: dict[str, Any]) -> dict[str, Any]:
            nonlocal executed
            assert journal.command_record(CID)["result"] is None
            executed += 1
            return {"success": True, "applied_revision": 1}

    async def blocking(function: Any, *args: Any) -> Any:
        return function(*args)

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        nonlocal receipts
        if path.endswith("claims"):
            return {"command": command}
        receipts += 1
        assert journal.command_record(CID)["result"] == body["result"]
        if receipts == 1:
            raise httpx.ReadError("synthetic lost receipt acknowledgement")
        done.set()
        return {"accepted": True, "status": "succeeded"}

    value = worker(journal, post)
    value.commands, value.blocking = Executor(), blocking
    task = asyncio.create_task(value.management())
    try:
        await asyncio.wait_for(done.wait(), 5)
        assert executed == 1 and receipts == 2
        assert journal.command_record(CID)["cloud_ack"] == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        journal.close()


@pytest.mark.parametrize("failure", ["network", "unsupported", "conflict", "wrong_ack"])
async def test_failed_delivery_keeps_durable_result(tmp_path: Path, failure: str) -> None:
    journal = Journal(tmp_path)
    journal.command_intent(CID, DIGEST)
    journal.command_result(CID, {"success": False, "error_code": "synthetic"})

    async def post(path: str, _: dict[str, Any]) -> dict[str, Any]:
        if failure == "network":
            raise httpx.ConnectError("synthetic unavailable")
        if failure == "wrong_ack":
            return {"accepted": False, "status": "running"}
        code = 404 if failure == "unsupported" else 409
        response = httpx.Response(
            code,
            json={"error": {"code": "receipt_conflict"}},
            request=httpx.Request("POST", "https://synthetic.test" + path),
        )
        response.raise_for_status()
        return {}

    try:
        with pytest.raises((httpx.HTTPError, ValueError)):
            await worker(journal, post).management_step()
        assert not journal.command_record(CID)["cloud_ack"]
    finally:
        journal.close()
