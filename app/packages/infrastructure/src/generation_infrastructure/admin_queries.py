from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from generation_contracts.dispatch import DispatchPolicy
from generation_contracts.queue_queries import QueueOptionQuery, QueueQuery
from generation_domain.errors import DomainError
from generation_domain.state import TERMINAL
from generation_domain.values import public_id, utcnow
from sqlalchemy import String, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, timestamp
from generation_infrastructure.admin_channels import begin_channel_read, channel_projection
from generation_infrastructure.admin_workflow_retries import RETRY_KEY
from generation_infrastructure.command_recovery import RECOVERY_KEY, recovery_proof, recovery_reason
from generation_infrastructure.job_cancellations import DOCUMENT as CANCELLATIONS_KEY
from generation_infrastructure.jobs import job_projection
from generation_infrastructure.model_inventory import model_files_view
from generation_infrastructure.models import (
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    ManagementOperation,
    Node,
    Project,
    Tenant,
    WorkerCommand,
    WorkflowBinding,
)
from generation_infrastructure.queue_queries import (
    begin_queue_read,
    channel_filters,
    filter_options,
    job_filters,
)


def operation_receipt(receipt: dict[str, Any] | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    safe: dict[str, Any] = {}
    for name in ("success", "environment_verified"):
        if type(receipt.get(name)) is bool:
            safe[name] = receipt[name]
    revision = receipt.get("applied_revision")
    if type(revision) is int and 1 <= revision <= 2**63 - 1:
        safe["applied_revision"] = revision
    digest = receipt.get("release_digest")
    if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
        safe["release_digest"] = digest
    source_digest = receipt.get("source_digest")
    if isinstance(source_digest, str) and re.fullmatch(r"[a-f0-9]{64}", source_digest):
        safe["source_digest"] = source_digest
    if receipt.get("export_format") in ("comfy-api", "manifest-v1"):
        safe["export_format"] = receipt["export_format"]
    if type(receipt.get("source_bytes")) is int and 1 <= receipt["source_bytes"] <= 1024 * 1024:
        safe["source_bytes"] = receipt["source_bytes"]
    if receipt.get("success") is False:
        error = receipt.get("error_code")
        safe["error_code"] = error if isinstance(error, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error) else "worker_command_failed"
    return safe


class AdminQueries(AdminBase):
    def _channel_queue(self, channel: Channel, now: datetime, candidates: int, assigned: int, occupancy: GpuOccupancy | None, gpu: Gpu | None) -> dict[str, Any]:
        observed = channel.observation.get("queue_observed_at", 0)
        queue = channel.observation.get("queue", {})
        fresh = now.timestamp() - 15 < observed <= now.timestamp() and channel.observation.get("queue_revision") == channel.desired_revision == channel.applied_revision and queue.get("reachable") is True
        return {**channel_projection(channel), "shared_candidates": candidates, "assigned_unfinished": assigned, "gpu_occupied": occupancy is not None, "gpu_occupancy_uncertain": occupancy.uncertain if occupancy else False, "gpu_management_hold": bool(gpu and gpu.management_hold), "observation_status": "fresh" if fresh else "unknown", "queue_observed_at": int(observed) if observed else None, "comfy_queue_running": queue.get("queue_running") if fresh else None, "comfy_queue_pending": queue.get("queue_pending") if fresh else None}

    def _channel_queues(self, session: Session, channels: list[Channel], now: datetime, query: QueueQuery) -> list[dict[str, Any]]:
        if not channels:
            return []
        ids = [channel.id for channel in channels]
        conditions = job_filters(query)
        channel_public_id = func.concat("ch_", func.replace(Channel.id.cast(String), "-", ""))
        candidates = {identifier: count for identifier, count in session.execute(select(Channel.id, func.count(Job.id)).join(Job, Job.eligible_channels.contains(func.jsonb_build_array(channel_public_id))).where(Channel.id.in_(ids), Job.state == "queued", Job.current_attempt_id.is_(None), *conditions).group_by(Channel.id))}
        assigned = {identifier: count for identifier, count in session.execute(select(Attempt.channel_id, func.count(Job.id)).join(Job, Job.current_attempt_id == Attempt.id).where(Attempt.channel_id.in_(ids), Job.state.not_in(TERMINAL), *conditions).group_by(Attempt.channel_id))}
        resources = {gpu.id: (gpu, occupancy) for gpu, occupancy in session.execute(select(Gpu, GpuOccupancy).outerjoin(GpuOccupancy, GpuOccupancy.gpu_id == Gpu.id).where(Gpu.id.in_({c.gpu_id for c in channels})))}
        return [self._channel_queue(channel, now, candidates.get(channel.id, 0), assigned.get(channel.id, 0), resources[channel.gpu_id][1], resources[channel.gpu_id][0]) for channel in channels]

    def scopes(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [{"tenant_id": public_id("ten", t.id), "tenant_name": t.name, "project_id": public_id("prj", p.id), "project_name": p.name} for t, p in session.execute(select(Tenant, Project).join(Project, Project.tenant_id == Tenant.id).order_by(Tenant.created_at, Project.created_at).limit(100))]

    def queue_snapshot(self, query: QueueQuery | None = None) -> dict[str, Any]:
        query = query or QueueQuery()
        with self.sessions.begin() as session:
            begin_queue_read(session)
            now = utcnow()
            conditions = job_filters(query)
            counts: dict[str, int] = {state: count for state, count in session.execute(select(Job.state, func.count()).where(*conditions).group_by(Job.state))}
            unassigned = session.scalar(select(func.count()).select_from(Job).where(Job.state == "queued", Job.current_attempt_id.is_(None), *conditions))
            channels = list(session.scalars(select(Channel).where(*channel_filters(query)).order_by(Channel.created_at, Channel.id).offset((query.page - 1) * 50).limit(51)))
            rows = self._channel_queues(session, channels[:50], now, query)
            occupied = session.scalar(select(func.count()).select_from(GpuOccupancy))
            return {"dispatch_policy": DispatchPolicy.current().model_dump(), "snapshot_id": str(uuid4()), "snapshot_at": int(now.timestamp()), "server_time": int(now.timestamp()), "observed_at": int(now.timestamp()), "stale": False, "filters": query.filters(), "global_unassigned_queued": unassigned, "unfinished": sum(v for k, v in counts.items() if k not in TERMINAL), "occupied_physical_gpus": occupied, "states": counts, "channels": rows, "channels_page": query.page, "channels_has_more": len(channels) > 50, "candidate_counts_overlap": True}

    def queue_filter_options(self, query: QueueOptionQuery) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            return filter_options(session, query)

    def channel_queue(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            channel = session.get(Channel, identifier)
            if not channel or channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            return self._channel_queues(session, [channel], utcnow(), QueueQuery())[0]

    def inventory(self, identifier: UUID, model_page: int = 1) -> dict[str, Any]:
        with self.sessions() as session:
            channel = session.get(Channel, identifier)
            if not channel or channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            observed = channel.observation.get("inventory_observed_at")
            return {"channel_id": public_id("ch", channel.id), "observed_at": observed, "stale": not observed or utcnow().timestamp() - observed > 300, "inventory": channel.observation.get("inventory", {}), "model_files": model_files_view(session, channel, session.get(Node, channel.node_id), utcnow(), model_page), "bindings": [{"release_id": public_id("wfr", binding.release_id), "desired_digest": binding.desired_digest, "applied_digest": binding.applied_digest, "verified_digest": binding.verified_digest, "observed_at": timestamp(binding.observed_at), "runtime_approved": binding.runtime_approval is not None} for binding in session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id).order_by(WorkflowBinding.id).limit(100))]}

    def jobs(self, page: int = 1, state: str | None = None, channel_id: UUID | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            query = select(Job)
            if state:
                query = query.where(Job.state == state)
            if channel_id:
                query = query.join(Attempt, Job.current_attempt_id == Attempt.id).where(Attempt.channel_id == channel_id)
            rows = list(session.scalars(query.order_by(Job.created_at.desc(), Job.id).offset((page - 1) * 50).limit(51)))
            return {"data": [job_projection(j) for j in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def operations(self, page: int = 1) -> dict[str, Any]:
        if not 1 <= page <= 10000:
            raise DomainError("invalid_page", "页码超出范围", 422)
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                rows = list(session.scalars(select(ManagementOperation).order_by(ManagementOperation.created_at.desc(), ManagementOperation.id).offset((page - 1) * 50).limit(51)))
                return {"data": [{"id": public_id("op", r.id), "command": r.command, "status": r.state, "revision": r.revision, "created_at": timestamp(r.created_at), "summary": {k: v for k, v in r.summary.items() if k not in (RECOVERY_KEY, RETRY_KEY, CANCELLATIONS_KEY)}} for r in rows[:50]], "page": page, "has_more": len(rows) > 50}
        except DBAPIError:
            raise DomainError("operation_query_unavailable", "管理操作读取暂不可用，请稍后重试", 503) from None

    def operation(self, identifier: UUID) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                now = utcnow()
                operation = session.get(ManagementOperation, identifier)
                if not operation:
                    raise DomainError("not_found", "管理操作不存在", 404)
                targets = list(session.scalars(select(WorkerCommand).where(WorkerCommand.operation_id == identifier).order_by(WorkerCommand.id).limit(101)))
                nodes = {n.id: n for n in session.scalars(select(Node).where(Node.id.in_({c.node_id for c in targets[:100]})))}
                projected = []
                for c in targets[:100]:
                    node = nodes.get(c.node_id)
                    reason = recovery_reason(c, node.current_boot_id if node else None, now)
                    proof = recovery_proof(operation, c)
                    projected.append({"id": public_id("cmd", c.id), "channel_id": public_id("ch", c.channel_id), "node_id": public_id("node", c.node_id), "status": c.state, "receipt": operation_receipt(c.receipt), "lease_expires_at": timestamp(c.lease_expires_at), "completed_at": timestamp(c.completed_at), "needs_reconciliation": reason is not None, "reason": reason, "recovery": {"resolution": "result_requires_revalidation", "reconciled_at": proof["reconciled_at"]} if proof else None})
                retry_of = operation.summary.get("retry_of")
                return {"id": public_id("op", identifier), "status": operation.state, "command": operation.command, "targets": projected, "has_more": len(targets) > 100, "snapshot_at": int(now.timestamp()), "retry_of": retry_of if isinstance(retry_of, str) and re.fullmatch(r"op_[0-9a-f]{32}", retry_of) else None}
        except DBAPIError:
            raise DomainError("operation_query_unavailable", "管理操作读取暂不可用，请稍后重试", 503) from None
