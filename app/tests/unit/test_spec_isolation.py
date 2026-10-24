from __future__ import annotations

from typing import Any

import httpx
import pytest
from generation_domain.errors import DomainError
from generation_domain.spec_isolation import generation_spec, spec_digest
from windows_worker.failure_causes import (
    execution_failure_code,
    rejected_media,
    require_prompt_absent,
)


def execution(**parameters: Any) -> dict[str, Any]:
    return {
        "parameters": {"size": "1024x1024", "quality": "standard", "n": 1, **parameters},
        "input": {
            "prompt": "synthetic-private-prompt",
            "negative_prompt": "synthetic-private-negative",
        },
        "asset_hashes": {"synthetic": "a" * 64},
    }


def test_spec_excludes_private_text_seed_and_asset_identity_but_normalizes_numbers() -> None:
    first = execution(seed=1, seconds=5.0)
    second = execution(seed=2, seconds=5)
    second["input"]["prompt"] = "another private prompt"
    second["asset_hashes"] = {"different": "b" * 64}
    assert spec_digest("text_to_image", first) == spec_digest("text_to_image", second)
    assert generation_spec("text_to_image", first)["parameters"]["seconds"] == 5
    assert "private" not in str(generation_spec("text_to_image", first))
    assert "seed" not in str(generation_spec("text_to_image", first))


@pytest.mark.parametrize(
    "change",
    ["size", "quality", "seconds", "fps", "strength", "source", "mask", "references", "task"],
)
def test_resource_shape_changes_spec(change: str) -> None:
    baseline, other = execution(), execution()
    task = "text_to_image"
    if change in {"size", "quality", "seconds", "fps", "strength"}:
        other["parameters"][change] = {
            "size": "512x512",
            "quality": "high",
            "seconds": 5,
            "fps": 24,
            "strength": 0.5,
        }[change]
    elif change == "task":
        task = "text_to_video"
    else:
        other["input"][
            {"source": "source_image", "mask": "mask", "references": "references"}[change]
        ] = [{"asset_id": "secret"}] if change == "references" else {"asset_id": "secret"}
    assert spec_digest(task, other) != spec_digest("text_to_image", baseline)


@pytest.mark.parametrize(
    "exception_type, prompt, expected",
    [
        ("torch.OutOfMemoryError", "current", "comfy_oom"),
        ("torch.cuda.OutOfMemoryError", "current", "comfy_oom"),
        ("RuntimeError", "current", "comfy_execution_failed"),
        ("builtins.MemoryError", "current", "comfy_execution_failed"),
        ("torch.OutOfMemoryError", "other", "comfy_execution_failed"),
    ],
)
def test_oom_uses_only_current_prompt_and_allowlisted_type(
    exception_type: str, prompt: str, expected: str
) -> None:
    history = {
        "status": {
            "messages": [
                [
                    "execution_error",
                    {
                        "prompt_id": prompt,
                        "exception_type": exception_type,
                        "exception_message": "torch.OutOfMemoryError CUDA out of memory synthetic-private-prompt",
                        "current_inputs": {"private": "prompt"},
                    },
                ]
            ]
        }
    }
    assert execution_failure_code(history, "current") == expected


@pytest.mark.parametrize(
    "messages",
    [
        None,
        {},
        ["torch.OutOfMemoryError"],
        [["execution_error", "torch.OutOfMemoryError"]],
        [
            [
                "execution_error",
                {"prompt_id": "current", "exception_type": ["torch.OutOfMemoryError"]},
            ]
        ],
        [
            [
                "execution_interrupted",
                {"prompt_id": "current", "exception_type": "torch.OutOfMemoryError"},
            ]
        ],
    ],
)
def test_malformed_or_interrupted_history_is_never_guessed_as_oom(messages: Any) -> None:
    assert (
        execution_failure_code({"status": {"messages": messages}}, "current")
        == "comfy_execution_failed"
    )


@pytest.mark.parametrize(
    "queue",
    [
        {"queue_running": [[1, "current"]], "queue_pending": []},
        {"queue_running": [], "queue_pending": [[1, "current"]]},
        {"queue_running": [[1]], "queue_pending": []},
        {"queue_running": [[1, {"invalid": "prompt"}]], "queue_pending": []},
    ],
)
def test_nonempty_current_or_malformed_queue_cannot_prove_stop(queue: dict[str, Any]) -> None:
    with pytest.raises(DomainError):
        require_prompt_absent(queue, "current")
    require_prompt_absent({"queue_running": [[1, "other"]], "queue_pending": []}, "current")


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (422, "invalid_image", True),
        (422, "unsupported_image", True),
        (422, "invalid_video", True),
        (422, "media_type_mismatch", True),
        (422, "output_spec_mismatch", True),
        (422, "output_digest_mismatch", False),
        (503, "media_probe_unavailable", False),
        (503, "invalid_image", False),
        (401, "invalid_image", False),
    ],
)
def test_upload_failure_classification_is_a_closed_media_allowlist(
    status: int, code: str, expected: bool
) -> None:
    response = httpx.Response(
        status,
        request=httpx.Request("PUT", "https://control.invalid/internal/v1/uploads/synthetic"),
        json={"error": {"code": code, "message": "synthetic private details"}},
    )
    assert (
        rejected_media(
            httpx.HTTPStatusError("synthetic", request=response.request, response=response)
        )
        is expected
    )
