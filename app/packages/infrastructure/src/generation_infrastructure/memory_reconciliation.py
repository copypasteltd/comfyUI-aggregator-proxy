"""Explicit, audited reconciliation of an unknown memory-maintenance hold.

Never retry /free, force-unlock a GPU, or claim an unacknowledged unload succeeded.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from generation_infrastructure.database import make_sessions
from generation_infrastructure.memory_reclamation import (
    BARRIER,
    SUPPORTED_VERSIONS,
    MemoryReclaimer,
    digest_origin,
    group,
    memory_snapshot,
    queue_ids,
    required,
)
from generation_infrastructure.models import (
    Channel,
    MemoryOperation,
    MemoryPolicy,
    Node,
    OriginReservation,
)
from generation_infrastructure.settings import Settings
from generation_infrastructure.transactions import record_change

KEY = "manual_reconciliation"
PROOF = "unknown_free_followed_by_two_ordered_cpu_barriers"


def check(value: Any, code: str) -> None:
    if not value:
        raise DomainError(code, "维护核对条件未满足，继续保留原占用", 409)


class MemoryReconciler(MemoryReclaimer):
    """Own only the named maintenance operation, never customer generation."""

    def _guard(self, session: Session, identifier: UUID, *, claim: bool = False) -> tuple[Any, ...]:
        now = utcnow()
        self._lock_dispatch(session)
        op = required(session.get(MemoryOperation, identifier, with_for_update=True))
        policy = required(session.get(MemoryPolicy, op.policy_id, with_for_update=True))
        channel = required(session.get(Channel, policy.channel_id))
        check(op.stage == "unknown", "memory_reconcile_not_unknown")
        check(policy.active_operation_id == op.id, "memory_reconcile_not_active")
        check(not channel.deleted_at and digest_origin(channel.base_url) == policy.origin_digest == op.evidence.get("origin_digest"), "memory_reconcile_origin_changed")
        members = group(session, channel)
        gpus = self._gpus(session, members)
        check(sorted(str(row.id) for row in members) == sorted(op.evidence.get("channel_ids", [])), "memory_reconcile_group_changed")
        check(sorted(str(row.id) for row in gpus) == sorted(op.evidence.get("gpu_ids", [])), "memory_reconcile_group_changed")
        check(gpus and all(row.management_hold == op.id for row in gpus), "memory_reconcile_hold_changed")
        check(not self._activity(session, members, now, owned=identifier)[0], "memory_reconcile_activity")
        live = session.scalar(select(OriginReservation.id).where(
            OriginReservation.state.in_(("acquiring", "active", "closing", "unknown")),
            or_(*(OriginReservation.gpu_ids.contains([str(row.id)]) for row in gpus)),
        ).limit(1))
        check(not live, "memory_reconcile_native_reservation")
        nodes = list(session.scalars(select(Node).where(Node.id.in_({row.node_id for row in members}))))
        boots = {str(row.id): str(row.current_boot_id) for row in nodes}
        rec = op.evidence.get(KEY)
        if rec:
            check(rec.get("boots") == boots, "memory_reconcile_boot_changed")
            check(now.timestamp() - rec["started_at"] <= 900, "memory_reconcile_deadline")
        if claim:
            check(op.owner == self.owner or op.lease_until is None or op.lease_until <= now, "memory_reconcile_leased")
            op.owner, op.lease_until, op.updated_at = self.owner, now + timedelta(seconds=90), now
            if not rec:
                rec = {"protocol": "memory-reconcile-v1", "started_at": int(now.timestamp()), "boots": boots,
                       "previous_error": op.error_code, "free_retried": False, "release_verified": False}
                op.evidence = {**op.evidence, KEY: rec}
                record_change(session, "memory-maintenance-cli", "memory.reconcile_started", op.id, str(self.owner), {"protocol": rec["protocol"]})
        else:
            check(op.owner == self.owner and op.lease_until and op.lease_until > now, "memory_reconcile_lease_lost")
        return op, policy, channel, members, gpus, boots

    def _save(self, identifier: UUID, changes: dict[str, Any]) -> None:
        with self.sessions.begin() as session:
            op, *_ = self._guard(session, identifier)
            op.evidence = {**op.evidence, KEY: {**op.evidence[KEY], **changes}}
            op.updated_at = utcnow()

    @staticmethod
    def _history(http: Any, identifier: str, association: str) -> bool:
        history = http.call("GET", "/history/" + identifier).get(identifier)
        if not history:
            return False
        prompt = history.get("prompt", [])
        check(len(prompt) > 3 and prompt[1] == identifier and prompt[2] == BARRIER
              and isinstance(prompt[3], dict) and prompt[3].get("render_control_memory") == association,
              "memory_reconcile_barrier_mismatch")
        check(history.get("status", {}).get("completed") is True
              and history["status"].get("status_str") == "success", "memory_reconcile_barrier_failed")
        return True

    def inspect(self, identifier: UUID) -> dict[str, Any]:
        """Default CLI operation; no HTTP writes and no persisted mutations."""
        with self.sessions.begin() as session:
            op = required(session.get(MemoryOperation, identifier))
            policy = required(session.get(MemoryPolicy, op.policy_id))
            channel = required(session.get(Channel, policy.channel_id))
            return {"operation_id": public_id("mmo", op.id), "stage": op.stage,
                    "error_code": op.error_code, "active": policy.active_operation_id == op.id,
                    "recorded_gpu_count": len(op.evidence.get("gpu_ids", [])),
                    "origin_matches": digest_origin(channel.base_url) == op.evidence.get("origin_digest"),
                    "reconciliation": op.evidence.get(KEY)}

    def step(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            op = required(session.get(MemoryOperation, identifier))
            if op.stage == "skipped" and op.evidence.get(KEY, {}).get("proof") == PROOF:
                return self.inspect(identifier)
            op, _, channel, *_ = self._guard(session, identifier, claim=True)
            rec = dict(op.evidence[KEY])
            authorization = self.vault.decrypt(channel.encrypted_auth) if channel.encrypted_auth else None
            base = channel.base_url
        http = self.http_factory(base, authorization)
        stats = http.call("GET", "/system_stats")
        check(stats.get("system", {}).get("comfyui_version") in SUPPORTED_VERSIONS, "memory_reconcile_version")
        for number in (1, 2):
            key = "barrier" + str(number)
            if rec.get(key + "_completed_at"):
                continue
            prompt_id, association = rec.get(key + "_prompt_id"), rec.get(key + "_association")
            if not prompt_id:
                if association:
                    # An already committed intent is NEVER submitted again.
                    prompt_id = self._find(http, association)
                    check(prompt_id, "memory_reconcile_submission_unknown")
                else:
                    check(not queue_ids(http.call("GET", "/queue")), "memory_reconcile_comfy_busy")
                    for node in ("EmptyImage", "PreviewImage"):
                        check(node in http.call("GET", "/object_info/" + node), "memory_reconcile_nodes_missing")
                    association = str(uuid4())
                    self._save(identifier, {key + "_association": association, key + "_intent_at": utcnow().isoformat()})
                    receipt = http.call("POST", "/prompt", {"prompt": BARRIER, "client_id": association,
                                                            "extra_data": {"render_control_memory": association}})
                    prompt_id = receipt.get("prompt_id")
                # Stock ComfyUI issues UUID prompt IDs; never put arbitrary paths in a read.
                check(isinstance(prompt_id, str) and str(UUID(prompt_id)) == prompt_id, "memory_reconcile_prompt_id")
                self._save(identifier, {key + "_prompt_id": prompt_id})
                return {"stage": "pending", "barrier": number}
            if not isinstance(association, str):
                raise DomainError("memory_reconcile_association_missing", "维护关联记录缺失，保留占用", 409)
            if not self._history(http, prompt_id, association):
                return {"stage": "pending", "barrier": number}
            check(not queue_ids(http.call("GET", "/queue")), "memory_reconcile_comfy_busy")
            self._save(identifier, {key + "_completed_at": utcnow().isoformat()})
            return {"stage": "pending", "barrier": number}
        # Re-read both exact histories before the final ownership transaction.
        for number in (1, 2):
            key = "barrier" + str(number)
            check(self._history(http, rec[key + "_prompt_id"], rec[key + "_association"]), "memory_reconcile_history_missing")
        check(rec["barrier1_completed_at"] <= rec["barrier2_intent_at"], "memory_reconcile_order")
        check(not queue_ids(http.call("GET", "/queue")), "memory_reconcile_comfy_busy")
        with self.sessions.begin() as session:
            op, policy, _, members, gpus, _ = self._guard(session, identifier)
            now = utcnow()
            rec = {**op.evidence[KEY], "proof": PROOF, "completed_at": now.isoformat(), "after": memory_snapshot(stats)}
            op.evidence = {**op.evidence, KEY: rec}
            op.stage, op.completed_at, op.updated_at, op.lease_until = "skipped", now, now, None
            for gpu in gpus:
                gpu.management_hold = None
            policy.active_operation_id, policy.released = None, False
            policy.status = "idle_countdown" if policy.enabled else "disabled"
            policy.idle_since, policy.observed_at, policy.next_check_at = now, now, now + timedelta(seconds=10)
            policy.activity_marker = self._activity(session, members, now, owned=identifier)[1]
            policy.history_marker = fingerprint([rec["barrier2_prompt_id"]])
            record_change(session, "memory-maintenance-cli", "memory.reconcile_completed", op.id, str(self.owner),
                          {"proof": PROOF, "release_verified": False, "free_retried": False})
        return self.inspect(identifier)

    def relinquish(self, identifier: UUID) -> None:
        with self.sessions.begin() as session:
            op = session.get(MemoryOperation, identifier, with_for_update=True)
            if op and op.owner == self.owner:
                op.lease_until = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--timeout", type=int, default=40, choices=range(1, 121))
    args = parser.parse_args()
    identifier = parse_id("mmo", args.operation)
    settings = Settings()
    service = MemoryReconciler(make_sessions(settings), settings)
    try:
        if not args.apply:
            print(json.dumps(service.inspect(identifier)))
            return
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            result = service.step(identifier)
            if result["stage"] == "skipped":
                print(json.dumps(result))
                return
            time.sleep(1)
        raise DomainError("memory_reconcile_wait_timeout", "核对尚未完成，保留维护占用及原提交标识", 409)
    except Exception as error:
        print(json.dumps({"status": "hold_preserved", "error_code": error.code if isinstance(error, DomainError) else "memory_reconcile_observation_failed"}))
        raise SystemExit(1) from None
    finally:
        if args.apply:
            service.relinquish(identifier)


if __name__ == "__main__":
    main()
