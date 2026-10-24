from __future__ import annotations

from decimal import Decimal

import pytest
from generation_contracts.validation import JobCreate
from generation_domain.errors import DomainError
from generation_domain.state import TERMINAL, transition
from generation_domain.values import fingerprint, request_fingerprint


def test_canonical_preserves_providedness_order_and_unicode() -> None:
    assert fingerprint({"b": Decimal("1.000"), "a": -0.0}) == fingerprint({"a": 0, "b": 1})
    assert fingerprint(["a", "b"]) != fingerprint(["b", "a"])
    assert fingerprint("é") != fingerprint("e\u0301")
    request = {"model": "m", "input": {"prompt": "p"}}
    assert request_fingerprint(request, {}) != request_fingerprint({**request, "parameters": {}}, {})
    assert request_fingerprint(request, {}) == request_fingerprint({**request, "client_request_id": "different"}, {})


def test_transition_requires_evidence_and_terminal_is_immutable() -> None:
    with pytest.raises(DomainError, match="缺少证据"):
        transition("submitting", "running", "submitted", set())
    for state in TERMINAL:
        with pytest.raises(DomainError):
            transition(state, "queued", "claim", {"all"})


def test_native_request_rejects_wrong_reference_semantics() -> None:
    with pytest.raises(ValueError):
        JobCreate.model_validate({"model": "m", "task_type": "references_to_video", "input": {"prompt": "p", "references": [{"asset_id": "asset_reference1", "role": "character"}]}})


def test_request_fingerprint_uses_ordered_content_instead_of_temporary_upload_ids() -> None:
    first = {"model": "image", "task_type": "image_to_image", "input": {"prompt": "test", "source_image": {"asset_id": "asset_a"}}}
    second = {**first, "input": {"prompt": "test", "source_image": {"asset_id": "asset_b"}}}
    assert request_fingerprint(first, {"asset_a": "a" * 64}) == request_fingerprint(second, {"asset_b": "a" * 64})
    assert request_fingerprint(first, {"asset_a": "a" * 64}) != request_fingerprint(second, {"asset_b": "b" * 64})
