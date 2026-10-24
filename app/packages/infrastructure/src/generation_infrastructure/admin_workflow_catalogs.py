from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.workflow_catalogs import WorkflowCatalogRequest
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import select, text

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision
from generation_infrastructure.admin_channels import enqueue_command, lock_read_channel
from generation_infrastructure.models import Channel, ManagementOperation, Node, WorkerCommand


def export_status(channel: Channel, node: Node | None) -> dict[str, Any]:
    observation = channel.observation
    raw = observation.get("workflow_export")
    value = raw if isinstance(raw, dict) else {}
    observed = observation.get("workflow_export_observed_at", 0)
    now = utcnow().timestamp()
    fresh = bool(node and node.enabled and node.current_boot_id and node.observed_at and 0 <= now - node.observed_at.timestamp() < 15 and observation.get("workflow_export_boot_id") == str(node.current_boot_id) and observation.get("workflow_export_revision") == channel.applied_revision == channel.desired_revision and isinstance(observed, (int, float)) and 0 <= now - observed < 15 and isinstance(value, dict) and value.get("protocol") == "local-directory-v1" and value.get("status") in ("ready", "not_configured", "unavailable"))
    return {"channel_id": public_id("ch", channel.id), "channel_revision": channel.revision, "status": value["status"] if fresh else "unknown", "observation_status": "fresh" if fresh else "unknown", "observed_at": int(observed) if fresh else None, "catalog_supported": fresh}


class WorkflowCatalogAdministration(AdminBase):
    def source_status(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            channel = session.get(Channel, identifier)
            if channel is None or channel.deleted_at is not None:
                raise DomainError("not_found", "渠道不存在", 404)
            return export_status(channel, session.get(Node, channel.node_id))

    def browse(self, actor: AdminIdentity, data: WorkflowCatalogRequest, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.catalog", token, data.model_dump())
            if idem.result:
                return idem.result
            channel, node = lock_read_channel(session, parse_id("ch", data.channel_id))
            require_revision(channel.revision, data.expected_revision)
            state = export_status(channel, node)
            if state["status"] != "ready":
                raise DomainError("workflow_export_unavailable", "来源目录未就绪或观测已过期，请刷新来源状态", 409)
            pending = session.scalar(select(WorkerCommand.id).where(WorkerCommand.channel_id == channel.id, WorkerCommand.command == "pull_workflow", WorkerCommand.state.in_(["pending", "running"])).limit(1))
            if pending:
                raise DomainError("workflow_pull_pending", "此渠道已有读取操作，请先查看操作结果", 409)
            payload = {"mode": "catalog", "revision": channel.desired_revision, "relative_directory": data.relative_directory, "cursor": data.cursor, "export_boot_id": str(node.current_boot_id)}
            idem.result = enqueue_command(session, actor, channel, "pull_workflow", payload, request_id)
            return idem.result

    def detail(self, actor: AdminIdentity, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            commands = list(session.scalars(select(WorkerCommand).join(ManagementOperation, ManagementOperation.id == WorkerCommand.operation_id).where(WorkerCommand.operation_id == identifier, ManagementOperation.actor_id == actor.user_id, ManagementOperation.command == "pull_workflow").limit(2)))
            if len(commands) != 1 or commands[0].payload.get("mode") != "catalog":
                raise DomainError("not_found", "目录操作不存在或未授权", 404)
            command = commands[0]
            channel = session.get(Channel, command.channel_id)
            if channel is None or channel.deleted_at is not None:
                raise DomainError("not_found", "来源渠道不存在", 404)
            if channel.desired_revision != command.payload["revision"]:
                raise DomainError("workflow_source_stale", "来源渠道配置已变化，请重新浏览", 409)
            receipt = command.receipt or {}
            return {"id": public_id("op", identifier), "channel_id": public_id("ch", channel.id), "relative_directory": command.payload["relative_directory"], "status": command.state, "catalog": receipt.get("catalog") if command.state == "succeeded" else None, "error_code": receipt.get("error_code")}
