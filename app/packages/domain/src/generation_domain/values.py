from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from generation_domain.errors import DomainError


def utcnow() -> datetime:
    return datetime.now(UTC)


def public_id(prefix: str, value: UUID) -> str:
    return f"{prefix}_{value.hex}"


def parse_id(prefix: str, value: str) -> UUID:
    try:
        if not value.startswith(prefix + "_") or len(value) != len(prefix) + 33:
            raise ValueError
        return UUID(hex=value[len(prefix) + 1 :])
    except ValueError as exc:
        raise DomainError("not_found", "资源不存在", 404) from exc


def canonical_json(value: Any) -> str:
    """Canonical v1: sorted code-point keys, ordered arrays, exact decimal text, UTF-8."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (Decimal, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise DomainError("invalid_number", "数值必须有限", 422)
        number = Decimal(str(value))
        if not number.is_finite():
            raise DomainError("invalid_number", "数值必须有限", 422)
        if number == 0:
            return "0"
        return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return "{" + ",".join(canonical_json(k) + ":" + canonical_json(value[k]) for k in sorted(value)) + "}"
    raise TypeError(f"Unsupported canonical value: {type(value).__name__}")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(("canonical-v1\n" + canonical_json(value)).encode("utf-8")).hexdigest()


def request_fingerprint(request: dict[str, Any], asset_hashes: dict[str, str]) -> str:
    """Called before resolving mutable aliases or allocating execution defaults."""
    def normalize(value: Any) -> Any:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {("asset_sha256" if key == "asset_id" and isinstance(item, str) and item in asset_hashes else key): (asset_hashes[item] if key == "asset_id" and isinstance(item, str) and item in asset_hashes else normalize(item)) for key, item in value.items()}
        return value
    semantic = normalize({k: v for k, v in request.items() if k != "client_request_id"})
    return fingerprint({"schema_revision": "0.4.0", "request": semantic})
