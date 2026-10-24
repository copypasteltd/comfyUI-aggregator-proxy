from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from generation_domain.errors import DomainError
from windows_worker.comfy import ComfyClient, SubmissionUnknown
from windows_worker.journal import Journal


def test_intent_survives_lost_response_and_forbids_resubmit(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    journal.accept_claim({"attempt_id": "att_test", "execution_digest": "a" * 64})
    calls = []

    def network(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert journal.get("att_test")["stage"] == "posting"
        raise httpx.ReadTimeout("Synthetic lost response")

    client = ComfyClient("http://127.0.0.1:8188", transport=httpx.MockTransport(network))
    try:
        with pytest.raises(SubmissionUnknown):
            client.submit(journal, "att_test", {}, "b" * 64)
        association = journal.get("att_test")["association_id"]
        journal.close()
        journal = Journal(tmp_path)
        assert journal.recovery()[0]["association_id"] == association
        with pytest.raises(DomainError):
            client.submit(journal, "att_test", {}, "b" * 64)
        with pytest.raises(DomainError):
            client.submit(journal, "att_test", {}, "b" * 64, association=association)
        assert len(calls) == 1
    finally:
        client.close()
        journal.close()


def test_journal_allows_only_one_writer(tmp_path: Path) -> None:
    first = Journal(tmp_path)
    try:
        with pytest.raises(DomainError, match="已有运行进程"):
            Journal(tmp_path)
    finally:
        first.close()


def test_empty_comfy_history_never_authorizes_resubmit() -> None:
    def network(request: httpx.Request) -> httpx.Response:
        value = {"queue_running": [], "queue_pending": []} if request.url.path == "/queue" else {}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(value).encode()))
    client = ComfyClient("http://127.0.0.1:8188", transport=httpx.MockTransport(network))
    try:
        assert client.reconcile("association", None) == {"observation": "absent", "safe_to_resubmit": False}
    finally:
        client.close()
