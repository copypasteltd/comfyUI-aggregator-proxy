from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.model_files import (
    ModelFileReadRequest,
    ModelFileRegistration,
    ModelInspectionReceipt,
)
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision
from generation_infrastructure.admin_channels import enqueue_command, lock_read_channel
from generation_infrastructure.admin_model_artifacts import artifact_projection
from generation_infrastructure.model_inventory import current_catalog, require_model_source
from generation_infrastructure.models import (
    Channel,
    ManagementOperation,
    ModelArtifact,
    Node,
    WorkerCommand,
)
from generation_infrastructure.transactions import record_change


def owned_read(session: Session, actor: AdminIdentity, identifier: UUID) -> WorkerCommand:
    query = select(WorkerCommand).join(ManagementOperation, ManagementOperation.id == WorkerCommand.operation_id).where(WorkerCommand.operation_id == identifier, ManagementOperation.command == "inspect_model_file", WorkerCommand.command == "inspect_model_file")
    if actor.role != "administrator":
        query = query.where(ManagementOperation.actor_id == actor.user_id)
    rows = list(session.scalars(query.limit(2)))
    if len(rows) != 1:
        raise DomainError("not_found", "模型检查操作不存在或未授权", 404)
    return rows[0]


class ModelFileAdministration(AdminBase):
    def create(self, actor: AdminIdentity, data: ModelFileReadRequest, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "model_file.read", token, data.model_dump())
            if idem.result is not None:
                return idem.result
            channel, node = lock_read_channel(session, parse_id("ch", data.channel_id))
            require_revision(channel.desired_revision, data.expected_revision)
            catalog = require_model_source(channel, node, data.catalog_digest, utcnow())
            entry = next((item for item in catalog.entries if item.relative_path == data.relative_path), None)
            if entry is None:
                raise DomainError("model_file_not_listed", "所选模型未出现在当前目录中", 409)
            pending = session.scalar(select(WorkerCommand.id).where(WorkerCommand.channel_id == channel.id, WorkerCommand.command == "inspect_model_file", WorkerCommand.state.in_(("pending", "running"))).limit(1))
            if pending:
                raise DomainError("model_file_read_pending", "该渠道已有模型检查，请先查看结果", 409)
            payload = {"revision": channel.desired_revision, "catalog_boot_id": str(node.current_boot_id), "catalog_digest": data.catalog_digest, **entry.model_dump()}
            idem.result = enqueue_command(session, actor, channel, "inspect_model_file", payload, request_id)
            return idem.result

    def detail(self, actor: AdminIdentity, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            command = owned_read(session, actor, identifier)
            channel = session.get(Channel, command.channel_id)
            if channel is None or channel.deleted_at is not None:
                raise DomainError("not_found", "来源渠道不存在", 404)
            node = session.get(Node, channel.node_id)
            catalog, stale, _ = current_catalog(channel, node, utcnow())
            stale = stale or catalog is None or catalog.catalog_digest != command.payload["catalog_digest"] or str(node.current_boot_id if node else None) != command.payload["catalog_boot_id"] or channel.desired_revision != command.payload["revision"]
            receipt = ModelInspectionReceipt.model_validate(command.receipt) if command.state == "succeeded" else None
            observed = (command.receipt or {}).get("error_code")
            at = int(command.completed_at.timestamp()) if command.completed_at else None
            stale = stale or (command.state == "succeeded" and at is None) or (at is not None and not 0 <= utcnow().timestamp() - at < 300)
            matches = conflicts = 0
            if receipt:
                same = (ModelArtifact.tenant_id == channel.tenant_id, ModelArtifact.project_id == channel.project_id, ModelArtifact.deleted_at.is_(None), func.lower(ModelArtifact.relative_path) == receipt.inspection.relative_path.lower())
                matches = session.scalar(select(func.count()).select_from(ModelArtifact).where(*same, ModelArtifact.sha256 == receipt.inspection.sha256, ModelArtifact.bytes == receipt.inspection.bytes)) or 0
                total = session.scalar(select(func.count()).select_from(ModelArtifact).where(*same)) or 0
                conflicts = total - matches
            return {"id": public_id("op", identifier), "channel_id": public_id("ch", channel.id), "status": command.state, "stale": bool(stale), "observed_at": at, "error_code": observed, "inspection": receipt.inspection.model_dump() if receipt else None, "matching_registrations": matches, "conflicting_registrations": conflicts}

    def register(self, actor: AdminIdentity, identifier: UUID, data: ModelFileRegistration, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "model_file.register:" + str(identifier), token, data.model_dump())
            if idem.result is not None:
                return idem.result
            command = owned_read(session, actor, identifier)
            channel, node = lock_read_channel(session, command.channel_id)
            if command.state != "succeeded" or command.completed_at is None or not 0 <= (utcnow() - command.completed_at).total_seconds() < 300:
                raise DomainError("model_inspection_unavailable", "模型检查尚未成功或已过期，请重新检查", 409)
            require_revision(channel.desired_revision, command.payload["revision"])
            require_model_source(channel, node, command.payload["catalog_digest"], utcnow())
            if str(node.current_boot_id) != command.payload["catalog_boot_id"]:
                raise DomainError("model_source_stale", "设备启动标识已变化，请重新扫描", 409)
            proof = ModelInspectionReceipt.model_validate(command.receipt).inspection
            values = {"tenant_id": channel.tenant_id, "project_id": channel.project_id, "name": data.name, "kind": data.kind, "sha256": proof.sha256, "bytes": proof.bytes, "relative_path": proof.relative_path}
            inserted = session.scalar(insert(ModelArtifact).values(**values).on_conflict_do_nothing(index_elements=[ModelArtifact.tenant_id, ModelArtifact.project_id, func.lower(ModelArtifact.relative_path), ModelArtifact.sha256], index_where=ModelArtifact.deleted_at.is_(None)).returning(ModelArtifact.id))
            row = session.get(ModelArtifact, inserted) if inserted else session.scalar(select(ModelArtifact).where(ModelArtifact.tenant_id == channel.tenant_id, ModelArtifact.project_id == channel.project_id, func.lower(ModelArtifact.relative_path) == proof.relative_path.lower(), ModelArtifact.sha256 == proof.sha256, ModelArtifact.deleted_at.is_(None)))
            assert row is not None
            if row.bytes != proof.bytes:
                raise DomainError("model_artifact_metadata_conflict", "同哈希登记的大小不一致，须先核对原登记", 409)
            record_change(session, public_id("usr", actor.user_id), "model_file.registered" if inserted else "model_file.registration_reused", row.id, request_id, {"operation_id": public_id("op", identifier), "channel_id": public_id("ch", channel.id)})
            idem.result = artifact_projection(session, row)
            return idem.result
