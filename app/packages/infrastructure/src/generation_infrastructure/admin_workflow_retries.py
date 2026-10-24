from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.workflow_retries import WorkflowSyncRetry
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, parse_id, public_id, utcnow
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.admin_channels import begin_channel_read
from generation_infrastructure.command_recovery import recovery_proof
from generation_infrastructure.commands import command_transaction
from generation_infrastructure.models import (
    Channel,
    ManagementOperation,
    Node,
    WorkerCommand,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.transactions import record_change

RETRY_KEY = "_workflow_retries"


def source_operation(session: Session, identifier: UUID) -> tuple[ManagementOperation, list[WorkerCommand], str, str, WorkflowRelease | None]:
    operation = session.get(ManagementOperation, identifier)
    if operation is None:
        raise DomainError("not_found", "管理操作不存在", 404)
    commands = list(session.scalars(select(WorkerCommand).where(WorkerCommand.operation_id == identifier).order_by(WorkerCommand.id).limit(101)))
    if operation.command != "apply_workflow_release" or not commands or len(commands) > 100 or len({c.channel_id for c in commands}) != len(commands):
        raise DomainError("workflow_sync_not_retryable", "此操作不支持失败渠道重试", 409)
    release_id = commands[0].payload.get("release_id")
    digest = commands[0].payload.get("release_digest")
    if not isinstance(release_id, str) or not re.fullmatch(r"wfr_[a-f0-9]{32}", release_id) or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) or any(c.command != "apply_workflow_release" or c.payload.get("release_id") != release_id or c.payload.get("release_digest") != digest for c in commands):
        raise DomainError("workflow_sync_not_retryable", "来源操作的冻结版本不一致，请核对原操作", 409)
    return operation, commands, release_id, digest, session.get(WorkflowRelease, parse_id("wfr", release_id))


def retry_projection(session: Session, operation: ManagementOperation, commands: list[WorkerCommand], release: WorkflowRelease | None, digest: str) -> list[dict[str, Any]]:
    ids = {c.channel_id for c in commands}
    channels = {c.id: c for c in session.scalars(select(Channel).where(Channel.id.in_(ids)))}
    bindings = {b.channel_id: b for b in session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id.in_(ids), WorkflowBinding.release_id == release.id))} if release else {}
    latest = {channel_id: command_id for channel_id, command_id in session.execute(select(WorkerCommand.channel_id, WorkerCommand.id).where(WorkerCommand.channel_id.in_(ids), WorkerCommand.command == "apply_workflow_release").distinct(WorkerCommand.channel_id).order_by(WorkerCommand.channel_id, WorkerCommand.created_at.desc(), WorkerCommand.id.desc()))}
    busy = set(session.scalars(select(WorkerCommand.channel_id).where(WorkerCommand.channel_id.in_(ids), WorkerCommand.state.not_in(("succeeded", "failed"))).distinct()))
    lineage = operation.summary.get(RETRY_KEY, {})
    if not isinstance(lineage, dict):
        raise DomainError("workflow_sync_not_retryable", "来源重试记录需核对", 409)
    result = []
    for command in commands:
        channel = channels.get(command.channel_id)
        binding = bindings.get(command.channel_id)
        successor = lineage.get(public_id("cmd", command.id))
        retry_id = successor.get("operation_id") if isinstance(successor, dict) else None
        reason = None
        if operation.state not in ("failed", "partial_failed"):
            reason = "operation_incomplete"
        elif command.state != "failed":
            reason = "not_failed"
        elif recovery_proof(operation, command):
            reason = "revalidation_required"
        elif not command.receipt or command.receipt.get("success") is not False:
            reason = "failure_evidence_required"
        elif successor is not None:
            reason = "already_retried"
        elif channel is None or channel.deleted_at or channel.node_id != command.node_id:
            reason = "channel_unavailable"
        elif not release or not release.validated or release.digest != digest or (channel.tenant_id, channel.project_id) != (release.tenant_id, release.project_id) or not binding or binding.desired_digest != digest:
            reason = "release_unavailable"
        elif type(command.payload.get("revision")) is not int or command.payload["revision"] != channel.desired_revision:
            reason = "configuration_changed"
        elif latest.get(channel.id) != command.id:
            reason = "superseded"
        elif channel.id in busy:
            reason = "management_busy"
        result.append({"command_id": public_id("cmd", command.id), "channel_id": public_id("ch", command.channel_id), "channel_name": channel.name if channel and not channel.deleted_at else None, "current_revision": channel.revision if channel and not channel.deleted_at else None, "status": command.state, "eligible": reason is None, "reason": reason, "retry_operation_id": retry_id if isinstance(retry_id, str) and re.fullmatch(r"op_[0-9a-f]{32}", retry_id) else None})
    return result


class WorkflowSyncRetries(AdminBase):
    def options(self, identifier: UUID) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                operation, commands, release_id, digest, release = source_operation(session, identifier)
                return {"operation_id": public_id("op", operation.id), "source_status": operation.state, "release_id": release_id, "release_digest": digest, "task_type": release.manifest.get("task_type") if release else None, "snapshot_at": int(utcnow().timestamp()), "targets": retry_projection(session, operation, commands, release, digest)}
        except DBAPIError:
            raise DomainError("workflow_retry_query_unavailable", "重试目标暂不可读，请稍后刷新", 503) from None

    def retry(self, actor: AdminIdentity, identifier: UUID, data: WorkflowSyncRetry, token: str, request_id: str) -> dict[str, Any]:
        with command_transaction(self.sessions) as session:
            idem = mutation(session, actor, "workflow.retry_sync:" + str(identifier), token, data.model_dump(mode="json"))
            if idem.result is not None:
                return idem.result
            operation, commands, release_id, digest, release = source_operation(session, identifier)
            if (data.release_id, data.release_digest) != (release_id, digest):
                raise DomainError("workflow_retry_version_conflict", "只能重试原操作的冻结版本", 409)
            selected = {parse_id("cmd", t.command_id): t for t in data.targets}
            sources = [c for c in commands if c.id in selected]
            if len(sources) != len(selected) or any(public_id("ch", c.channel_id) != selected[c.id].channel_id for c in sources):
                raise DomainError("workflow_retry_target_conflict", "重试目标与来源操作不匹配", 409)
            # Even duplicate terminal receipts lock Command before Channel.
            # Lock selected source commands first and never wait for a GPU here.
            node_ids = {c.node_id for c in sources}
            list(session.scalars(select(Node).where(Node.id.in_(node_ids)).order_by(Node.id).with_for_update(read=True)))
            list(session.scalars(select(WorkerCommand).where(WorkerCommand.id.in_(selected)).order_by(WorkerCommand.id).with_for_update().execution_options(populate_existing=True)))
            channel_ids = {c.channel_id for c in sources}
            channels = list(session.scalars(select(Channel).where(Channel.id.in_(channel_ids)).order_by(Channel.id).with_for_update().execution_options(populate_existing=True)))
            operation = session.scalars(select(ManagementOperation).where(ManagementOperation.id == identifier).with_for_update().execution_options(populate_existing=True)).one()
            options = {parse_id("cmd", row["command_id"]): row for row in retry_projection(session, operation, sources, release, digest)}
            for source in sources:
                row = options[source.id]
                if not row["eligible"]:
                    raise DomainError("workflow_retry_" + row["reason"], "目标状态已变化或不允许重试，请刷新目标并核对原因", 409)
                require_revision(row["current_revision"], selected[source.id].expected_revision)
            assert release
            if len(canonical_json(release.manifest).encode("utf-8")) * len(sources) > 16 * 1024**2:
                raise DomainError("sync_batch_too_large", "本批冻结指令超过 16 MiB，请减少目标数量", 422)
            child = ManagementOperation(actor_id=actor.user_id, command="apply_workflow_release", summary={"retry_of": public_id("op", identifier), "release_id": release_id, "release_digest": digest, "target_count": len(sources)})
            session.add(child)
            session.flush()
            lineage = dict(operation.summary.get(RETRY_KEY, {}))
            bindings = CatalogAdministration(self.sessions, self.settings)
            for channel in channels:
                source = next(c for c in sources if c.channel_id == channel.id)
                before = channel.revision
                payload = bindings._prepare_binding(session, channel, release)
                command = WorkerCommand(operation_id=child.id, node_id=channel.node_id, channel_id=channel.id, command="apply_workflow_release", payload=payload)
                session.add(command)
                session.flush()
                lineage[public_id("cmd", source.id)] = {"operation_id": public_id("op", child.id), "command_id": public_id("cmd", command.id)}
                record_change(session, public_id("usr", actor.user_id), "workflow.channel_retried", channel.id, request_id, {"release_id": release_id, "source_command_id": public_id("cmd", source.id), "retry_of": public_id("op", identifier)}, revision_before=before, revision_after=channel.revision, operation_id=child.id)
            if len(lineage) > 100:
                raise DomainError("workflow_sync_not_retryable", "来源重试记录超过受控上限", 409)
            operation.summary = {**operation.summary, RETRY_KEY: lineage}
            operation.revision += 1
            record_change(session, public_id("usr", actor.user_id), "workflow.sync_retried", child.id, request_id, {"retry_of": public_id("op", identifier), "release_id": release_id, "target_count": len(sources)}, operation_id=child.id)
            idem.result = {"operation_id": public_id("op", child.id), "status": "pending"}
            return idem.result
