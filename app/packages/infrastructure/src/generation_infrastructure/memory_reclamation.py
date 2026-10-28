"""Durable idle reclamation through stock ComfyUI HTTP; no host control.

Two successful CPU barriers are required: ComfyUI records history before
handling /free flags, so one history completion alone is insufficient.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from generation_application.ports import AdminIdentity
from generation_contracts.memory import MemoryPolicyPatch
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, public_id, utcnow
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision, timestamp
from generation_infrastructure.dispatch import DISPATCH_LOCK
from generation_infrastructure.models import (
    Attempt,
    Channel,
    Gpu,
    GpuOccupancy,
    Job,
    MemoryOperation,
    MemoryPolicy,
    Node,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from generation_infrastructure.transactions import record_change

SUPPORTED_VERSIONS = {"0.33.1", "0.42.1"}
TERMINAL = {"succeeded", "failed", "cancelled", "expired"}
DONE = {"completed", "skipped"}
BARRIER = {
    "1": {"class_type": "EmptyImage", "inputs": {"width": 1, "height": 1, "batch_size": 1, "color": 0}},
    "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
}


def required[T](value: T | None) -> T:
    if value is None:
        raise DomainError("memory_resource_missing", "回收资源关系异常，保持保护状态", 409)
    return value


def origin(value: str) -> str:
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
        raise DomainError("memory_invalid_origin", "回收仅支持已登记的 ComfyUI HTTP(S) 源", 409)
    host = "[" + url.hostname.lower() + "]" if ":" in url.hostname else url.hostname.lower()
    port = url.port or (443 if url.scheme == "https" else 80)
    return f"{url.scheme}://{host}:{port}"


def digest_origin(value: str) -> str:
    return hashlib.sha256(origin(value).encode()).hexdigest()


def group(session: Session, channel: Channel) -> list[Channel]:
    """Close over shared origin and registered GPU identities, across projects."""
    rows = list(session.scalars(select(Channel).where(Channel.deleted_at.is_(None)).limit(1001)))
    if len(rows) > 1000:
        raise DomainError("memory_group_limit", "渠道数量超过回收分组上限", 409)
    origins, gpus, chosen = {origin(channel.base_url)}, {channel.gpu_id}, {}
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row.id not in chosen and (row.gpu_id in gpus or origin(row.base_url) in origins):
                chosen[row.id] = row
                origins.add(origin(row.base_url))
                gpus.add(row.gpu_id)
                changed = True
    return sorted(chosen.values(), key=lambda row: str(row.id))


def memory_snapshot(stats: dict[str, Any]) -> dict[str, Any]:
    system = stats.get("system", {})
    return {"scope": "whole_host_and_device_not_process_rss", "ram_total": system.get("ram_total"), "ram_free": system.get("ram_free"),
            "devices": [{key: device.get(key) for key in ("vram_total", "vram_free", "torch_vram_total", "torch_vram_free")} for device in stats.get("devices", [])[:32]]}


class ComfyMemoryHttp:
    """Bounded, nonredirecting metadata requests; only fixed API paths/graphs."""
    def __init__(self, base: str, authorization: str | None) -> None:
        self.base, self.authorization = origin(base), authorization

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Accept-Encoding": "identity", "Accept": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        started = time.monotonic()
        with httpx.Client(base_url=self.base, timeout=httpx.Timeout(3, connect=3, write=5), follow_redirects=False, trust_env=False) as client:
            with client.stream(method, path, json=body, headers=headers) as response:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise DomainError("memory_http_response", "ComfyUI 回收接口响应异常", 409)
                data = bytearray()
                for chunk in response.iter_raw():
                    data.extend(chunk)
                    if len(data) > 4 * 1024 * 1024 or time.monotonic() - started > 12:
                        raise DomainError("memory_response_limit", "回收元数据超过限额", 409)
        value = json.loads(data or b"{}")
        if not isinstance(value, dict):
            raise DomainError("memory_http_response", "ComfyUI 回收响应格式异常", 409)
        return value


def queue_ids(value: dict[str, Any]) -> list[str]:
    result = []
    for key in ("queue_running", "queue_pending"):
        rows = value.get(key)
        if not isinstance(rows, list) or len(rows) > 2000:
            raise DomainError("memory_invalid_queue", "ComfyUI 队列不可核对", 409)
        for row in rows:
            if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], str):
                raise DomainError("memory_invalid_queue", "ComfyUI 队列格式异常", 409)
            result.append(row[1])
    return result


class MemoryAdministration(AdminBase):
    def get(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            channel = session.get(Channel, identifier)
            if not channel or channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            policy = session.scalar(select(MemoryPolicy).where(MemoryPolicy.origin_digest == digest_origin(channel.base_url)))
            if policy is None:
                policy = session.scalar(select(MemoryPolicy).where(MemoryPolicy.channel_id == channel.id))
            current_origin = bool(policy and policy.origin_digest == digest_origin(channel.base_url))
            members = group(session, channel)
            operations = list(session.scalars(select(MemoryOperation).where(MemoryOperation.policy_id == policy.id).order_by(MemoryOperation.created_at.desc(), MemoryOperation.id).limit(20))) if policy else []
            return {"id": public_id("mmp", policy.id) if policy else None, "channel_id": public_id("ch", channel.id), "channel_ids": [public_id("ch", row.id) for row in members],
                    "enabled": bool(policy and policy.enabled and current_origin), "idle_seconds": policy.idle_seconds if policy else 600, "revision": policy.revision if policy else 0,
                    "status": policy.status if current_origin and policy else "channel_changed" if policy else "not_configured", "idle_since": timestamp(policy.idle_since) if policy and current_origin else None,
                    "observed_at": timestamp(policy.observed_at) if policy else None, "released": bool(policy and policy.released),
                    "active_operation_id": public_id("mmo", policy.active_operation_id) if policy and policy.active_operation_id else None,
                    "operations": [{"id": public_id("mmo", row.id), "stage": row.stage, "created_at": timestamp(row.created_at), "updated_at": timestamp(row.updated_at), "completed_at": timestamp(row.completed_at), "error_code": row.error_code, "evidence": row.evidence} for row in operations]}

    def patch(self, actor: AdminIdentity, identifier: UUID, data: MemoryPolicyPatch, token: str, request_id: str) -> dict[str, Any]:
        # New policies use revision=1 in request; absent view revision=0 is a UI marker.
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "memory.policy:" + str(identifier), token, data.model_dump())
            if idem.result is not None:
                return idem.result
            channel = session.execute(select(Channel).where(Channel.id == identifier).with_for_update()).scalar_one_or_none()
            if not channel or channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            digest = digest_origin(channel.base_url)
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 624087031))"), {"key": digest})
            policy = session.scalar(select(MemoryPolicy).where(MemoryPolicy.origin_digest == digest).with_for_update())
            if policy is None:
                policy = session.scalar(select(MemoryPolicy).where(MemoryPolicy.channel_id == channel.id).with_for_update())
                if policy and policy.active_operation_id:
                    raise DomainError("memory_active_on_old_origin", "原地址的回收操作尚未核对，保留保护", 409)
            if policy is None:
                if (session.scalar(select(func.count()).select_from(MemoryPolicy)) or 0) >= 100:
                    raise DomainError("memory_policy_limit", "回收策略数量已达上限", 409)
                require_revision(1, data.expected_revision)
                policy = MemoryPolicy(channel_id=channel.id, origin_digest=digest, revision=1)
                session.add(policy)
                session.flush()
            else:
                require_revision(policy.revision, data.expected_revision)
                if policy.origin_digest != digest:
                    policy.origin_digest = digest
                    policy.activity_marker, policy.history_marker = "", ""
            policy.enabled, policy.idle_seconds = data.enabled, data.idle_seconds
            policy.revision += 1
            policy.idle_since, policy.released = None, False
            policy.next_check_at = utcnow()
            if not policy.active_operation_id:
                policy.status = "observing" if data.enabled else "disabled"
            record_change(session, public_id("usr", actor.user_id), "memory.policy_updated", policy.id, request_id, revision_before=data.expected_revision, revision_after=policy.revision)
            idem.result = {"id": public_id("mmp", policy.id), "revision": policy.revision, "enabled": policy.enabled, "idle_seconds": policy.idle_seconds}
            return idem.result


class MemoryReclaimer:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings, *, http_factory: Any = ComfyMemoryHttp) -> None:
        self.sessions, self.http_factory = sessions, http_factory
        self.vault = SecretVault(settings.encryption_key.get_secret_value())
        self.owner = uuid4()

    @staticmethod
    def _lock_dispatch(session: Session) -> None:
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DISPATCH_LOCK})

    @staticmethod
    def _gpus(session: Session, members: list[Channel]) -> list[Gpu]:
        return list(session.scalars(select(Gpu).where(Gpu.id.in_({row.gpu_id for row in members})).order_by(Gpu.id).with_for_update()))

    @staticmethod
    def _activity(session: Session, members: list[Channel], now: datetime, *, owned: UUID | None = None) -> tuple[bool, str]:
        gpus = list(session.scalars(select(Gpu).where(Gpu.id.in_({row.gpu_id for row in members}))))
        nodes = list(session.scalars(select(Node).where(Node.id.in_({row.node_id for row in members}))))
        blocked = not gpus or not nodes or any(not node.enabled or not node.current_boot_id or not node.observed_at or node.observed_at < now - timedelta(seconds=15) for node in nodes)
        blocked = blocked or any(row.management_hold not in (None, owned) for row in gpus)
        blocked = blocked or bool(session.scalar(select(GpuOccupancy.gpu_id).where(GpuOccupancy.gpu_id.in_([row.id for row in gpus])).limit(1)))
        channel_ids = [public_id("ch", row.id) for row in members]
        related = or_(*(Job.eligible_channels.contains([key]) for key in channel_ids))
        blocked = blocked or bool(session.scalar(select(Job.id).where(related, Job.state.not_in(TERMINAL)).limit(1)))
        blocked = blocked or bool(session.scalar(select(Attempt.id).where(Attempt.gpu_id.in_([row.id for row in gpus]), Attempt.generation_released_at.is_(None)).limit(1)))
        latest = session.scalar(select(func.max(Attempt.created_at)).where(Attempt.gpu_id.in_([row.id for row in gpus])))
        boots = sorted(str(node.current_boot_id) for node in nodes)
        return blocked, fingerprint({"latest_attempt": latest.isoformat() if latest else None, "boots": boots})

    def tick(self, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        operation = self._claim(now)
        if operation:
            self._advance(operation, now)
            return {"memory_operation_steps": 1}
        with self.sessions.begin() as session:
            policy = session.scalar(select(MemoryPolicy).where(MemoryPolicy.enabled.is_(True), MemoryPolicy.active_operation_id.is_(None), or_(MemoryPolicy.next_check_at.is_(None), MemoryPolicy.next_check_at <= now)).order_by(MemoryPolicy.next_check_at.asc().nullsfirst(), MemoryPolicy.id).with_for_update(skip_locked=True).limit(1))
            if not policy:
                return {"memory_operation_steps": 0}
            policy.next_check_at = now + timedelta(seconds=10)
            identifier, revision = policy.id, policy.revision
            channel = session.get(Channel, policy.channel_id)
            if not channel or channel.deleted_at or digest_origin(channel.base_url) != policy.origin_digest:
                policy.status, policy.idle_since = "channel_changed", None
                return {"memory_operation_steps": 0}
            members = group(session, channel)
            blocked, activity = self._activity(session, members, now)
            if blocked:
                policy.idle_since, policy.released, policy.status = None, False, "busy_or_unknown"
                policy.activity_marker = activity
                return {"memory_operation_steps": 0}
            base, auth = channel.base_url, self.vault.decrypt(channel.encrypted_auth) if channel.encrypted_auth else None
        try:
            http = self.http_factory(base, auth)
            stats = http.call("GET", "/system_stats")
            queue = http.call("GET", "/queue")
            history = http.call("GET", "/history?max_items=1")
            busy = bool(queue_ids(queue))
            history_marker = fingerprint(sorted(history.keys()))
            supported = stats.get("system", {}).get("comfyui_version") in SUPPORTED_VERSIONS
        except Exception:
            with self.sessions.begin() as session:
                policy = session.get(MemoryPolicy, identifier, with_for_update=True)
                if policy and policy.revision == revision and not policy.active_operation_id:
                    policy.status, policy.idle_since = "observation_unknown", None
            return {"memory_operation_steps": 0}
        with self.sessions.begin() as session:
            self._lock_dispatch(session)
            policy = session.get(MemoryPolicy, identifier, with_for_update=True)
            if not policy or policy.revision != revision or not policy.enabled or policy.active_operation_id:
                return {"memory_operation_steps": 0}
            channel = session.get(Channel, policy.channel_id)
            if channel is None or digest_origin(channel.base_url) != policy.origin_digest:
                policy.status, policy.idle_since = "channel_changed", None
                return {"memory_operation_steps": 0}
            members = group(session, channel)
            gpus = self._gpus(session, members)
            blocked, activity = self._activity(session, members, now)
            changed = activity != policy.activity_marker or history_marker != policy.history_marker
            policy.observed_at, policy.activity_marker, policy.history_marker = now, activity, history_marker
            if blocked or busy or not supported:
                policy.status = "busy_or_unknown" if blocked or busy else "unsupported_comfy_version"
                policy.idle_since, policy.released = None, False
            elif changed or policy.idle_since is None:
                policy.idle_since, policy.released, policy.status = now, False, "idle_countdown"
            elif not policy.released and (now - policy.idle_since).total_seconds() >= policy.idle_seconds:
                identifier = uuid4()
                boots = {str(row.node_id): str(required(session.get(Node, row.node_id)).current_boot_id) for row in members}
                evidence = {"gpu_ids": [str(row.id) for row in gpus], "channel_ids": [str(row.id) for row in members], "boots": boots,
                            "origin_digest": policy.origin_digest, "before": memory_snapshot(stats), "comfyui_version": stats["system"]["comfyui_version"], "barrier1_association": str(uuid4()), "barrier2_association": str(uuid4())}
                session.add(MemoryOperation(id=identifier, policy_id=policy.id, stage="preflight", updated_at=now, evidence=evidence))
                for gpu in gpus:
                    gpu.management_hold = identifier
                policy.active_operation_id, policy.status = identifier, "releasing"
                record_change(session, "memory-runner", "memory.release_started", identifier, str(identifier), revision_before=None, revision_after=None)
        return {"memory_operation_steps": 0}

    def _claim(self, now: datetime) -> UUID | None:
        with self.sessions.begin() as session:
            operation = session.scalar(select(MemoryOperation).where(MemoryOperation.stage.not_in(DONE), MemoryOperation.stage.not_in({"unknown", "failed"}), or_(MemoryOperation.lease_until.is_(None), MemoryOperation.lease_until <= now)).order_by(MemoryOperation.updated_at, MemoryOperation.id).with_for_update(skip_locked=True).limit(1))
            if not operation:
                return None
            if operation.stage == "free_intent":
                operation.stage, operation.error_code = "unknown", "free_outcome_unknown"
                required(session.get(MemoryPolicy, operation.policy_id)).status = "release_unknown"
                return None
            operation.owner, operation.lease_until = self.owner, now + timedelta(seconds=90)
            return operation.id

    def _advance(self, identifier: UUID, now: datetime) -> None:
        with self.sessions() as session:
            operation = session.get(MemoryOperation, identifier)
            if not operation or operation.owner != self.owner:
                return
            policy = required(session.get(MemoryPolicy, operation.policy_id))
            channel = session.get(Channel, policy.channel_id)
            if not channel or digest_origin(channel.base_url) != operation.evidence["origin_digest"]:
                self._update(identifier, now, "unknown", error="channel_changed_during_release")
                return
            stage, evidence = operation.stage, dict(operation.evidence)
            if (now - operation.created_at).total_seconds() > 900:
                self._update(identifier, now, "unknown", error="release_observation_timeout")
                return
            http = self.http_factory(channel.base_url, self.vault.decrypt(channel.encrypted_auth) if channel.encrypted_auth else None)
            boots_valid = all(str(required(session.get(Node, UUID(node))).current_boot_id) == boot for node, boot in evidence["boots"].items())
        if not boots_valid:
            self._update(identifier, now, "unknown", error="worker_boot_changed")
            return
        try:
            if stage == "preflight":
                # Second check after the durable GPU hold; newly queued requests win.
                queue = http.call("GET", "/queue")
                if queue_ids(queue):
                    self._finish(identifier, now, "skipped", "comfy_busy")
                    return
                for node in ("EmptyImage", "PreviewImage"):
                    if node not in http.call("GET", "/object_info/" + node):
                        self._finish(identifier, now, "skipped", "barrier_unavailable")
                        return
                with self.sessions.begin() as session:
                    self._lock_dispatch(session)
                    op = required(session.get(MemoryOperation, identifier, with_for_update=True))
                    policy = required(session.get(MemoryPolicy, op.policy_id))
                    members = group(session, required(session.get(Channel, policy.channel_id)))
                    gpus = self._gpus(session, members)
                    blocked, _ = self._activity(session, members, now, owned=identifier)
                    safe = all(row.management_hold == identifier for row in gpus) and not blocked and policy.enabled
                    if safe:
                        op.stage, op.updated_at = "free_intent", now
                if not safe:
                    self._finish(identifier, now, "skipped", "new_activity")
                    return
                # Intent committed before I/O. Never retry an uncertain /free POST.
                http.call("POST", "/free", {"unload_models": True, "free_memory": True})
                self._update(identifier, now, "free_accepted", evidence={"free_acknowledged_at": now.isoformat()})
            elif stage in {"free_accepted", "barrier1_intent", "barrier1_wait", "barrier2_intent", "barrier2_wait"}:
                number = 2 if stage.startswith("barrier2") or stage == "barrier1_wait" and evidence.get("barrier1_completed") else 1
                key = "barrier" + str(number)
                prompt_id = evidence.get(key + "_prompt_id")
                if not prompt_id and stage.endswith("_intent"):
                    # Lost POST responses are reconciled by our exact association, never resubmitted.
                    prompt_id = self._find(http, evidence[key + "_association"])
                    if not prompt_id:
                        self._update(identifier, now, "unknown", error="barrier_submission_unknown")
                        return
                if not prompt_id:
                    self._update(identifier, now, key + "_intent", release_lease=False)
                    association = evidence[key + "_association"]
                    result = http.call("POST", "/prompt", {"prompt": BARRIER, "client_id": association, "extra_data": {"render_control_memory": association}})
                    prompt_id = result.get("prompt_id")
                    if not isinstance(prompt_id, str) or len(prompt_id) > 128:
                        raise ValueError("invalid prompt identifier")
                    self._update(identifier, now, key + "_wait", evidence={key + "_prompt_id": prompt_id})
                    return
                history = http.call("GET", "/history/" + prompt_id).get(prompt_id)
                if not history:
                    self._update(identifier, now, key + "_wait", evidence={key + "_prompt_id": prompt_id})
                    return
                if not history.get("status", {}).get("completed") or history.get("status", {}).get("status_str") != "success":
                    self._update(identifier, now, "failed", error="barrier_execution_failed")
                    return
                if prompt_id in queue_ids(http.call("GET", "/queue")):
                    self._update(identifier, now, key + "_wait")
                    return
                if number == 1:
                    self._update(identifier, now, "barrier1_wait", evidence={"barrier1_prompt_id": prompt_id, "barrier1_completed": True})
                else:
                    after = memory_snapshot(http.call("GET", "/system_stats"))
                    self._finish(identifier, now, "completed", evidence={"barrier2_prompt_id": prompt_id, "barrier2_completed": True, "after": after,
                        "proof": "free_ack_then_two_ordered_cpu_barriers", "memory_result": self._delta(evidence["before"], after)})
        except Exception:
            # Reads may be retried; once a POST intent exists its result is uncertain.
            with self.sessions() as session:
                actual = session.get(MemoryOperation, identifier)
                current = actual.stage if actual else "unknown"
            if current == "free_intent":
                self._update(identifier, now, "unknown", error="http_write_outcome_unknown")
            elif current in {"barrier1_intent", "barrier2_intent"}:
                self._update(identifier, now, current, error="barrier_requires_reconciliation")
            elif (now - operation.created_at).total_seconds() > 900:
                self._update(identifier, now, "unknown", error="release_observation_timeout")
            else:
                self._update(identifier, now, current, error="observation_retry")

    @staticmethod
    def _delta(before: dict[str, Any], after: dict[str, Any]) -> str:
        pairs = [(a.get("vram_free"), b.get("vram_free")) for a, b in zip(before["devices"], after["devices"], strict=False)]
        pairs.append((before.get("ram_free"), after.get("ram_free")))
        return "increase_observed" if any(isinstance(a, int) and isinstance(b, int) and b > a for a, b in pairs) else "no_increase_observed"

    @staticmethod
    def _find(http: Any, association: str) -> str | None:
        queue = http.call("GET", "/queue")
        queue_ids(queue)
        found = []
        for row in queue["queue_running"] + queue["queue_pending"]:
            if len(row) > 3 and isinstance(row[3], dict) and row[3].get("render_control_memory") == association:
                found.append(row[1])
        history = http.call("GET", "/history?max_items=100")
        for key, value in history.items():
            prompt = value.get("prompt", [])
            if len(prompt) > 3 and isinstance(prompt[3], dict) and prompt[3].get("render_control_memory") == association:
                found.append(key)
        unique = set(found)
        return unique.pop() if len(unique) == 1 else None

    def _update(self, identifier: UUID, now: datetime, stage: str, *, evidence: dict[str, Any] | None = None, error: str | None = None, release_lease: bool = True) -> None:
        with self.sessions.begin() as session:
            op = session.get(MemoryOperation, identifier, with_for_update=True)
            if not op or op.owner != self.owner or op.lease_until is None or op.lease_until < now:
                return
            op.stage, op.updated_at, op.error_code = stage, now, error
            op.evidence = {**op.evidence, **(evidence or {})}
            if release_lease:
                op.lease_until = None
            if stage in {"unknown", "failed"}:
                required(session.get(MemoryPolicy, op.policy_id)).status = "release_" + stage

    def _finish(self, identifier: UUID, now: datetime, stage: str, error: str | None = None, *, evidence: dict[str, Any] | None = None) -> None:
        with self.sessions.begin() as session:
            self._lock_dispatch(session)
            op = session.get(MemoryOperation, identifier, with_for_update=True)
            if not op or op.owner != self.owner or op.lease_until is None or op.lease_until < now:
                return
            gpus = list(session.scalars(select(Gpu).where(Gpu.id.in_([UUID(value) for value in op.evidence["gpu_ids"]])).order_by(Gpu.id).with_for_update()))
            if any(row.management_hold != op.id for row in gpus):
                op.stage, op.error_code = "unknown", "hold_ownership_changed"
                required(session.get(MemoryPolicy, op.policy_id)).status = "release_unknown"
                return
            for gpu in gpus:
                gpu.management_hold = None
            op.stage, op.completed_at, op.updated_at, op.error_code, op.lease_until = stage, now, now, error, None
            op.evidence = {**op.evidence, **(evidence or {})}
            policy = required(session.get(MemoryPolicy, op.policy_id))
            policy.active_operation_id = None
            policy.status = "released" if stage == "completed" else "idle_countdown"
            policy.released = stage == "completed"
            policy.idle_since = now
            # Our own two barrier histories must not count as new user activity.
            if stage == "completed":
                policy.history_marker = fingerprint([op.evidence["barrier2_prompt_id"]])
            record_change(session, "memory-runner", "memory.release_" + stage, op.id, str(op.id), revision_before=None, revision_after=None)
            session.execute(delete(MemoryOperation).where(MemoryOperation.policy_id == policy.id, MemoryOperation.stage.in_(DONE), MemoryOperation.completed_at < now - timedelta(days=30)))
