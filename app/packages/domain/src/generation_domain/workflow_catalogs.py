from __future__ import annotations

import base64
import json
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.workflow_imports import export_path

CATALOG_PAGE_SIZE = 20
CATALOG_MAX_ENTRIES = 2000


def export_directory(value: str) -> str:
    if value == "":
        return value
    if len(value) > 512 or len(value.split("/")) > 8:
        raise DomainError("invalid_export_directory", "导出目录路径过长或层级超过 8 级", 422)
    for part in value.split("/"):
        export_path(part + "/probe.json")
    return value


def catalog_cursor(directory: str, digest: str, after: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"directory": directory, "digest": digest, "after": after}, ensure_ascii=False, separators=(",", ":")).encode()).rstrip(b"=").decode("ascii")


def parse_catalog_cursor(value: str | None, directory: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        if not 1 <= len(value) <= 4096:
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        document = json.loads(raw.decode("utf-8"))
        if not isinstance(document, dict) or set(document) != {"directory", "digest", "after"} or document["directory"] != directory or not isinstance(document["digest"], str) or len(document["digest"]) != 64 or any(c not in "0123456789abcdef" for c in document["digest"]) or not isinstance(document["after"], str) or not 1 <= len(document["after"]) <= 255 or "/" in document["after"]:
            raise ValueError
        export_directory(document["after"])
        if catalog_cursor(directory, document["digest"], document["after"]) != value:
            raise ValueError
        return document
    except (ValueError, UnicodeError, DomainError) as exc:
        raise DomainError("invalid_catalog_cursor", "目录页标识无效，请重新加载目录", 422) from exc
