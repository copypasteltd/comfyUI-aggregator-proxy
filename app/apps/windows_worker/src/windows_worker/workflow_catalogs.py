"""One directory, bounded metadata and file inspection; no recursive scan."""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from typing import Any

from generation_contracts.workflow_catalogs import WorkflowCatalogPage
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, fingerprint
from generation_domain.workflow_catalogs import (
    CATALOG_MAX_ENTRIES,
    CATALOG_PAGE_SIZE,
    catalog_cursor,
    export_directory,
    parse_catalog_cursor,
)
from generation_domain.workflow_imports import export_path

from windows_worker.workflow_exports import export_directory_handle, read_export


def list_exports(root: Path | None, relative: str, cursor: str | None) -> dict[str, Any]:
    if root is None:
        raise DomainError("workflow_export_not_configured", "尚未配置 Workflow 导出目录", 422)
    export_directory(relative)
    continuation = parse_catalog_cursor(cursor, relative)
    started = time.monotonic()

    def budget() -> None:
        if time.monotonic() - started >= 15:
            raise DomainError("workflow_catalog_timeout", "目录检查超过时间预算，请缩小目录后重试", 409)

    metadata: list[dict[str, Any]] = []
    skipped = 0
    with export_directory_handle(root, relative) as handle, os.scandir(handle) as entries:
        for count, entry in enumerate(entries, 1):
            budget()
            if count > CATALOG_MAX_ENTRIES:
                raise DomainError("workflow_catalog_too_large", "单个目录超过 2000 项，请划分子目录", 413)
            path = (relative + "/" if relative else "") + entry.name
            try:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    skipped += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    export_directory(path)
                    kind = "directory"
                elif stat.S_ISREG(info.st_mode) and entry.name.lower().endswith(".json"):
                    export_path(path)
                    kind = "file"
                else:
                    skipped += 1
                    continue
                if len(entry.name) > 255:
                    raise ValueError
                metadata.append({"name": entry.name, "relative_path": path, "kind": kind, "bytes": info.st_size if kind == "file" else None, "mtime_ns": info.st_mtime_ns})
            except (OSError, DomainError, ValueError):
                skipped += 1
        metadata.sort(key=lambda item: item["name"])
        directory_digest = fingerprint({"directory": relative, "entries": metadata, "skipped_count": skipped})
        if continuation and continuation["digest"] != directory_digest:
            raise DomainError("workflow_catalog_changed", "目录发生变化，请重新加载首页", 409)
        remaining = [item for item in metadata if continuation is None or item["name"] > continuation["after"]]
        result: list[dict[str, Any]] = []
        for item in remaining[:CATALOG_PAGE_SIZE]:
            budget()
            row = {k: v for k, v in item.items() if k != "mtime_ns"}
            row.update({"modified_at": max(0, item["mtime_ns"] // 1000000000), "status": "directory" if item["kind"] == "directory" else "invalid"})
            if item["kind"] == "file":
                try:
                    source = read_export(root, item["relative_path"])
                    document = source["source_document"]
                    assert isinstance(document, dict)
                    graph = document["workflow_api"] if source["export_format"] == "manifest-v1" else document
                    task_type = document.get("task_type") if source["export_format"] == "manifest-v1" else None
                    if task_type is not None and task_type not in ("text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"):
                        raise DomainError("invalid_workflow_source", "发布包能力类型无效", 422)
                    row.update({"bytes": source["source_bytes"], "status": "ready", "source_digest": source["source_digest"], "export_format": source["export_format"], "node_count": len(graph), "task_type": task_type})
                except DomainError as exc:
                    row["error_code"] = exc.code
            result.append(row)
        budget()
        more = len(remaining) > CATALOG_PAGE_SIZE
        page = WorkflowCatalogPage.model_validate({"protocol": "local-directory-v1", "relative_directory": relative, "directory_digest": directory_digest, "entries": result, "skipped_count": skipped, "has_more": more, "next_cursor": catalog_cursor(relative, directory_digest, result[-1]["name"]) if more else None}).model_dump()
        if len(canonical_json(page).encode()) > 60 * 1024:
            raise DomainError("workflow_catalog_too_large", "目录回执超过上限", 413)
        return {"catalog": page, "catalog_digest": fingerprint(page)}
