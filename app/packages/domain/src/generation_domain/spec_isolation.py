from __future__ import annotations

import json
from typing import Any

from generation_domain.values import canonical_json, fingerprint

GLOBAL_UNFINISHED_JOBS = 500
ISOLATING_FAILURES = frozenset({"comfy_oom", "comfy_output_invalid"})
FAILURE_WINDOW_SECONDS = 600
FAILURE_THRESHOLD = 2


def generation_spec(task_type: str, execution: dict[str, Any]) -> dict[str, Any]:
    """Public resource shape only. Never include prompts, seed or asset identities."""
    parameters = execution["parameters"]
    inputs = execution["input"]
    return {"protocol": "generation-spec-v1", "task_type": task_type,
            "parameters": json.loads(canonical_json({key: parameters[key] for key in ("size", "quality", "n", "seconds", "fps", "strength") if key in parameters})),
            "source_image": "source_image" in inputs, "mask": "mask" in inputs,
            "reference_count": len(inputs.get("references", []))}


def spec_digest(task_type: str, execution: dict[str, Any]) -> str:
    return fingerprint(generation_spec(task_type, execution))
