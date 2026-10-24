from __future__ import annotations

from datetime import datetime
from typing import Any

from generation_contracts.model_files import ModelFileCatalog
from generation_domain.errors import DomainError
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generation_infrastructure.models import Channel, ModelArtifact, Node


def current_catalog(channel: Channel, node: Node | None, now: datetime) -> tuple[ModelFileCatalog | None, bool, int | None]:
    value = channel.observation.get("model_catalog")
    if not isinstance(value, dict):
        return None, True, None
    at = value.get("observed_at")
    try:
        catalog = ModelFileCatalog.model_validate(value.get("catalog"))
    except (ValidationError, ValueError, DomainError):
        return None, True, None
    stale = (type(at) is not int or not 0 <= now.timestamp() - at < 300 or node is None or not node.enabled
             or value.get("boot_id") != str(node.current_boot_id) or value.get("revision") != channel.desired_revision
             or channel.applied_revision != channel.desired_revision or channel.deleted_at is not None)
    return catalog, stale, at if type(at) is int else None


def require_model_source(channel: Channel, node: Node, digest: str, now: datetime) -> ModelFileCatalog:
    catalog, stale, _ = current_catalog(channel, node, now)
    if stale or catalog is None or catalog.status == "unavailable" or catalog.catalog_digest != digest:
        raise DomainError("model_source_stale", "模型目录已过期或来源已变化，请重新扫描", 409)
    return catalog


def model_files_view(session: Session, channel: Channel, node: Node | None, now: datetime, page: int) -> dict[str, Any]:
    catalog, stale, at = current_catalog(channel, node, now)
    if catalog is None:
        legacy = channel.observation.get("inventory_observed_at") is not None
        return {"scope_root": "models", "status": "unsupported" if legacy else "unknown", "stale": True, "observed_at": None, "catalog_digest": None, "reason": None, "page": page, "total": 0, "has_more": False, "scanned_entries": 0, "skipped_entries": 0, "data": []}
    entries = catalog.entries[(page - 1) * 50:page * 50]
    paths = [item.relative_path.lower() for item in entries]
    # At most fifty groups; no unbounded per-path list of archived model versions.
    rows: Any = session.execute(select(func.lower(ModelArtifact.relative_path).label("path"), func.count().label("registered_total"), func.min(ModelArtifact.bytes).label("minimum"), func.max(ModelArtifact.bytes).label("maximum"), func.count(ModelArtifact.bytes).label("known")).where(ModelArtifact.tenant_id == channel.tenant_id, ModelArtifact.project_id == channel.project_id, ModelArtifact.deleted_at.is_(None), func.lower(ModelArtifact.relative_path).in_(paths)).group_by(func.lower(ModelArtifact.relative_path))) if paths else []
    registered = {row.path: row for row in rows}
    result = []
    for item in entries:
        row = registered.get(item.relative_path.lower())
        status = "unregistered" if row is None else "size_mismatch" if row.known == row.registered_total and row.minimum == row.maximum and row.minimum != item.bytes else "unverified"
        result.append({**item.model_dump(), "registered_count": row.registered_total if row else 0, "registration_status": status})
    return {"scope_root": "models", "status": catalog.status, "stale": stale, "observed_at": at, "catalog_digest": catalog.catalog_digest, "reason": catalog.reason, "page": page, "total": len(catalog.entries), "has_more": page * 50 < len(catalog.entries), "scanned_entries": catalog.scanned_entries, "skipped_entries": catalog.skipped_entries, "data": result}
