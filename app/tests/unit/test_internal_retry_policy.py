from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from generation_contracts.retries import RetryInfo
from generation_domain.errors import DomainError
from generation_domain.retries import RETRY_CODE, confirmed_oom_evidence, retry_after
from generation_domain.values import fingerprint, utcnow
from pydantic import BaseModel
from windows_worker.failure_causes import oom_retry_evidence, preparation_failure_code

CASES = [case for case in json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/fixtures.json").read_text(encoding="utf-8"))["cases"] if case["schema"] == "job-retry.schema.json"]


@pytest.mark.parametrize("case", CASES, ids=lambda value: value["id"])
def test_retry_dto_agrees_with_frozen_examples(case: dict) -> None:
    if case["valid"]:
        assert RetryInfo.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            RetryInfo.model_validate(case["data"])


@pytest.mark.parametrize("count", [0, 1, 2])
def test_required_retry_nulls_survive_parent_exclusion_and_keep_openapi_shape(count: int) -> None:
    class Envelope(BaseModel):
        retry: RetryInfo

    expected = {"count": count, "limit": 2, "next_at": None, "last_error": RETRY_CODE if count else None}
    value = Envelope(retry=RetryInfo.model_validate(expected))
    assert value.model_dump(exclude_none=True)["retry"] == expected
    schema = Envelope.model_json_schema(mode="serialization")["$defs"]["RetryInfo"]
    assert set(schema["required"]) == set(expected)
    assert schema["properties"]["count"]["maximum"] == 2


def decision(**changes):
    now = utcnow()
    fields = {"state": "preparing", "count": 0, "error_code": RETRY_CODE, "execution_stopped": True, "evidence_digest": "a" * 64, "has_submission": False, "cancelled": False, "validation": False, "now": now, "deadline": now + timedelta(minutes=5), **changes}
    return fields, retry_after(**fields)


@pytest.mark.parametrize(("count", "delay"), [(0, 5), (1, 15)])
def test_backoff_uses_server_time_and_bounded_count(count: int, delay: int) -> None:
    fields, result = decision(count=count)
    assert result == fields["now"] + timedelta(seconds=delay)


@pytest.mark.parametrize("fields", [{"count": 2}, {"count": -1}, {"state": "running"}, {"state": "submitting"}, {"state": "reconciling"}, {"state": "uploading"}, {"error_code": "preparation_failed"}, {"error_code": "comfy_oom"}, {"execution_stopped": False}, {"evidence_digest": None}, {"evidence_digest": "invalid"}, {"has_submission": True}, {"cancelled": True}, {"validation": True}])
def test_unsafe_or_ineligible_failure_never_schedules(fields: dict) -> None:
    assert decision(**fields)[1] is None


def test_backoff_must_finish_strictly_before_deadline() -> None:
    now = utcnow()
    assert decision(now=now, deadline=now + timedelta(seconds=5))[1] is None
    assert decision(now=now, deadline=now)[1] is None


@pytest.mark.parametrize("exc", [httpx.ConnectError("synthetic"), httpx.ReadError("synthetic"), httpx.WriteError("synthetic"), httpx.ConnectTimeout("synthetic"), httpx.ReadTimeout("synthetic"), httpx.RemoteProtocolError("synthetic")])
def test_explicit_transport_failures_are_classified_only_before_intent(exc: Exception) -> None:
    assert preparation_failure_code(exc) == RETRY_CODE


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_temporary_status_allowlist(status: int) -> None:
    response = httpx.Response(status, request=httpx.Request("GET", "https://synthetic.invalid"))
    assert preparation_failure_code(httpx.HTTPStatusError("synthetic", request=response.request, response=response)) == RETRY_CODE


@pytest.mark.parametrize("exc", [httpx.LocalProtocolError("synthetic"), httpx.UnsupportedProtocol("synthetic"), httpx.ProxyError("synthetic"), DomainError("input_hash_mismatch", "synthetic"), ValueError("synthetic"), OSError("synthetic")])
def test_configuration_hash_paths_and_other_failures_are_not_temporary(exc: Exception) -> None:
    assert preparation_failure_code(exc) == "preparation_failed"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 501, 505])
def test_other_http_statuses_are_not_retried(status: int) -> None:
    response = httpx.Response(status, request=httpx.Request("GET", "https://synthetic.invalid"))
    assert preparation_failure_code(httpx.HTTPStatusError("synthetic", request=response.request, response=response)) == "preparation_failed"


@pytest.mark.parametrize(("count", "delay"), [(0, 5), (1, 15)])
def test_proven_oom_uses_shared_backoff_budget(count: int, delay: int) -> None:
    fields, result = decision(state="running", count=count, error_code="comfy_oom", has_submission=True,
                              oom_evidence=True, alternative_gpu=True)
    assert result == fields["now"] + timedelta(seconds=delay)


@pytest.mark.parametrize("change", [
    {"oom_failures": 1}, {"oom_failures": -1}, {"count": 2}, {"oom_evidence": False},
    {"alternative_gpu": False}, {"has_submission": False}, {"state": "submitting"},
    {"state": "collecting"}, {"state": "uploading"}, {"state": "verifying"},
    {"execution_stopped": False}, {"cancelled": True}, {"validation": True},
    {"error_code": "comfy_execution_failed"}, {"error_code": "comfy_output_invalid"},
    {"error_code": "preparation_failed"}, {"error_code": "execution_timeout_unknown"},
    {"error_code": "input_hash_mismatch"}, {"error_code": "invalid_audio"},
    {"error_code": "output_spec_mismatch"}, {"error_code": "storage_unavailable"},
    {"error_code": "unknown_failure"},
])
def test_oom_or_other_failures_cannot_bypass_evidence_or_budget(change: dict) -> None:
    fields = {"state": "running", "error_code": "comfy_oom", "has_submission": True,
              "oom_evidence": True, "alternative_gpu": True, **change}
    assert decision(**fields)[1] is None


def test_reconciled_oom_needs_the_same_current_prompt_proof() -> None:
    assert decision(state="reconciling", error_code="comfy_oom", has_submission=True,
                    oom_evidence=True, alternative_gpu=True)[1] is not None
    assert decision(state="reconciling", error_code="comfy_oom", has_submission=True,
                    oom_evidence=False, alternative_gpu=True)[1] is None


def oom_history():
    return {"status": {"status_str": "error", "messages": [["execution_error", {
        "prompt_id": "synthetic-current", "exception_type": "torch.cuda.OutOfMemoryError",
        "exception_message": "synthetic-private-prompt", "current_inputs": {"private": "synthetic-secret"},
    }]]}}


def test_worker_projects_only_fixed_current_prompt_and_stop_facts() -> None:
    queue = {"queue_running": [], "queue_pending": []}
    proof = oom_retry_evidence(oom_history(), queue, "synthetic-current", "00000000-0000-4000-8000-000000000000")
    assert proof and proof["history_digest"] == fingerprint(oom_history()) and proof["queue_digest"] == fingerprint(queue)
    assert "synthetic-private-prompt" not in json.dumps(proof) and "synthetic-secret" not in json.dumps(proof)
    payload = {"error_code": "comfy_oom", "execution_stop_confirmed": True, "retry_evidence": proof, "evidence_digest": fingerprint(proof)}
    assert confirmed_oom_evidence(payload, prompt_id=proof["prompt_id"], association_id=proof["association_id"])
    assert not confirmed_oom_evidence(payload, prompt_id="another-prompt", association_id=proof["association_id"])
    assert not confirmed_oom_evidence(payload, prompt_id=proof["prompt_id"], association_id=None)
    payload["evidence_digest"] = "0" * 64
    assert not confirmed_oom_evidence(payload, prompt_id=proof["prompt_id"], association_id=proof["association_id"])


def test_worker_never_builds_oom_retry_proof_for_running_or_unrelated_prompt() -> None:
    with pytest.raises(DomainError):
        oom_retry_evidence(oom_history(), {"queue_running": [[1, "synthetic-current"]], "queue_pending": []}, "synthetic-current", "synthetic-association")
    assert oom_retry_evidence(oom_history(), {"queue_running": [], "queue_pending": []}, "another-prompt", "synthetic-association") is None


@pytest.mark.parametrize("change", [{"queue_absent": False}, {"exception_type": []}, {"kind": "unknown"},
                                    {"history_digest": "bad"}, {"queue_digest": None}, {"extra": "private"}])
def test_untrusted_oom_proof_shape_is_closed(change: dict) -> None:
    proof = oom_retry_evidence(oom_history(), {"queue_running": [], "queue_pending": []}, "synthetic-current", "synthetic-association")
    proof.update(change)
    assert not confirmed_oom_evidence({"error_code": "comfy_oom", "execution_stop_confirmed": True,
                                      "retry_evidence": proof, "evidence_digest": fingerprint(proof)},
                                     prompt_id="synthetic-current", association_id="synthetic-association")
