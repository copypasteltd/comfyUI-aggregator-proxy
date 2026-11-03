"""Write scalar request values into an operator-frozen JSON document."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from generation_domain.errors import DomainError


def _invalid() -> DomainError:
    return DomainError("invalid_json_binding", "JSON 绑定必须指向冻结文档内已存在的标量字段", 422)


def _pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in entries:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise ValueError("finite_json_required")


def _number(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("finite_json_required")
    return value


def document_target(raw: Any, pointer: Any) -> tuple[Any, str | int, Any]:
    if (
        not isinstance(raw, str)
        or len(raw.encode("utf-8")) > 1024 * 1024
        or not isinstance(pointer, str)
        or not pointer.startswith("/")
        or not 1 < len(pointer) <= 512
    ):
        raise _invalid()
    tokens = pointer[1:].split("/")
    if len(tokens) > 16 or any(not token or re.search(r"~(?![01])", token) for token in tokens):
        raise _invalid()
    try:
        root = json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_number
        )
        current = root
        key: str | int = ""
        for index, token in enumerate(tokens):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                if not re.fullmatch(r"0|[1-9][0-9]{0,5}", token):
                    raise ValueError("invalid_index")
                key = int(token)
            elif isinstance(current, dict):
                key = token
            else:
                raise ValueError("container_required")
            value = current[key]
            if index == len(tokens) - 1:
                if isinstance(value, (dict, list)):
                    raise ValueError("scalar_required")
                return root, key, current
            current = value
    except (ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
        raise _invalid() from error
    raise _invalid()


def replace_scalar(raw: str, pointer: str, value: Any) -> str:
    if type(value) not in (str, int, float, bool, type(None)):
        raise _invalid()
    root, key, container = document_target(raw, pointer)
    container[key] = value
    try:
        return json.dumps(root, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise _invalid() from error
