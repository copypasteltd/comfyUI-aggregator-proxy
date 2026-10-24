from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.model_artifacts import ModelArtifactCreate, ModelArtifactPatch
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from generation_domain.workflows import relative_file
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import (
    AdminBase,
    mutation,
    require_revision,
    scope_ids,
    timestamp,
)
from generation_infrastructure.models import ModelArtifact, WorkflowRelease
from generation_infrastructure.transactions import record_change


def artifact_path(value: str) -> str:
    relative_file(value)
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
    if any(ord(c) < 32 or c in '<>"|?*' for c in value) or any(part.rstrip(" .") != part or part.split(".", 1)[0].upper() in reserved for part in value.split("/")):
        raise DomainError("unsafe_path", "路径包含 Windows 不允许的文件名", 422)
    return value


def references(session: Session, row: ModelArtifact) -> int:
    if not row.sha256:
        return 0
    return session.scalar(select(func.count()).select_from(WorkflowRelease).where(WorkflowRelease.tenant_id == row.tenant_id, WorkflowRelease.project_id == row.project_id, WorkflowRelease.manifest.contains({"dependency_lock": {"models": [{"sha256": row.sha256}]}}))) or 0


def artifact_projection(session: Session, row: ModelArtifact) -> dict[str, Any]:
    return {"id": public_id("ma", row.id), "tenant_id": public_id("ten", row.tenant_id), "project_id": public_id("prj", row.project_id), "name": row.name, "kind": row.kind, "sha256": row.sha256, "bytes": row.bytes, "relative_path": row.relative_path, "enabled": row.enabled, "revision": row.revision, "created_at": timestamp(row.created_at), "deleted_at": timestamp(row.deleted_at), "referenced_release_count": references(session, row)}


class ModelArtifactAdministration(AdminBase):
    def list(self, page: int = 1, include_archived: bool = False) -> dict[str, Any]:
        with self.sessions() as session:
            query = select(ModelArtifact)
            if not include_archived:
                query = query.where(ModelArtifact.deleted_at.is_(None))
            rows = list(session.scalars(query.order_by(ModelArtifact.created_at.desc(), ModelArtifact.id).offset((page - 1) * 50).limit(51)))
            return {"data": [artifact_projection(session, row) for row in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def create(self, actor: AdminIdentity, data: ModelArtifactCreate, token: str, request_id: str) -> dict[str, Any]:
        path = artifact_path(data.relative_path)
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "model_artifact.create", token, data.model_dump(mode="json"))
            if idem.result is not None:
                return idem.result
            tenant, project = scope_ids(session, data.tenant_id, data.project_id)
            # The unique index arbitrates distinct concurrent create requests.
            row = ModelArtifact(tenant_id=tenant, project_id=project, name=data.name, kind=data.kind, sha256=data.sha256, bytes=data.bytes, relative_path=path)
            session.add(row)
            session.flush()
            record_change(session, public_id("usr", actor.user_id), "model_artifact.created", row.id, request_id, revision_before=None, revision_after=row.revision)
            idem.result = artifact_projection(session, row)
            return idem.result

    def patch(self, actor: AdminIdentity, identifier: UUID, data: ModelArtifactPatch, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "model_artifact.patch:" + str(identifier), token, data.model_dump(mode="json"))
            if idem.result is not None:
                return idem.result
            row = session.scalar(select(ModelArtifact).where(ModelArtifact.id == identifier, ModelArtifact.deleted_at.is_(None)).with_for_update())
            if not row:
                raise DomainError("not_found", "模型文件登记不存在", 404)
            require_revision(row.revision, data.expected_revision)
            if data.name is not None:
                row.name = data.name
            if data.enabled is not None:
                row.enabled = data.enabled
            row.revision += 1
            record_change(session, public_id("usr", actor.user_id), "model_artifact.updated", row.id, request_id, {"revision": row.revision, "enabled": row.enabled}, revision_before=data.expected_revision, revision_after=row.revision)
            idem.result = artifact_projection(session, row)
            return idem.result

    def delete(self, actor: AdminIdentity, identifier: UUID, revision: int, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "model_artifact.delete:" + str(identifier), token, {"expected_revision": revision})
            if idem.result is not None:
                return idem.result
            row = session.scalar(select(ModelArtifact).where(ModelArtifact.id == identifier, ModelArtifact.deleted_at.is_(None)).with_for_update())
            if not row:
                raise DomainError("not_found", "模型文件登记不存在", 404)
            require_revision(row.revision, revision)
            if references(session, row):
                raise DomainError("model_artifact_referenced", "冻结 Workflow 仍引用该文件哈希，请保留登记或先停用", 409)
            row.deleted_at, row.enabled = utcnow(), False
            row.revision += 1
            record_change(session, public_id("usr", actor.user_id), "model_artifact.archived", row.id, request_id, revision_before=revision, revision_after=row.revision)
            idem.result = {"id": public_id("ma", row.id), "status": "archived"}
            return idem.result
