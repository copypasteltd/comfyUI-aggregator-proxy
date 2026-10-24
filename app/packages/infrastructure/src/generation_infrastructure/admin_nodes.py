from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.worker_nodes import WorkerNodeAction, WorkerNodeCreate, WorkerNodePatch
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from sqlalchemy import String, func, or_, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision, timestamp
from generation_infrastructure.models import (
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Node,
    WorkerCommand,
)
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.transactions import record_change


def activity(session: Session, identifiers: list[UUID]) -> dict[UUID, dict[str, int]]:
    result = {identifier: dict.fromkeys(("gpu_count", "open_attempts", "held_gpus", "uncertain_gpus", "management_holds", "running_commands", "live_channels"), 0) for identifier in identifiers}
    queries = (
        ("gpu_count", select(Gpu.node_id, func.count()).where(Gpu.node_id.in_(identifiers)).group_by(Gpu.node_id)),
        ("management_holds", select(Gpu.node_id, func.count()).where(Gpu.node_id.in_(identifiers), Gpu.management_hold.is_not(None)).group_by(Gpu.node_id)),
        ("open_attempts", select(Attempt.node_id, func.count()).where(Attempt.node_id.in_(identifiers), Attempt.closed_at.is_(None)).group_by(Attempt.node_id)),
        ("held_gpus", select(Gpu.node_id, func.count()).join(GpuOccupancy, GpuOccupancy.gpu_id == Gpu.id).where(Gpu.node_id.in_(identifiers)).group_by(Gpu.node_id)),
        ("uncertain_gpus", select(Gpu.node_id, func.count()).join(GpuOccupancy, GpuOccupancy.gpu_id == Gpu.id).where(Gpu.node_id.in_(identifiers), GpuOccupancy.uncertain.is_(True)).group_by(Gpu.node_id)),
        ("running_commands", select(WorkerCommand.node_id, func.count()).where(WorkerCommand.node_id.in_(identifiers), WorkerCommand.state == "running").group_by(WorkerCommand.node_id)),
        ("live_channels", select(Channel.node_id, func.count()).where(Channel.node_id.in_(identifiers), Channel.deleted_at.is_(None)).group_by(Channel.node_id)),
    )
    if identifiers:
        for name, query in queries:
            for identifier, count in session.execute(query):
                result[identifier][name] = count
    return result


def idle(counts: dict[str, int]) -> bool:
    return not any(counts[name] for name in ("open_attempts", "held_gpus", "management_holds", "running_commands"))


def projection(node: Node, counts: dict[str, int]) -> dict[str, Any]:
    mutable = node.archived_at is None
    return {
        "id": public_id("node", node.id), "name": node.name, "enabled": node.enabled,
        "archived_at": timestamp(node.archived_at), "revision": node.revision, "created_at": timestamp(node.created_at),
        "observed_at": timestamp(node.observed_at), "boot_registered": node.current_boot_id is not None,
        "credential_hint": "worker_" + node.credential_identifier[:8] + "…", "gpu_count": counts["gpu_count"],
        "activity": {name: value for name, value in counts.items() if name != "gpu_count"},
        "can_disable": mutable and node.enabled and idle(counts), "can_rotate": mutable and idle(counts),
        "can_archive": mutable and not node.enabled and idle(counts) and counts["live_channels"] == 0,
    }


class NodeAdministration(AdminBase):
    def list_nodes(self, page: int = 1, q: str = "", state: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            conditions = []
            if q:
                conditions.append(or_(Node.name.ilike("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"), func.concat("node_", func.replace(func.cast(Node.id, String), "-", "")) == q))
            if state == "archived":
                conditions.append(Node.archived_at.is_not(None))
            elif state in ("enabled", "disabled"):
                conditions.extend((Node.archived_at.is_(None), Node.enabled.is_(state == "enabled")))
            total = session.scalar(select(func.count()).select_from(Node).where(*conditions)) or 0
            nodes = list(session.scalars(select(Node).where(*conditions).order_by(Node.created_at.desc(), Node.id).offset((page - 1) * 50).limit(51)))
            counts = activity(session, [node.id for node in nodes[:50]])
            return {"snapshot_at": int(utcnow().timestamp()), "data": [projection(node, counts[node.id]) for node in nodes[:50]], "page": page, "has_more": len(nodes) > 50, "total": total}

    def get_node(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            node = session.get(Node, identifier)
            if node is None:
                raise DomainError("not_found", "节点不存在", 404)
            result = projection(node, activity(session, [identifier])[identifier])
            gpus = list(session.scalars(select(Gpu).where(Gpu.node_id == identifier).order_by(Gpu.id).limit(101)))
            channels = list(session.scalars(select(Channel).where(Channel.node_id == identifier, Channel.deleted_at.is_(None)).order_by(Channel.id).limit(101)))
            return {**result, "gpus": [{"id": public_id("gpu", gpu.id), "name": gpu.name, "hardware_id": gpu.hardware_id, "memory_bytes": gpu.memory_bytes, "enabled": gpu.enabled} for gpu in gpus[:100]], "channels": [{"id": public_id("ch", channel.id), "name": channel.name, "enabled": channel.enabled, "draining": channel.draining} for channel in channels[:100]], "gpus_truncated": len(gpus) > 100, "channels_truncated": len(channels) > 100}

    def create_node(self, actor: AdminIdentity, data: WorkerNodeCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "node.create", token, data.model_dump())
            if idem.result:
                return {**idem.result, "secret_available": False}
            identifier, secret = issue_secret("worker")
            node = Node(name=data.name, credential_identifier=identifier, credential_verifier=verifier(secret, self.settings.credential_pepper.get_secret_value()), pepper_version=self.settings.credential_pepper_version)
            session.add(node)
            session.flush()
            result = {"node": projection(node, activity(session, [node.id])[node.id])}
            idem.result = result
            record_change(session, public_id("usr", actor.user_id), "node.created", node.id, request_id, revision_before=None, revision_after=node.revision)
            return {**result, "secret_available": True, "secret": secret}

    def patch_node(self, actor: AdminIdentity, identifier: UUID, data: WorkerNodePatch, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "node.patch:" + str(identifier), token, data.model_dump())
            if idem.result:
                return idem.result
            node = session.scalar(select(Node).where(Node.id == identifier).with_for_update())
            self._mutable(node, data.expected_revision)
            assert node is not None
            node.name = data.name
            node.revision += 1
            result = {"node": projection(node, activity(session, [identifier])[identifier]), "secret_available": False}
            idem.result = result
            record_change(session, public_id("usr", actor.user_id), "node.renamed", identifier, request_id, revision_before=data.expected_revision, revision_after=node.revision)
            return result

    @staticmethod
    def _mutable(node: Node | None, expected: int) -> None:
        if node is None:
            raise DomainError("not_found", "节点不存在", 404)
        require_revision(node.revision, expected)
        if node.archived_at:
            raise DomainError("node_archived", "节点已归档，不能修改或恢复", 409)

    def action(self, actor: AdminIdentity, identifier: UUID, data: WorkerNodeAction, token: str, request_id: str) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                idem = mutation(session, actor, "node.action:" + str(identifier), token, data.model_dump())
                if idem.result:
                    return {**idem.result, "secret_available": False}
                gpu_ids = list(session.scalars(select(Gpu.id).where(Gpu.node_id == identifier).order_by(Gpu.id).limit(101).with_for_update(nowait=True)))
                if len(gpu_ids) > 100:
                    raise DomainError("node_inventory_limit", "节点历史 GPU 超过管理上限，需要先核对硬件清单", 409)
                node = session.scalar(select(Node).where(Node.id == identifier).with_for_update(nowait=True))
                self._mutable(node, data.expected_revision)
                assert node is not None
                if list(session.scalars(select(Gpu.id).where(Gpu.node_id == identifier).order_by(Gpu.id).limit(101))) != gpu_ids:
                    raise DomainError("node_inventory_changed", "节点硬件清单已变化，请刷新后重试", 409)
                counts = activity(session, [identifier])[identifier]
                if data.action != "enable" and not idle(counts):
                    raise DomainError("node_not_idle", "节点仍有活动尝试、GPU 占用或管理操作；请先排空并完成核对", 409)
                secret = None
                if data.action == "archive":
                    if node.enabled or counts["live_channels"]:
                        raise DomainError("node_still_referenced", "请先停用节点并删除其全部渠道，再归档", 409)
                    node.archived_at = utcnow()
                elif data.action == "enable":
                    node.enabled = True
                elif data.action == "disable":
                    node.enabled = False
                else:
                    credential, secret = issue_secret("worker")
                    node.credential_identifier = credential
                    node.credential_verifier = verifier(secret, self.settings.credential_pepper.get_secret_value())
                    node.pepper_version = self.settings.credential_pepper_version
                if data.action in ("disable", "rotate_credential", "archive"):
                    node.current_boot_id = None
                    node.observed_at = None
                    node.inventory = {}
                node.revision += 1
                result = {"node": projection(node, counts)}
                idem.result = result
                record_change(session, public_id("usr", actor.user_id), "node." + data.action, identifier, request_id, revision_before=data.expected_revision, revision_after=node.revision)
                return {**result, "secret_available": secret is not None, **({"secret": secret} if secret else {})}
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) in ("55P03", "40P01"):
                raise DomainError("node_busy", "节点正在处理其他操作，请刷新后重试", 409) from None
            raise
