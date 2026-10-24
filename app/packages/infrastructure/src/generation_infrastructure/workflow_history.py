from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from typing import Any
from uuid import UUID

from generation_contracts.workflow_history import (
    WorkflowDiff,
    WorkflowDiffQuery,
    WorkflowHistoryPage,
    WorkflowHistoryQuery,
)
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from pydantic import ValidationError
from sqlalchemy import Text, cast, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase
from generation_infrastructure.admin_channels import begin_channel_read
from generation_infrastructure.models import Workflow, WorkflowRelease

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_NODES = 100000
MAX_DEPTH = 64
MISSING = object()
DISPLAY_ESCAPES = {index: f"\\u{index:04x}" for index in [*range(32), *range(127, 160)]}


def comparison_limit() -> DomainError:
    return DomainError("workflow_comparison_limit", "发布包超过可视比较的大小或结构限制，请导出原包核对", 413)


def json_kind(value: Any) -> str | None:
    if value is MISSING:
        return None
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError("Invalid JSON number")
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise ValueError("Invalid JSON type")


def check_structure(document: Any) -> None:
    stack = [(document, 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH:
            raise comparison_limit()
        kind = json_kind(value)
        if kind == "object":
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Invalid JSON key")
            if visited + len(stack) + len(value) > MAX_NODES:
                raise comparison_limit()
            stack.extend((item, depth + 1) for item in value.values())
        elif kind == "array":
            if visited + len(stack) + len(value) > MAX_NODES:
                raise comparison_limit()
            stack.extend((item, depth + 1) for item in value)


def escaped(value: str) -> str:
    return value.translate(DISPLAY_ESCAPES)


def preview(value: Any) -> tuple[str | None, bool]:
    if value is MISSING:
        return None, False
    raw = escaped(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return raw[:2048], len(raw) > 2048


def difference_page(before: Any, after: Any, page: int) -> tuple[dict[str, int], list[dict[str, Any]]]:
    check_structure(before)
    check_structure(after)
    visited = 0

    def walk(a: Any, b: Any, path: str) -> Iterator[tuple[str, str, Any, Any]]:
        nonlocal visited
        visited += 1
        if visited > MAX_NODES:
            raise comparison_limit()
        left, right = json_kind(a), json_kind(b)
        if a is MISSING:
            yield "added", path, a, b
        elif b is MISSING:
            yield "removed", path, a, b
        elif left != right:
            yield "changed", path, a, b
        elif left == "object":
            for key in sorted(a.keys() | b.keys()):
                yield from walk(a.get(key, MISSING), b.get(key, MISSING), path + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif left == "array":
            for index in range(max(len(a), len(b))):
                yield from walk(a[index] if index < len(a) else MISSING, b[index] if index < len(b) else MISSING, path + "/" + str(index))
        elif a != b:
            yield "changed", path, a, b

    counts = {"added": 0, "removed": 0, "changed": 0}
    rows = []
    for index, (kind, path, left, right) in enumerate(walk(before, after, "")):
        counts[kind] += 1
        if (page - 1) * 50 <= index < page * 50:
            display = escaped(path or "/")
            previous, previous_truncated = preview(left)
            current, current_truncated = preview(right)
            rows.append({"path": display[:512], "path_digest": hashlib.sha256(path.encode("utf-8")).hexdigest(), "path_truncated": len(display) > 512, "change": kind, "before_type": json_kind(left), "after_type": json_kind(right), "before_preview": previous, "after_preview": current, "before_truncated": previous_truncated, "after_truncated": current_truncated})
    return counts, rows


def release_columns() -> list[Any]:
    return [WorkflowRelease.id, WorkflowRelease.version, WorkflowRelease.digest, WorkflowRelease.validated, WorkflowRelease.created_at]


def release_reference(raw: Any) -> dict[str, Any]:
    return {"kind": "release", "id": public_id("wfr", raw["id"]), "version": raw["version"], "digest": raw["digest"], "validated": raw["validated"], "created_at": int(raw["created_at"].timestamp())}


class WorkflowHistory(AdminBase):
    def _parent(self, session: Session, identifier: UUID) -> Any:
        row = session.execute(select(Workflow.id, Workflow.project_id, Workflow.revision).where(Workflow.id == identifier)).mappings().one_or_none()
        if row is None:
            raise DomainError("not_found", "Workflow 不存在", 404)
        return row

    def _envelope(self, parent: Any, page: int, total: int) -> dict[str, Any]:
        return {"workflow_id": public_id("wf", parent["id"]), "project_id": public_id("prj", parent["project_id"]), "workflow_revision": parent["revision"], "page": page, "has_more": total > page * 50, "total": total, "snapshot_at": int(utcnow().timestamp())}

    def history(self, identifier: UUID, query: WorkflowHistoryQuery) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                parent = self._parent(session, identifier)
                conditions = (WorkflowRelease.workflow_id == identifier, WorkflowRelease.project_id == parent["project_id"])
                total = session.scalar(select(func.count()).select_from(WorkflowRelease).where(*conditions)) or 0
                rows = session.execute(select(*release_columns()).where(*conditions).order_by(WorkflowRelease.version.desc(), WorkflowRelease.id).offset((query.page - 1) * 50).limit(50)).mappings().all()
                return WorkflowHistoryPage.model_validate({**self._envelope(parent, query.page, total), "data": [release_reference(raw) for raw in rows]}).model_dump()
        except (DBAPIError, ValidationError, TypeError, KeyError, ValueError):
            raise DomainError("workflow_history_unavailable", "Workflow 发布历史暂不可用，请稍后重试", 503) from None

    def _release(self, session: Session, parent: Any, identifier: str) -> Any:
        row = session.execute(select(*release_columns(), func.octet_length(cast(WorkflowRelease.manifest, Text)).label("document_bytes")).where(WorkflowRelease.id == parse_id("wfr", identifier), WorkflowRelease.workflow_id == parent["id"], WorkflowRelease.project_id == parent["project_id"])).mappings().one_or_none()
        if row is None:
            raise DomainError("not_found", "同 Workflow 发布版本不存在", 404)
        if row["document_bytes"] > MAX_DOCUMENT_BYTES:
            raise comparison_limit()
        return row

    def compare(self, identifier: UUID, query: WorkflowDiffQuery) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                parent = self._parent(session, identifier)
                base = self._release(session, parent, query.base_release_id)
                target = self._release(session, parent, query.target_release_id) if query.target_release_id else None
                if target is None:
                    size = session.scalar(select(func.octet_length(cast(Workflow.draft, Text))).where(Workflow.id == identifier))
                    if size is None:
                        raise ValueError("Missing stored document")
                    if size > MAX_DOCUMENT_BYTES:
                        raise comparison_limit()
                before = session.scalar(select(WorkflowRelease.manifest).where(WorkflowRelease.id == base["id"]))
                after = session.scalar(select(WorkflowRelease.manifest).where(WorkflowRelease.id == target["id"])) if target is not None else session.scalar(select(Workflow.draft).where(Workflow.id == identifier))
                counts, rows = difference_page(before, after, query.page)
                result = {**self._envelope(parent, query.page, sum(counts.values())), "base": release_reference(base), "target": release_reference(target) if target is not None else {"kind": "draft", "revision": parent["revision"]}, "counts": counts, "data": rows}
                return WorkflowDiff.model_validate(result).model_dump()
        except RecursionError:
            raise comparison_limit() from None
        except (DBAPIError, ValidationError, TypeError, KeyError, ValueError):
            raise DomainError("workflow_history_unavailable", "Workflow 版本比较暂不可用，请稍后重试", 503) from None
