from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.admin import ChannelAction, ChannelCreate, ChannelPatch, ExpectedRevision
from generation_contracts.channel_queries import ChannelQuery
from generation_domain.errors import DomainError
from generation_domain.state import TERMINAL
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from generation_infrastructure.admin_base import (
    AdminBase,
    configured_url,
    mutation,
    require_revision,
    scope_ids,
    timestamp,
)
from generation_infrastructure.commands import command_transaction
from generation_infrastructure.models import (
    Attempt,
    Channel,
    ChannelRevision,
    Gpu,
    GpuOccupancy,
    Job,
    ManagementOperation,
    Node,
    PublicModel,
    WorkerCommand,
    WorkflowBinding,
)
from generation_infrastructure.transactions import record_change


def channel_projection(channel: Channel) -> dict[str, Any]:
    return {"id": public_id("ch", channel.id), "tenant_id": public_id("ten", channel.tenant_id), "project_id": public_id("prj", channel.project_id), "name": channel.name, "node_id": public_id("node", channel.node_id), "gpu_id": public_id("gpu", channel.gpu_id), "base_url": channel.base_url, "has_authorization": channel.encrypted_auth is not None, "enabled": channel.enabled, "draining": channel.draining, "revision": channel.revision, "desired_revision": channel.desired_revision, "applied_revision": channel.applied_revision, "verified_revision": channel.verified_revision, "deleted_at": timestamp(channel.deleted_at), "observed_at": timestamp(channel.observed_at)}


def begin_channel_read(session: Session) -> None:
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
    session.execute(text("SET LOCAL lock_timeout = '1000ms'"))


def channel_deletion_blockers(session: Session, channel: Channel, gpu: Gpu) -> list[dict[str, Any]]:
    """Use bounded identifier samples, never mutable command bodies or prompts."""
    identifier = public_id("ch", channel.id)
    queries = (
        ("active_jobs", "job", select(Job.id).where(Job.state.not_in(TERMINAL), Job.eligible_channels.contains([identifier])).order_by(Job.id)),
        ("open_attempts", "job", select(Attempt.job_id).where(Attempt.channel_id == channel.id, Attempt.closed_at.is_(None)).distinct().order_by(Attempt.job_id)),
        ("pending_commands", "op", select(WorkerCommand.operation_id).where(WorkerCommand.channel_id == channel.id, WorkerCommand.state.not_in(("succeeded", "failed"))).distinct().order_by(WorkerCommand.operation_id)),
        ("published_models", "mdl", select(PublicModel.id).where(PublicModel.published.is_(True), PublicModel.channel_ids.contains([identifier])).order_by(PublicModel.id)),
        ("gpu_occupancy", "job", select(GpuOccupancy.job_id).where(GpuOccupancy.gpu_id == gpu.id).order_by(GpuOccupancy.job_id)),
    )
    blockers = []
    for kind, prefix, query in queries:
        rows = list(session.scalars(query.limit(11)))
        if rows:
            blockers.append({"kind": kind, "resource_ids": [public_id(prefix, value) for value in rows[:10]], "has_more": len(rows) > 10})
    if gpu.management_hold is not None:
        blockers.append({"kind": "gpu_management_hold", "resource_ids": [public_id("gpu", gpu.id)], "has_more": False})
    return blockers


def deletion_projection(channel: Channel, blockers: list[dict[str, Any]]) -> dict[str, Any]:
    return {"channel_id": public_id("ch", channel.id), "node_id": public_id("node", channel.node_id), "gpu_id": public_id("gpu", channel.gpu_id), "revision": channel.revision, "snapshot_at": int(utcnow().timestamp()), "deleted": channel.deleted_at is not None, "can_delete": channel.deleted_at is None and not blockers, "blockers": blockers}


def lock_read_channel(session: Session, identifier: UUID) -> tuple[Channel, Node]:
    node_id = session.scalar(select(Channel.node_id).where(Channel.id == identifier, Channel.deleted_at.is_(None)))
    if node_id is None:
        raise DomainError("not_found", "渠道不存在", 404)
    # Heartbeat/register lock Node before updating its Channels. Acquire the
    # FK-compatible node lock first, before holding Channel through enqueue.
    node = session.scalar(select(Node).where(Node.id == node_id).with_for_update(read=True))
    channel = session.scalar(select(Channel).where(Channel.id == identifier, Channel.deleted_at.is_(None)).with_for_update())
    if channel is None:
        raise DomainError("not_found", "渠道不存在", 404)
    if node is None or channel.node_id != node_id:
        raise DomainError("config_revision_conflict", "渠道所属节点已变化，请刷新后重试", 409)
    return channel, node


def enqueue_command(session: Any, actor: AdminIdentity, channel: Channel, command: str, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    operation = ManagementOperation(actor_id=actor.user_id, command=command)
    session.add(operation)
    session.flush()
    session.add(WorkerCommand(operation_id=operation.id, node_id=channel.node_id, channel_id=channel.id, command=command, payload=payload))
    record_change(session, public_id("usr", actor.user_id), "operation." + command, operation.id, request_id)
    return {"operation_id": public_id("op", operation.id), "status": "pending"}


class ChannelAdministration(AdminBase):
    def get_node(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            node = session.get(Node, identifier)
            if not node:
                raise DomainError("not_found", "节点不存在", 404)
            gpus = list(session.scalars(select(Gpu).where(Gpu.node_id == identifier).order_by(Gpu.id).limit(101)))
            channels = list(session.scalars(select(Channel).where(Channel.node_id == identifier, Channel.deleted_at.is_(None)).order_by(Channel.id).limit(101)))
            return {"id": public_id("node", node.id), "name": node.name, "enabled": node.enabled, "observed_at": timestamp(node.observed_at), "gpus": [{"id": public_id("gpu", gpu.id), "name": gpu.name, "hardware_id": gpu.hardware_id, "memory_bytes": gpu.memory_bytes, "enabled": gpu.enabled} for gpu in gpus[:100]], "channels": [channel_projection(channel) for channel in channels[:100]], "gpus_truncated": len(gpus) > 100, "channels_truncated": len(channels) > 100}

    def list_nodes(self) -> dict[str, Any]:
        with self.sessions() as session:
            return {"data": [{"id": public_id("node", n.id), "name": n.name, "enabled": n.enabled, "observed_at": timestamp(n.observed_at), "gpus": [{"id": public_id("gpu", g.id), "name": g.name, "hardware_id": g.hardware_id, "memory_bytes": g.memory_bytes, "enabled": g.enabled} for g in session.scalars(select(Gpu).where(Gpu.node_id == n.id).order_by(Gpu.id))]} for n in session.scalars(select(Node).where(Node.archived_at.is_(None)).order_by(Node.created_at).limit(100))]}

    def list_channels(self, page: int = 1, query: ChannelQuery | None = None) -> dict[str, Any]:
        query = query or ChannelQuery(page=page)
        conditions: list[ColumnElement[bool]] = []
        if query.q is not None:
            conditions.append(or_(Channel.name.icontains(query.q, autoescape=True), Channel.base_url.icontains(query.q, autoescape=True)))
        for name, prefix in (("channel_id", "ch"), ("tenant_id", "ten"), ("project_id", "prj"), ("node_id", "node")):
            if value := getattr(query, name):
                conditions.append(getattr(Channel, "id" if name == "channel_id" else name) == parse_id(prefix, value))
        if query.status is not None:
            conditions.append(Channel.deleted_at.is_not(None) if query.status == "deleted" else Channel.deleted_at.is_(None))
            if query.status in ("enabled", "disabled"):
                conditions.append(Channel.enabled.is_(query.status == "enabled"))
            elif query.status == "draining":
                conditions.append(Channel.draining.is_(True))
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                rows = list(session.scalars(select(Channel).where(*conditions).order_by(Channel.created_at.desc(), Channel.id).offset((query.page - 1) * 50).limit(51)))
                return {"data": [channel_projection(c) for c in rows[:50]], "page": query.page, "has_more": len(rows) > 50}
        except DBAPIError:
            raise DomainError("channel_query_unavailable", "渠道读取暂不可用，请稍后重试", 503) from None

    def get_channel(self, identifier: UUID) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                channel = session.get(Channel, identifier)
                if not channel:
                    raise DomainError("not_found", "渠道不存在", 404)
                result = channel_projection(channel)
                result["revisions"] = [{"revision": r.revision, "created_at": timestamp(r.created_at), "base_url": r.config["base_url"], "has_authorization": bool(r.config.get("encrypted_auth"))} for r in session.scalars(select(ChannelRevision).where(ChannelRevision.channel_id == identifier).order_by(ChannelRevision.revision.desc()).limit(50))]
                return result
        except DBAPIError:
            raise DomainError("channel_query_unavailable", "渠道读取暂不可用，请稍后重试", 503) from None

    def deletion_check(self, identifier: UUID) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                channel = session.get(Channel, identifier)
                if channel is None:
                    raise DomainError("not_found", "渠道不存在", 404)
                if channel.deleted_at is not None:
                    return deletion_projection(channel, [])
                gpu = session.get(Gpu, channel.gpu_id)
                assert gpu is not None
                return deletion_projection(channel, channel_deletion_blockers(session, channel, gpu))
        except DBAPIError:
            raise DomainError("channel_deletion_unavailable", "渠道删除检查暂不可用，请稍后重试", 503) from None

    def create_channel(self, actor: AdminIdentity, data: ChannelCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "channel.create", token, data.model_dump())
            if idem.result:
                return idem.result
            tenant, project = scope_ids(session, data.tenant_id, data.project_id)
            from generation_domain.values import parse_id
            node_id, gpu_id = parse_id("node", data.node_id), parse_id("gpu", data.gpu_id)
            node = session.scalar(select(Node).where(Node.id == node_id).with_for_update(read=True))
            if node is None or node.archived_at is not None:
                raise DomainError("node_unavailable", "节点不存在或已归档", 409)
            gpu = session.get(Gpu, gpu_id)
            if not gpu or gpu.node_id != node_id:
                raise DomainError("gpu_node_mismatch", "GPU 未登记或不属于指定 Worker", 422)
            channel = Channel(tenant_id=tenant, project_id=project, name=data.name, node_id=node_id, gpu_id=gpu_id, base_url=configured_url(data.base_url), encrypted_auth=self.vault.encrypt(data.authorization) if data.authorization else None)
            session.add(channel)
            session.flush()
            self._revision(session, channel)
            operation = enqueue_command(session, actor, channel, "apply_channel_config", {"revision": 1}, request_id)
            record_change(session, public_id("usr", actor.user_id), "channel.created", channel.id, request_id, revision_before=None, revision_after=channel.revision, operation_id=parse_id("op", operation["operation_id"]))
            idem.result = {"channel": channel_projection(channel), **operation}
            return idem.result

    def _revision(self, session: Any, channel: Channel) -> None:
        session.add(ChannelRevision(channel_id=channel.id, revision=channel.desired_revision, config={"base_url": channel.base_url, "encrypted_auth": channel.encrypted_auth, "gpu_id": public_id("gpu", channel.gpu_id)}))

    def patch_channel(self, actor: AdminIdentity, identifier: UUID, data: ChannelPatch, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            channel = session.execute(select(Channel).where(Channel.id == identifier).with_for_update()).scalar_one_or_none()
            if not channel or channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            require_revision(channel.revision, data.expected_revision)
            if data.name is not None:
                channel.name = data.name
            runtime_changed = data.base_url is not None or "authorization" in data.model_fields_set
            if runtime_changed:
                if data.base_url is not None:
                    channel.base_url = configured_url(data.base_url)
                if "authorization" in data.model_fields_set:
                    channel.encrypted_auth = self.vault.encrypt(data.authorization) if data.authorization else None
                channel.desired_revision += 1
                channel.verified_revision = None
                channel.enabled = False
                channel.draining = True
                self._revision(session, channel)
                for binding in session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier)):
                    binding.verified_digest = None
                    binding.runtime_approval = None
                operation = enqueue_command(session, actor, channel, "apply_channel_config", {"revision": channel.desired_revision}, request_id)
            else:
                # Resource revisions and immutable runtime revisions are distinct.
                operation = {}
            channel.revision += 1
            record_change(session, public_id("usr", actor.user_id), "channel.updated", channel.id, request_id, revision_before=data.expected_revision, revision_after=channel.revision, operation_id=parse_id("op", operation["operation_id"]) if operation else None)
            return {"channel": channel_projection(channel), **operation}

    def channel_action(self, actor: AdminIdentity, identifier: UUID, data: ChannelAction, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            channel = session.execute(select(Channel).where(Channel.id == identifier).with_for_update()).scalar_one_or_none()
            if not channel or channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            require_revision(channel.revision, data.expected_revision)
            if data.action in ("enable", "resume"):
                if channel.desired_revision != channel.applied_revision or channel.desired_revision != channel.verified_revision:
                    raise DomainError("channel_not_verified", "渠道配置尚未应用并验证")
                channel.enabled = True
                channel.draining = False
            else:
                channel.enabled = False
                channel.draining = data.action == "drain"
            channel.revision += 1
            record_change(session, public_id("usr", actor.user_id), "channel." + data.action, channel.id, request_id, revision_before=data.expected_revision, revision_after=channel.revision)
            return channel_projection(channel)

    def reapply_channel(self, actor: AdminIdentity, identifier: UUID, data: ExpectedRevision, token: str, request_id: str) -> dict[str, Any]:
        with command_transaction(self.sessions) as session:
            idem = mutation(session, actor, "channel.reapply:" + str(identifier), token, data.model_dump())
            if idem.result:
                return idem.result
            target = session.execute(select(Channel.node_id, Channel.gpu_id).where(Channel.id == identifier)).one_or_none()
            if target is None:
                raise DomainError("not_found", "渠道不存在", 404)
            session.execute(select(Node).where(Node.id == target.node_id).with_for_update(read=True)).scalar_one()
            session.execute(select(Gpu).where(Gpu.id == target.gpu_id).with_for_update()).scalar_one()
            channel = session.execute(select(Channel).where(Channel.id == identifier).with_for_update()).scalar_one()
            if channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            if (channel.node_id, channel.gpu_id) != tuple(target):
                raise DomainError("config_revision_conflict", "渠道资源归属已变化，请刷新后重试", 409)
            require_revision(channel.revision, data.expected_revision)
            if session.scalar(select(WorkerCommand.id).where(WorkerCommand.channel_id == identifier, WorkerCommand.state.not_in(("succeeded", "failed"))).limit(1)):
                raise DomainError("channel_command_unresolved", "渠道仍有未结束指令，请先在操作中心核对", 409)
            channel.enabled, channel.draining, channel.verified_revision = False, True, None
            channel.revision += 1
            for binding in session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id == identifier)):
                binding.verified_digest, binding.runtime_approval = None, None
            operation = enqueue_command(session, actor, channel, "apply_channel_config", {"revision": channel.desired_revision}, request_id)
            record_change(session, public_id("usr", actor.user_id), "channel.reapplied", channel.id, request_id, revision_before=data.expected_revision, revision_after=channel.revision, operation_id=parse_id("op", operation["operation_id"]))
            idem.result = {"channel": channel_projection(channel), **operation}
            return idem.result

    def delete_channel(self, actor: AdminIdentity, identifier: UUID, expected_revision: int, request_id: str) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                target = session.execute(select(Channel.node_id, Channel.gpu_id).where(Channel.id == identifier)).one_or_none()
                if target is None:
                    raise DomainError("not_found", "渠道不存在", 404)
                # Receipts use Node -> Command -> GPU -> Channel. Do not lock
                # commands/jobs/models after acquiring these resource locks.
                session.execute(select(Node).where(Node.id == target.node_id).with_for_update(read=True)).scalar_one()
                gpu = session.execute(select(Gpu).where(Gpu.id == target.gpu_id).with_for_update()).scalar_one()
                channel = session.execute(select(Channel).where(Channel.id == identifier).with_for_update()).scalar_one()
                if (channel.node_id, channel.gpu_id) != tuple(target):
                    raise DomainError("config_revision_conflict", "渠道资源归属已变化，请刷新后重试", 409)
                if channel.deleted_at:
                    return channel_projection(channel)
                require_revision(channel.revision, expected_revision)
                if channel_deletion_blockers(session, channel, gpu):
                    raise DomainError("channel_in_use", "渠道仍有任务、公开模型或资源操作引用，请查看删除检查并先完成处理", 409)
                channel.enabled = False
                channel.draining = True
                channel.deleted_at = utcnow()
                channel.revision += 1
                record_change(session, public_id("usr", actor.user_id), "channel.deleted", channel.id, request_id, revision_before=expected_revision, revision_after=channel.revision)
                return channel_projection(channel)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) in ("55P03", "57014", "40P01"):
                raise DomainError("channel_busy", "渠道资源正在处理其他操作，请刷新删除检查后重试", 409) from None
            raise DomainError("channel_deletion_unavailable", "渠道删除暂不可用，请稍后重试", 503) from None

    def channel_command(self, actor: AdminIdentity, identifier: UUID, command: str, token: str, request_id: str) -> dict[str, Any]:
        if command not in ("probe_channel", "scan_inventory", "collect_diagnostics"):
            raise DomainError("invalid_command", "不支持的管理指令", 422)
        with self.sessions.begin() as session:
            idem = mutation(session, actor, command + ":" + str(identifier), token, {})
            if idem.result:
                return idem.result
            channel, _ = lock_read_channel(session, identifier)
            idem.result = enqueue_command(session, actor, channel, command, {"revision": channel.desired_revision}, request_id)
            return idem.result
