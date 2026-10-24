"""Redacted access metadata is observational; it never decides task outcomes."""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import struct
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from generation_contracts.access_events import AccessCompletion, AccessResult
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from sqlalchemy import and_, create_engine, delete, func, literal, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.elements import ColumnElement

from generation_infrastructure.models import AccessEvent, ApiKey
from generation_infrastructure.settings import Settings

logger = logging.getLogger("render_control")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
CURSOR = struct.Struct(">B16sqq16sB")
FILTERS: tuple[AccessResult, ...] = ("all", "success", "denied", "error")


@dataclass(frozen=True)
class AccessSubject:
    tenant_id: UUID
    project_id: UUID
    key_id: UUID
    client_ip: str | None
    gateway_id: UUID | None = None


@dataclass(frozen=True)
class AccessRecord:
    request_id: UUID
    started_at: datetime
    tenant_id: UUID | None
    project_id: UUID | None
    key_id: UUID | None
    gateway_id: UUID | None
    entry: str
    method: str
    route: str
    peer_ip: str | None
    client_ip: str | None
    status_code: int | None
    error_code: str | None
    completion: AccessCompletion
    duration_milliseconds: int
    request_bytes: int
    response_bytes: int


class AccessEventWriter:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True, poolclass=NullPool, connect_args={"connect_timeout": 3, "options": "-c statement_timeout=2000 -c lock_timeout=500"})
        self.sessions = sessionmaker(self.engine)
        self.started_at = int(utcnow().timestamp())
        self._slots = threading.BoundedSemaphore(2)
        self._lock = threading.Lock()
        self._counts = {"written": 0, "failed": 0, "overloaded": 0, "anonymous_skipped": 0}
        self._warnings: dict[str, int] = {}
        self._anonymous_window = -1
        self._anonymous_count = 0
        self._pending: set[asyncio.Future[bool]] = set()

    def health(self) -> dict[str, int]:
        with self._lock:
            return {"started_at": self.started_at, **self._counts}

    def _issue(self, kind: str, request_id: UUID) -> None:
        minute = int(time.monotonic() // 60)
        with self._lock:
            self._counts[kind] += 1
            warn = self._warnings.get(kind) != minute
            self._warnings[kind] = minute
        if warn:
            logger.warning("access_event_%s request_id=%s", kind, request_id)

    async def record(self, event: AccessRecord) -> bool:
        if event.key_id is None and event.gateway_id is None:
            minute = int(time.monotonic() // 60)
            with self._lock:
                if minute != self._anonymous_window:
                    self._anonymous_window, self._anonymous_count = minute, 0
                if self._anonymous_count >= 30:
                    self._counts["anonymous_skipped"] += 1
                    return False
                self._anonymous_count += 1
        if not self._slots.acquire(blocking=False):
            self._issue("overloaded", event.request_id)
            return False
        try:
            # Reserve before submitting: at most two work items, including queued threads.
            future = asyncio.get_running_loop().run_in_executor(None, self._write, event)
        except RuntimeError:
            self._slots.release()
            self._issue("failed", event.request_id)
            return False
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)
        # Cancellation must not strand the reservation or cancel a queued DB append.
        return await asyncio.shield(future)

    def _write(self, event: AccessRecord) -> bool:
        try:
            with self.sessions.begin() as session:
                inserted = session.scalar(insert(AccessEvent).values(**asdict(event)).on_conflict_do_nothing(index_elements=[AccessEvent.request_id]).returning(AccessEvent.id))
            if inserted is not None:
                with self._lock:
                    self._counts["written"] += 1
            return True
        except Exception:
            # DB errors may contain parameters. Emit only a bounded, safe correlation ID.
            self._issue("failed", event.request_id)
            return False
        finally:
            self._slots.release()

    async def close(self) -> None:
        pending = tuple(self._pending)
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=8)
            except TimeoutError:
                logger.warning("access_event_shutdown_pending")
        await asyncio.to_thread(self.engine.dispose)


def _micros(value: datetime) -> int:
    delta = value - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _cursor(key_id: UUID, through: datetime, row: AccessEvent | None, result: AccessResult) -> str:
    payload = CURSOR.pack(1, key_id.bytes, _micros(through), _micros(row.created_at) if row else 0, row.id.bytes if row else bytes(16), FILTERS.index(result))
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _read_cursor(value: str, key_id: UUID, result: AccessResult) -> tuple[datetime, datetime | None, UUID | None]:
    try:
        if not 1 <= len(value) <= 128 or not value.isascii() or "=" in value:
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        version, key, through, before, identifier, selected = CURSOR.unpack(raw)
        if version != 1 or key != key_id.bytes or selected != FILTERS.index(result) or before > through or through <= 0 or before < 0 or (before == 0 and identifier != bytes(16)):
            raise ValueError
        return EPOCH + timedelta(microseconds=through), EPOCH + timedelta(microseconds=before) if before else None, UUID(bytes=identifier) if before else None
    except (ValueError, OverflowError, binascii.Error, struct.error) as exc:
        raise DomainError("invalid_cursor", "访问记录游标无效，请重新刷新", 422) from exc


def projection(row: AccessEvent) -> dict[str, Any]:
    result = "error" if row.completion != "completed" or row.status_code is None or row.status_code < 200 or row.status_code >= 500 else "denied" if row.status_code >= 400 else "success"
    return {"id": public_id("ace", row.id), "request_id": str(row.request_id), "started_at": int(row.started_at.timestamp()), "recorded_at": int(row.created_at.timestamp()), "entry": row.entry, "method": row.method, "route": row.route, "peer_ip": row.peer_ip, "client_ip": row.client_ip, "gateway_id": public_id("gateway", row.gateway_id) if row.gateway_id else None, "status_code": row.status_code, "error_code": row.error_code, "result": result, "completion": row.completion, "duration_milliseconds": row.duration_milliseconds, "request_bytes": row.request_bytes, "response_bytes": row.response_bytes}


class AccessEventQueries:
    def __init__(self, sessions: sessionmaker[Session], retention_days: int) -> None:
        self.sessions, self.retention_days = sessions, retention_days

    def snapshot(self, key_id: UUID, result: AccessResult = "all", before: str | None = None) -> dict[str, Any]:
        cursor = _read_cursor(before, key_id, result) if before else None
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            now = session.scalar(select(func.clock_timestamp()))
            assert now is not None
            key = session.get(ApiKey, key_id)
            if not key:
                raise DomainError("not_found", "API Key 不存在", 404)
            through = cursor[0] if cursor else now
            if through > now:
                raise DomainError("invalid_cursor", "访问记录游标时间无效，请重新刷新", 422)
            conditions: list[ColumnElement[bool]] = [AccessEvent.key_id == key.id, AccessEvent.tenant_id == key.tenant_id, AccessEvent.project_id == key.project_id, AccessEvent.created_at <= through, AccessEvent.created_at >= now - timedelta(days=self.retention_days)]
            if cursor and cursor[1] is not None:
                conditions.append(tuple_(AccessEvent.created_at, AccessEvent.id) < tuple_(literal(cursor[1]), literal(cursor[2])))
            if result == "success":
                conditions.append(and_(AccessEvent.completion == "completed", AccessEvent.status_code.between(200, 399)))
            elif result == "denied":
                conditions.append(and_(AccessEvent.completion == "completed", AccessEvent.status_code.between(400, 499)))
            elif result == "error":
                conditions.append(or_(AccessEvent.completion != "completed", AccessEvent.status_code.is_(None), AccessEvent.status_code < 200, AccessEvent.status_code >= 500))
            rows = list(session.scalars(select(AccessEvent).where(*conditions).order_by(AccessEvent.created_at.desc(), AccessEvent.id.desc()).limit(51)))
            return {"key_id": public_id("key", key.id), "key_name": key.name, "enabled": key.enabled, "deleted_at": int(key.deleted_at.timestamp()) if key.deleted_at else None, "snapshot_at": int(through.timestamp()), "retention_days": self.retention_days, "data": [projection(row) for row in rows[:50]], "first_cursor": _cursor(key.id, through, None, result), "next_cursor": _cursor(key.id, through, rows[49], result) if len(rows) > 50 else None}


def prune_access_events(sessions: sessionmaker[Session], retention_days: int = 30) -> int:
    with sessions.begin() as session:
        cutoff = func.clock_timestamp() - timedelta(days=retention_days)
        candidates = select(AccessEvent.id).where(AccessEvent.created_at < cutoff).order_by(AccessEvent.created_at, AccessEvent.id).limit(500).with_for_update(skip_locked=True)
        deleted = session.scalars(delete(AccessEvent).where(AccessEvent.id.in_(candidates)).returning(AccessEvent.id))
        return len(list(deleted))
