from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import psycopg
from cryptography.fernet import InvalidToken
from generation_contracts.recovery_checkpoint import (
    RecoveryCheckpoint,
    RecoveryMedia,
    RecoveryObjects,
    RecoverySecrets,
    TableSeal,
)
from psycopg import sql
from psycopg.pq import TransactionStatus

from generation_infrastructure.models import Base
from generation_infrastructure.security import SecretVault, verifier
from generation_infrastructure.settings import Settings

MAX_CHECKPOINT_BYTES = 1024 * 1024
CHUNK = 1024 * 1024
DOMAIN = b"render-control-recovery-v1\0"


class CompleteObjectReader(Protocol):
    def read_complete(self, key: str, version: str, size: int) -> Iterator[bytes]: ...


class RecoveryCheckError(ValueError):
    """Fixed public code; never include database, object or secret values."""


@dataclass(frozen=True)
class RecoveryLimits:
    rows: int = 1000000
    row_bytes: int = 8 * CHUNK
    objects: int = 10000
    object_bytes: int = 64 * 1024**3
    seconds: int = 300

    def __post_init__(self) -> None:
        for value, maximum in [(self.rows, 1000000), (self.row_bytes, 32 * CHUNK), (self.objects, 10000), (self.object_bytes, 1024**4), (self.seconds, 3600)]:
            if type(value) is not int or not 1 <= value <= maximum:
                raise RecoveryCheckError("recovery_limits_invalid")


class Budget:
    def __init__(self, limits: RecoveryLimits) -> None:
        self.limits = limits
        self.deadline = time.monotonic() + limits.seconds

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise RecoveryCheckError("recovery_deadline_exceeded")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def load_recovery_key(path: Path) -> bytes:
    try:
        with path.open("rb") as source:
            encoded = source.read(1025)
        if len(encoded) > 1024:
            raise ValueError
        key = base64.b64decode(encoded.strip(), altchars=b"-_", validate=True)
        if len(key) != 32:
            raise ValueError
        return key
    except (OSError, ValueError):
        raise RecoveryCheckError("recovery_key_invalid") from None


def write_exclusive(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _key(key: bytes, settings: Settings | None = None) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise RecoveryCheckError("recovery_key_invalid")
    if settings is not None and (hmac.compare_digest(key, base64.urlsafe_b64decode(settings.encryption_key.get_secret_value())) or hmac.compare_digest(key, settings.credential_pepper.get_secret_value().encode()) or hmac.compare_digest(base64.urlsafe_b64encode(key), settings.credential_pepper.get_secret_value().encode())):
        raise RecoveryCheckError("recovery_key_must_be_independent")


def _signature(checkpoint: RecoveryCheckpoint, key: bytes) -> str:
    return hmac.new(key, DOMAIN + canonical(checkpoint.model_dump(exclude={"signature"})), hashlib.sha256).hexdigest()


def authenticate_checkpoint(data: object, key: bytes) -> RecoveryCheckpoint:
    _key(key)
    try:
        checkpoint = RecoveryCheckpoint.model_validate(data)
    except ValueError:
        raise RecoveryCheckError("recovery_checkpoint_invalid") from None
    if not hmac.compare_digest(_signature(checkpoint, key), checkpoint.signature):
        raise RecoveryCheckError("recovery_signature_invalid")
    return checkpoint


def load_checkpoint(path: Path, key: bytes) -> RecoveryCheckpoint:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError
            result[name] = value
        return result

    try:
        with path.open("rb") as source:
            raw = source.read(MAX_CHECKPOINT_BYTES + 1)
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=unique)
    except (OSError, ValueError, RecursionError):
        raise RecoveryCheckError("recovery_checkpoint_invalid") from None
    return authenticate_checkpoint(value, key)


def _prepare(connection: psycopg.Connection[Any]) -> None:
    if connection.info.transaction_status == TransactionStatus.IDLE:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    readonly = connection.execute("SHOW transaction_read_only").fetchone()
    isolation = connection.execute("SHOW transaction_isolation").fetchone()
    if readonly is None or isolation is None or readonly[0] != "on" or isolation[0] != "repeatable read":
        raise RecoveryCheckError("recovery_readonly_snapshot_required")
    connection.execute("SET LOCAL TIME ZONE 'UTC'")
    connection.execute("SET LOCAL statement_timeout = '30000ms'")
    connection.execute("SET LOCAL lock_timeout = '1000ms'")


def _inspect_database(connection: psycopg.Connection[Any], settings: Settings, key: bytes, nonce: str, budget: Budget) -> tuple[str, list[TableSeal], list[tuple[Any, ...]], int]:
    _prepare(connection)
    names = sorted(Base.metadata.tables)
    actual = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename")]
    if actual != names:
        raise RecoveryCheckError("recovery_table_set_mismatch")
    revisions = list(connection.execute("SELECT version_num FROM public.alembic_version"))
    if len(revisions) != 1:
        raise RecoveryCheckError("recovery_revision_invalid")
    seals: list[TableSeal] = []
    total_rows = 0
    for name in names:
        budget.check()
        digest = hmac.new(key, DOMAIN + nonce.encode() + b"\0" + name.encode(), hashlib.sha256)
        columns = connection.execute("SELECT column_name,data_type,udt_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (name,)).fetchall()
        digest.update(canonical(columns))
        primary = [column.name for column in Base.metadata.tables[name].primary_key.columns]
        query = sql.SQL("SELECT CASE WHEN octet_length(to_jsonb(t)::text) <= %s THEN to_jsonb(t)::text END FROM public.{} AS t ORDER BY {}").format(sql.Identifier(name), sql.SQL(",").join(sql.Identifier(column) for column in primary))
        rows = 0
        with connection.cursor(name="recovery_" + uuid4().hex) as cursor:
            cursor.itersize = 1
            cursor.execute(query, (budget.limits.row_bytes,))
            for (value,) in cursor:
                budget.check()
                if value is None:
                    raise RecoveryCheckError("recovery_database_limit_exceeded")
                encoded = value.encode("utf-8")
                rows += 1
                total_rows += 1
                if total_rows > budget.limits.rows or len(encoded) > budget.limits.row_bytes:
                    raise RecoveryCheckError("recovery_database_limit_exceeded")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
        seals.append(TableSeal(name=name, rows=rows, hmac_sha256=digest.hexdigest()))
    vault = SecretVault(settings.encryption_key.get_secret_value())
    encrypted = 0
    for secret_query in ["SELECT encrypted_auth FROM public.channels WHERE encrypted_auth IS NOT NULL", "SELECT config->>'encrypted_auth' FROM public.channel_revisions WHERE config->>'encrypted_auth' IS NOT NULL", "SELECT channel_snapshot->>'encrypted_auth' FROM public.attempts WHERE channel_snapshot->>'encrypted_auth' IS NOT NULL", "SELECT authorization_snapshot FROM public.worker_commands WHERE authorization_snapshot IS NOT NULL"]:
        with connection.cursor(name="recovery_secrets_" + uuid4().hex) as cursor:
            cursor.itersize = 1
            cursor.execute(secret_query)
            for (ciphertext,) in cursor:
                budget.check()
                encrypted += 1
                if not isinstance(ciphertext, str) or len(ciphertext) > 65536 or encrypted > budget.limits.rows:
                    raise RecoveryCheckError("recovery_secret_invalid")
                try:
                    vault.decrypt(ciphertext)
                except (InvalidToken, UnicodeError, ValueError):
                    raise RecoveryCheckError("recovery_secret_invalid") from None
    objects = connection.execute("SELECT object_key,version_id,bytes,sha256,media_type FROM public.object_records WHERE deleted_at IS NULL ORDER BY id LIMIT %s", (budget.limits.objects + 1,)).fetchall()
    if len(objects) > budget.limits.objects or sum(row[2] for row in objects) > budget.limits.object_bytes:
        raise RecoveryCheckError("recovery_object_limit_exceeded")
    return revisions[0][0], seals, objects, encrypted


def _inspect_objects(store: CompleteObjectReader, objects: list[tuple[Any, ...]], budget: Budget) -> RecoveryObjects:
    total = 0
    media = {"images": 0, "videos": 0, "audio": 0, "other": 0}
    for key, version, size, expected, mime in objects:
        budget.check()
        if not isinstance(version, str) or not version or version == "null" or size <= 0:
            raise RecoveryCheckError("recovery_object_version_missing")
        digest, length = hashlib.sha256(), 0
        data = None
        try:
            data = store.read_complete(key, version, size)
            for block in data:
                budget.check()
                if not isinstance(block, bytes) or len(block) > CHUNK:
                    raise RecoveryCheckError("recovery_object_mismatch")
                length += len(block)
                if length > size:
                    raise RecoveryCheckError("recovery_object_mismatch")
                digest.update(block)
        except RecoveryCheckError:
            raise
        except Exception:
            raise RecoveryCheckError("recovery_object_unavailable") from None
        finally:
            if close := getattr(data, "close", None):
                try:
                    close()
                except Exception:
                    logging.getLogger(__name__).warning("recovery_object_close_failed")
        if length != size or not hmac.compare_digest(digest.hexdigest(), expected):
            raise RecoveryCheckError("recovery_object_mismatch")
        total += length
        kind = "images" if mime.startswith("image/") else "videos" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "other"
        media[kind] += 1
    return RecoveryObjects(records=len(objects), bytes=total, media=RecoveryMedia(**media))


def create_checkpoint(connection: psycopg.Connection[Any], settings: Settings, store: CompleteObjectReader, key: bytes, limits: RecoveryLimits = RecoveryLimits()) -> RecoveryCheckpoint:
    _key(key, settings)
    budget = Budget(limits)
    nonce = uuid4().hex
    try:
        revision, tables, objects, encrypted = _inspect_database(connection, settings, key, nonce, budget)
    except psycopg.Error:
        raise RecoveryCheckError("recovery_database_unavailable") from None
    challenge = "render-control-recovery-canary:" + nonce
    result = RecoveryCheckpoint(format_version=1, contract_version="0.5.0", created_at_ms=int(datetime.now(UTC).timestamp() * 1000), nonce=nonce, database_revision=revision, tables=tables, objects=_inspect_objects(store, objects, budget), secrets=RecoverySecrets(master_canary=SecretVault(settings.encryption_key.get_secret_value()).encrypt(challenge), pepper_canary=verifier(challenge, settings.credential_pepper.get_secret_value()), pepper_version=settings.credential_pepper_version, encrypted_values=encrypted), signature="0" * 64)
    return result.model_copy(update={"signature": _signature(result, key)})


def verify_checkpoint(connection: psycopg.Connection[Any], settings: Settings, store: CompleteObjectReader, key: bytes, checkpoint: RecoveryCheckpoint, limits: RecoveryLimits = RecoveryLimits()) -> dict[str, Any]:
    _key(key, settings)
    checkpoint = authenticate_checkpoint(checkpoint.model_dump(), key)
    challenge = "render-control-recovery-canary:" + checkpoint.nonce
    try:
        valid_master = hmac.compare_digest(SecretVault(settings.encryption_key.get_secret_value()).decrypt(checkpoint.secrets.master_canary), challenge)
    except (InvalidToken, ValueError, UnicodeError):
        valid_master = False
    if not valid_master or checkpoint.secrets.pepper_version != settings.credential_pepper_version or not hmac.compare_digest(checkpoint.secrets.pepper_canary, verifier(challenge, settings.credential_pepper.get_secret_value())):
        raise RecoveryCheckError("recovery_runtime_key_mismatch")
    budget = Budget(limits)
    try:
        revision, tables, objects, encrypted = _inspect_database(connection, settings, key, checkpoint.nonce, budget)
    except psycopg.Error:
        raise RecoveryCheckError("recovery_database_unavailable") from None
    if revision != checkpoint.database_revision or tables != checkpoint.tables or encrypted != checkpoint.secrets.encrypted_values:
        raise RecoveryCheckError("recovery_database_mismatch")
    actual = _inspect_objects(store, objects, budget)
    if actual != checkpoint.objects:
        raise RecoveryCheckError("recovery_object_mismatch")
    return {"passed": True, "tables": len(tables), "rows": sum(table.rows for table in tables), "objects": actual.records, "object_bytes": actual.bytes, "media": actual.media.model_dump(), "encrypted_values": encrypted, "master_key_verified": True, "pepper_verified": True, "database_read_only": True, "reconciliation_required_before_dispatch": True, "production_recovery_accepted": False}
