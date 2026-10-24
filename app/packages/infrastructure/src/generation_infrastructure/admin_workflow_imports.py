from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.workflow_imports import (
    NewImportTarget,
    WorkflowPull,
)
from generation_contracts.workflow_imports import (
    WorkflowImportPreview as PreviewRequest,
)
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_domain.workflow_imports import bounded_document, checked_source_receipt, draft_diff
from generation_domain.workflows import validate_manifest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision, timestamp
from generation_infrastructure.admin_channels import enqueue_command, lock_read_channel
from generation_infrastructure.admin_workflows import workflow_projection
from generation_infrastructure.models import (
    AdminUser,
    Channel,
    ManagementOperation,
    WorkerCommand,
    Workflow,
    WorkflowImportPreview,
)
from generation_infrastructure.transactions import record_change


def preview_view(row: WorkflowImportPreview, operation_id: UUID) -> dict[str, Any]:
    return {"id": public_id("wip", row.id), "source_operation_id": public_id("op", operation_id), "channel_id": public_id("ch", row.channel_id), "source_digest": row.source_digest, "export_format": row.export_format, "candidate_digest": row.candidate_digest, "target": row.target, "diff": row.diff, "validation": row.validation, "expires_at": timestamp(row.expires_at), "status": "committed" if row.result else "expired" if row.expires_at <= utcnow() else "pending", "result": row.result}


def _channel(session: Session, identifier: UUID, revision: int | None = None) -> Channel:
    row = session.scalar(select(Channel).where(Channel.id == identifier).with_for_update(read=True))
    if row is None or row.deleted_at is not None:
        raise DomainError("not_found", "来源渠道不存在", 404)
    if revision is not None and row.desired_revision != revision:
        raise DomainError("workflow_source_stale", "来源渠道配置已变化，请重新拉取", 409)
    return row


def _target(session: Session, identifier: UUID, channel: Channel, revision: int, *, lock: bool = False) -> Workflow:
    query = select(Workflow).where(Workflow.id == identifier, Workflow.tenant_id == channel.tenant_id, Workflow.project_id == channel.project_id, Workflow.archived.is_(False))
    row = session.scalar(query.with_for_update() if lock else query.with_for_update(read=True))
    if row is None:
        raise DomainError("not_found", "目标 Workflow 不存在或不属于来源项目", 404)
    require_revision(row.revision, revision)
    return row


class WorkflowImportAdministration(AdminBase):
    def pull(self, actor: AdminIdentity, data: WorkflowPull, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.pull", token, data.model_dump())
            if idem.result:
                return idem.result
            channel, _ = lock_read_channel(session, parse_id("ch", data.channel_id))
            require_revision(channel.revision, data.expected_revision)
            pending = session.scalar(select(WorkerCommand.id).where(WorkerCommand.channel_id == channel.id, WorkerCommand.command == "pull_workflow", WorkerCommand.state.in_(["pending", "running"])).limit(1))
            if pending:
                raise DomainError("workflow_pull_pending", "此渠道已有读取操作，请先查看操作结果", 409)
            payload: dict[str, Any] = {"revision": channel.desired_revision, "relative_path": data.relative_path}
            if data.expected_source_digest is not None:
                payload["expected_source_digest"] = data.expected_source_digest
            idem.result = enqueue_command(session, actor, channel, "pull_workflow", payload, request_id)
            return idem.result

    def preview(self, actor: AdminIdentity, data: PreviewRequest, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.import_preview", token, data.model_dump())
            if idem.result:
                # Fetch live status: a replay must not resurrect a consumed preview.
                return self._view(session, actor, parse_id("wip", idem.result["id"]))
            # The preceding idempotency INSERT holds an actor FK KEY SHARE.
            # NO KEY UPDATE serializes limits without a mutual lock upgrade.
            session.execute(select(AdminUser.id).where(AdminUser.id == actor.user_id).with_for_update(key_share=True)).scalar_one()
            expired = session.scalars(select(WorkflowImportPreview).where(WorkflowImportPreview.actor_id == actor.user_id, WorkflowImportPreview.expires_at <= utcnow(), WorkflowImportPreview.result.is_(None), WorkflowImportPreview.candidate.is_not(None)).order_by(WorkflowImportPreview.expires_at, WorkflowImportPreview.id).limit(100).with_for_update(skip_locked=True))
            for old in expired:
                old.candidate = None
            active = session.scalar(select(func.count()).select_from(WorkflowImportPreview).where(WorkflowImportPreview.actor_id == actor.user_id, WorkflowImportPreview.expires_at > utcnow(), WorkflowImportPreview.result.is_(None)))
            if active and active >= 100:
                raise DomainError("workflow_preview_limit", "待确认预览过多，请等待旧预览过期后重试", 429)
            operation_id = parse_id("op", data.source_operation_id)
            commands = list(session.scalars(select(WorkerCommand).join(ManagementOperation, ManagementOperation.id == WorkerCommand.operation_id).where(WorkerCommand.operation_id == operation_id, ManagementOperation.actor_id == actor.user_id, ManagementOperation.command == "pull_workflow", ManagementOperation.state == "succeeded").limit(2)))
            if len(commands) != 1 or commands[0].command != "pull_workflow" or commands[0].payload.get("mode", "file") != "file" or commands[0].state != "succeeded" or not commands[0].receipt:
                raise DomainError("workflow_source_unavailable", "请选择本人已完成的 Workflow 读取操作", 404)
            command = commands[0]
            assert command.receipt
            document = checked_source_receipt(command.receipt)
            if command.receipt["source_digest"] != data.source_digest:
                raise DomainError("workflow_source_mismatch", "来源摘要已变化，请重新读取预览", 409)
            channel = _channel(session, command.channel_id, command.payload["revision"])
            before: dict[str, Any] = {}
            workflow: Workflow | None = None
            task_type: str
            if isinstance(data.target, NewImportTarget):
                task_type = data.target.task_type
            else:
                workflow = _target(session, parse_id("wf", data.target.workflow_id), channel, data.target.expected_revision)
                task_type, before = workflow.task_type, workflow.draft
            if command.receipt["export_format"] == "manifest-v1":
                if document.get("task_type") != task_type:
                    raise DomainError("workflow_task_type_mismatch", "来源发布包与目标能力类型不一致", 422)
                candidate = document
            else:
                candidate = {**before, "workflow_api": document}
                candidate.setdefault("format_version", "1.0")
                candidate.setdefault("task_type", task_type)
            bounded_document(candidate)
            try:
                WorkflowManifest.model_validate(candidate)
                validate_manifest(candidate, task_type)
                validation = {"publishable": True, "code": "structure_valid", "message": "发布包结构通过；运行环境与生成质量仍需单独验证"}
            except (ValidationError, DomainError, ValueError, KeyError):
                validation = {"publishable": False, "code": "draft_incomplete", "message": "可导入草稿；冻结前须补全或修正绑定、输出、依赖与运行画像"}
            row = WorkflowImportPreview(actor_id=actor.user_id, command_id=command.id, channel_id=channel.id, source_revision=channel.desired_revision, workflow_id=workflow.id if workflow else None, expected_revision=workflow.revision if workflow else None, target=data.target.model_dump(), candidate=candidate, candidate_digest=fingerprint(candidate), source_digest=data.source_digest, export_format=command.receipt["export_format"], diff=draft_diff(before, candidate), validation=validation, expires_at=utcnow() + timedelta(minutes=30))
            session.add(row)
            session.flush()
            record_change(session, public_id("usr", actor.user_id), "workflow.import_preview", row.id, request_id)
            idem.result = {"id": public_id("wip", row.id)}
            return preview_view(row, operation_id)

    def _view(self, session: Session, actor: AdminIdentity, identifier: UUID) -> dict[str, Any]:
        row = session.scalar(select(WorkflowImportPreview).where(WorkflowImportPreview.id == identifier, WorkflowImportPreview.actor_id == actor.user_id))
        if row is None:
            raise DomainError("not_found", "预览不存在或未授权", 404)
        command = session.get(WorkerCommand, row.command_id)
        assert command
        return preview_view(row, command.operation_id)

    def get_preview(self, actor: AdminIdentity, identifier: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            return self._view(session, actor, identifier)

    def commit(self, actor: AdminIdentity, identifier: UUID, digest: str, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.import_commit", token, {"preview_id": public_id("wip", identifier), "candidate_digest": digest})
            if idem.result:
                return idem.result
            row = session.scalar(select(WorkflowImportPreview).where(WorkflowImportPreview.id == identifier, WorkflowImportPreview.actor_id == actor.user_id).with_for_update())
            if row is None:
                raise DomainError("not_found", "预览不存在或未授权", 404)
            if row.candidate_digest != digest:
                raise DomainError("workflow_candidate_mismatch", "确认摘要与预览不一致，请重新预览", 409)
            if row.result:
                idem.result = row.result
                return row.result
            if row.expires_at <= utcnow() or row.candidate is None:
                raise DomainError("workflow_preview_expired", "预览已过期，请重新预览", 409)
            if fingerprint(row.candidate) != digest:
                raise DomainError("workflow_candidate_mismatch", "预览内容摘要不一致", 409)
            channel = _channel(session, row.channel_id, row.source_revision)
            if row.workflow_id:
                assert row.expected_revision is not None
                workflow = _target(session, row.workflow_id, channel, row.expected_revision, lock=True)
                changed = fingerprint(workflow.draft) != digest
                if changed:
                    workflow.draft = row.candidate
                    workflow.revision += 1
            else:
                workflow = Workflow(tenant_id=channel.tenant_id, project_id=channel.project_id, name=row.target["name"], task_type=row.target["task_type"], draft=row.candidate)
                session.add(workflow)
                changed = True
            session.flush()
            row.result = {"workflow": workflow_projection(workflow), "changed": changed}
            row.candidate = None  # Imported draft now owns the data; avoid a redundant copy.
            idem.result = row.result
            record_change(session, public_id("usr", actor.user_id), "workflow.imported", workflow.id, request_id, {"preview_id": public_id("wip", row.id), "source_digest": row.source_digest, "candidate_digest": digest, "changed": changed}, revision_before=row.expected_revision, revision_after=workflow.revision)
            return row.result
