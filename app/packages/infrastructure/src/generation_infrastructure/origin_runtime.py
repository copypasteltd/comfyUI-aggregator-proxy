"""Bounded upstream observations and safe reclamation of native-panel holds."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from generation_domain.errors import DomainError
from generation_domain.values import utcnow
from sqlalchemy import select, text

from generation_infrastructure.dispatch import DISPATCH_LOCK
from generation_infrastructure.memory_reclamation import group
from generation_infrastructure.models import (
    ApiKey,
    Channel,
    Gpu,
    OriginGrant,
    OriginPrompt,
    OriginReservation,
)
from generation_infrastructure.origin_access import LIVE, OriginAccess, configuration


def bounded_json(
    base: str, path: str, authorization: str | None, *, maximum: int = 16 * 1024 * 1024
) -> Any:
    headers = {"Accept-Encoding": "identity"}
    if authorization:
        headers["Authorization"] = authorization
    with httpx.Client(
        timeout=httpx.Timeout(4, connect=2), trust_env=False, follow_redirects=False
    ) as client:
        with client.stream("GET", base.rstrip("/") + path, headers=headers) as response:
            if response.status_code != 200:
                raise DomainError("origin_source_unavailable", "设备暂不可访问", 502)
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > maximum:
                    raise DomainError("origin_source_limit", "设备响应超过读取上限", 502)
    return json.loads(data)


def queue_entries(queue: Any) -> list[list[Any]]:
    if not isinstance(queue, dict) or any(
        not isinstance(queue.get(name), list) for name in ("queue_running", "queue_pending")
    ):
        raise DomainError("origin_queue_unknown", "设备队列无法核对", 502)
    entries = queue["queue_running"] + queue["queue_pending"]
    if len(entries) > 2000 or any(
        not isinstance(entry, list) or len(entry) < 4 or not isinstance(entry[1], str)
        for entry in entries
    ):
        raise DomainError("origin_queue_unknown", "设备队列无法核对", 502)
    return entries


def association_of(entry: Any) -> str | None:
    if not isinstance(entry, list) or len(entry) < 4 or not isinstance(entry[3], dict):
        return None
    control = entry[3].get("origin_control")
    return control.get("association_id") if isinstance(control, dict) else None


def output_files(value: Any) -> list[tuple[str, str, str]]:
    result = []
    pending = [value]
    visited = 0
    while pending and visited < 20000:
        row = pending.pop()
        visited += 1
        if isinstance(row, dict):
            name, folder, kind = (
                row.get("filename"),
                row.get("subfolder", ""),
                row.get("type", "output"),
            )
            if (
                isinstance(name, str)
                and isinstance(folder, str)
                and kind in {"input", "output", "temp"}
                and 0 < len(name) <= 255
                and len(folder) <= 1024
                and "/" not in name
                and "\\" not in name
                and ":" not in name + folder
                and not any(ord(c) < 32 for c in name + folder)
                and name not in {".", ".."}
                and all(part not in {".", ".."} for part in folder.replace("\\", "/").split("/"))
                and not folder.startswith(("/", "\\"))
            ):
                result.append((name, folder, kind))
            pending.extend(row.values())
        elif isinstance(row, list):
            pending.extend(row)
    return result[:1000]


class OriginReconciler(OriginAccess):
    def reconcile(self, identifier: UUID) -> dict[str, Any]:
        now = utcnow()
        with self.sessions.begin() as db:
            reservation = db.get(OriginReservation, identifier)
            if reservation is None or reservation.state not in LIVE:
                return {"state": "closed"}
            channel = db.get(Channel, reservation.channel_id)
            if (
                not channel
                or channel.deleted_at
                or reservation.config_digest != configuration(channel, group(db, channel))
            ):
                reservation.state, reservation.error_code, reservation.updated_at = (
                    "unknown",
                    "origin_configuration_changed",
                    now,
                )
                return {"state": "unknown"}
            base, authorization = (
                channel.base_url,
                self.vault.decrypt(channel.encrypted_auth) if channel.encrypted_auth else None,
            )
            pending = [
                (row.id, row.association, row.prompt_id)
                for row in db.scalars(
                    select(OriginPrompt)
                    .where(
                        OriginPrompt.reservation_id == identifier,
                        OriginPrompt.state.in_(("intent", "submitted", "unknown")),
                    )
                    .order_by(OriginPrompt.created_at)
                    .limit(32)
                )
            ]
            key_id, channel_id = reservation.key_id, reservation.channel_id
        # Queue reads cannot establish absence of a previously uncertain submit.
        # Association recovery is authoritative only on a positive queue/history match.
        try:
            queue = bounded_json(base, "/queue", authorization)
            entries = queue_entries(queue)
            histories: dict[str, Any] = {}
            if any(not item[2] for item in pending):
                histories = bounded_json(base, "/history?max_items=128", authorization)
                if not isinstance(histories, dict):
                    raise ValueError("Invalid source history")
            resolved: list[tuple[UUID, str | None, str, Any]] = []
            for prompt_pk, association, prompt_id in pending:
                if not prompt_id:
                    matched = next(
                        (entry[1] for entry in entries if association_of(entry) == association),
                        None,
                    )
                    matched = matched or next(
                        (
                            pid
                            for pid, row in histories.items()
                            if isinstance(row, dict)
                            and association_of(row.get("prompt")) == association
                        ),
                        None,
                    )
                    prompt_id = matched
                if not prompt_id:
                    resolved.append((prompt_pk, None, "unknown", None))
                    continue
                if any(entry[1] == prompt_id for entry in entries):
                    resolved.append((prompt_pk, prompt_id, "submitted", None))
                    continue
                history = bounded_json(base, "/history/" + prompt_id, authorization)
                history_row = history.get(prompt_id) if isinstance(history, dict) else None
                if (
                    isinstance(history_row, dict)
                    and isinstance(history_row.get("status"), dict)
                    and history_row["status"].get("completed") is True
                ):
                    resolved.append(
                        (prompt_pk, prompt_id, "completed", history_row.get("outputs", {}))
                    )
                else:
                    resolved.append((prompt_pk, prompt_id, "unknown", None))
        except (httpx.HTTPError, DomainError, ValueError, TypeError):
            with self.sessions.begin() as db:
                row = db.get(OriginReservation, identifier)
                if row and row.state in LIVE:
                    row.error_code, row.updated_at = "origin_source_unavailable", utcnow()
            return {"state": "unknown"}
        for _, _, _, outputs in resolved:
            if outputs is not None:
                for filename, subfolder, file_type in output_files(outputs):
                    self.remember_file(key_id, channel_id, filename, subfolder, file_type)
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DISPATCH_LOCK})
            reservation = db.execute(
                select(OriginReservation)
                .where(OriginReservation.id == identifier)
                .with_for_update()
            ).scalar_one()
            channel = db.get(Channel, reservation.channel_id)
            gpus = list(
                db.scalars(
                    select(Gpu)
                    .where(Gpu.id.in_([UUID(value) for value in reservation.gpu_ids]))
                    .order_by(Gpu.id)
                    .with_for_update()
                )
            )
            if reservation.state not in LIVE or not channel:
                return {"state": reservation.state}
            if reservation.config_digest != configuration(channel, group(db, channel)) or any(
                gpu.management_hold != reservation.id for gpu in gpus
            ):
                reservation.state, reservation.error_code = (
                    "unknown",
                    "origin_configuration_changed",
                )
                return {"state": reservation.state}
            for prompt_pk, prompt_id, state, _ in resolved:
                prompt = db.get(OriginPrompt, prompt_pk)
                if prompt and prompt.state in {"intent", "submitted", "unknown"}:
                    prompt.prompt_id, prompt.state, prompt.updated_at = prompt_id, state, now
                    if prompt_id and prompt.response is None:
                        prompt.response = {"prompt_id": prompt_id, "node_errors": {}}
            key, grant = db.get(ApiKey, reservation.key_id), db.get(OriginGrant, reservation.key_id)
            if (
                reservation.expires_at <= now
                or not key
                or not key.enabled
                or key.deleted_at
                or not grant
                or not grant.enabled
                or (grant and "ch_" + reservation.channel_id.hex not in grant.channel_ids)
            ):
                reservation.state = "closing"
            uncertain = bool(
                db.scalar(
                    select(OriginPrompt.id)
                    .where(
                        OriginPrompt.reservation_id == identifier,
                        OriginPrompt.state.in_(("intent", "submitted", "unknown")),
                    )
                    .limit(1)
                )
            )
            abandoned_acquisition = (
                reservation.state == "acquiring"
                and reservation.created_at + timedelta(seconds=60) <= now
            )
            if (
                (reservation.state in {"closing", "unknown"} or abandoned_acquisition)
                and not uncertain
                and not entries
            ):
                for gpu in gpus:
                    gpu.management_hold = None
                reservation.state, reservation.error_code = "closed", None
            reservation.updated_at = now
            return {
                "state": reservation.state,
                "queue_running": len(queue["queue_running"]),
                "queue_pending": len(queue["queue_pending"]),
            }

    def tick(self) -> dict[str, int]:
        with self.sessions() as db:
            identifiers = list(
                db.scalars(
                    select(OriginReservation.id)
                    .where(
                        OriginReservation.state.in_(LIVE),
                        OriginReservation.updated_at <= utcnow() - timedelta(seconds=5),
                    )
                    .order_by(OriginReservation.updated_at)
                    .limit(2)
                )
            )
        for identifier in identifiers:
            self.reconcile(identifier)
        return {"origin_reconciled": len(identifiers)}
