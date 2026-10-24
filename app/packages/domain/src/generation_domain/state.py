from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from generation_domain.errors import DomainError

_MACHINE: dict[str, Any] = json.loads(files("generation_domain").joinpath("job-state-machine.json").read_text(encoding="utf-8"))
STATES = frozenset(_MACHINE["states"])
TERMINAL = frozenset(_MACHINE["terminal_states"])


def transition(source: str, target: str, trigger: str, facts: set[str]) -> str:
    matches = [t for t in _MACHINE["transitions"] if (t["from"], t["to"], t["trigger"]) == (source, target, trigger)]
    if not matches:
        raise DomainError("invalid_transition", f"不允许从 {source} 执行 {trigger} 到 {target}")
    required = set(matches[0]["guards"])
    if not required <= facts:
        raise DomainError("missing_evidence", "状态变更缺少证据：" + ", ".join(sorted(required - facts)))
    return target


def uncertain(source: str) -> str:
    if source == "reconciling":
        return source
    return transition(source, "reconciling", "cancel_result_unknown" if source == "cancel_requested" else "ownership_or_execution_unknown", {"ownership_uncertain"})
