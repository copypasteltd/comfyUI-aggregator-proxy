from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from generation_domain.errors import DomainError
from generation_domain.values import canonical_json

from windows_worker.capacity import is_pointer

IDENTITY_BACKUP_MAX_BYTES = 512 * 1024**2
IDENTITY_BACKUP_SECONDS = 30


class Journal:
    """Single process writer. Submission intent commits before the network call."""
    def __init__(self, root: Path) -> None:
        if root.exists() and (is_pointer(root.lstat()) or not root.is_dir()):
            raise DomainError("journal_path_invalid", "Worker journal 目录无效")
        self.root = root.resolve()
        self.cleanup_cursor = ""
        self._closed = False
        self.identity_backup: Path | None = None
        root.mkdir(parents=True, exist_ok=True)
        for name in ("worker.lock", "journal.sqlite3", "journal.sqlite3-wal", "journal.sqlite3-shm"):
            path = root / name
            if path.exists() and (is_pointer(path.lstat()) or not stat.S_ISREG(path.lstat().st_mode)):
                raise DomainError("journal_path_invalid", "Worker journal 文件无效")
        self._lock: BinaryIO = (root / "worker.lock").open("a+b")
        if self._lock.seek(0, os.SEEK_END) == 0:
            self._lock.write(b"0")
            self._lock.flush()
        self._lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            raise DomainError("journal_in_use", "同一 Worker journal 已有运行进程") from exc
        try:
            self.db = sqlite3.connect(root / "journal.sqlite3")
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS worker_identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                control_origin TEXT NOT NULL, node_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS output_packages (
                attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), package TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claim_requests (
                boot_id TEXT PRIMARY KEY, request_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT,
                cloud_ack INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, execution_digest TEXT NOT NULL,
                claim TEXT NOT NULL, stage TEXT NOT NULL, association_id TEXT,
                workflow_digest TEXT, prompt_id TEXT, next_seq INTEGER NOT NULL DEFAULT 1,
                cloud_ack INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
                seq INTEGER NOT NULL, envelope TEXT NOT NULL, ack INTEGER NOT NULL DEFAULT 0,
                UNIQUE(attempt_id, seq)
            );
            """)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self._lock.close()
            raise

    def bind_identity(self, origin: str, node_id: str, *, adopt_legacy: bool = False) -> None:
        row = self.db.execute("SELECT control_origin,node_id FROM worker_identity WHERE singleton=1").fetchone()
        if row is not None:
            if tuple(row) != (origin, node_id):
                raise DomainError("journal_identity_mismatch", "该 journal 已绑定其他节点或控制面")
            return
        legacy = any(self.db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None for table in ("attempts", "commands", "claim_requests"))  # noqa: S608 -- fixed internal table names only
        if legacy and not adopt_legacy:
            raise DomainError("journal_identity_unbound", "旧 journal 有运行记录，须核对身份后显式接管")
        if legacy:
            self.identity_backup = self.backup_identity()
        with self.db:
            self.db.execute("INSERT INTO worker_identity(singleton,control_origin,node_id) VALUES(1,?,?)", (origin, node_id))

    def backup_identity(self) -> Path:
        deadline = time.monotonic() + IDENTITY_BACKUP_SECONDS
        directory = self.root / "identity-backups"
        target = directory / (uuid4().hex + ".sqlite3")
        created = False
        destination: sqlite3.Connection | None = None
        try:
            size = self.db.execute("PRAGMA page_count").fetchone()[0] * self.db.execute("PRAGMA page_size").fetchone()[0]
            if size > IDENTITY_BACKUP_MAX_BYTES:
                raise OSError("backup budget exceeded")
            directory.mkdir(exist_ok=True)
            if is_pointer(directory.lstat()) or not directory.is_dir():
                raise OSError("unsafe backup directory")
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            created = True
            destination = sqlite3.connect(target)

            def progress(status: int, remaining: int, total: int) -> None:
                if time.monotonic() > deadline:
                    raise OSError("backup time budget exceeded")

            self.db.backup(destination, pages=256, progress=progress, sleep=0.01)
            destination.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if destination.execute("PRAGMA quick_check").fetchall() != [("ok",)] or target.stat().st_size > IDENTITY_BACKUP_MAX_BYTES:
                raise OSError("backup validation failed")
            destination.close()
            destination = None
            with target.open("r+b") as stream:
                os.fsync(stream.fileno())
            return target
        except (OSError, sqlite3.Error):
            if destination is not None:
                destination.close()
            if created:
                target.unlink(missing_ok=True)
            raise DomainError("journal_identity_backup_failed", "旧 journal 身份接管备份未通过，运行记录保留") from None

    def accept_claim(self, claim: dict[str, Any]) -> None:
        existing = self.get(claim["attempt_id"])
        if existing:
            if existing["execution_digest"] != claim["execution_digest"]:
                raise DomainError("journal_conflict", "执行摘要与已有 journal 冲突")
            return
        persisted = {**claim}
        if "channel" in persisted:
            persisted["channel"] = {k: v for k, v in persisted["channel"].items() if k != "authorization"}
        with self.db:
            self.db.execute("INSERT INTO attempts(id, execution_digest, claim, stage) VALUES(?,?,?,?)", (claim["attempt_id"], claim["execution_digest"], canonical_json(persisted), "accepted"))

    def claim_request(self, boot_id: str) -> str:
        with self.db:
            row = self.db.execute("SELECT request_id FROM claim_requests WHERE boot_id=?", (boot_id,)).fetchone()
            if row:
                return str(row[0])
            value = str(uuid4())
            self.db.execute("INSERT INTO claim_requests(boot_id,request_id) VALUES(?,?)", (boot_id, value))
            return value

    def clear_claim(self, boot_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM claim_requests WHERE boot_id=?", (boot_id,))

    def has_pending_claim(self, boot_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM claim_requests WHERE boot_id=?", (boot_id,)).fetchone() is not None

    def command_intent(self, identifier: str, digest: str) -> dict[str, Any] | None:
        with self.db:
            row = self.db.execute("SELECT digest,result FROM commands WHERE id=?", (identifier,)).fetchone()
            if row:
                if row["digest"] != digest:
                    raise DomainError("command_conflict", "同一管理指令的摘要发生变化")
                return json.loads(row["result"]) if row["result"] else None
            self.db.execute("INSERT INTO commands(id,digest) VALUES(?,?)", (identifier, digest))
        return None

    def command_record(self, identifier: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM commands WHERE id=?", (identifier,)).fetchone()
        return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None} if row else None

    def pending_command(self, after: str = "") -> dict[str, Any] | None:
        row = self.db.execute("SELECT id FROM commands WHERE cloud_ack=0 AND id>? ORDER BY id LIMIT 1", (after,)).fetchone()
        if row is None and after:
            row = self.db.execute("SELECT id FROM commands WHERE cloud_ack=0 ORDER BY id LIMIT 1").fetchone()
        return self.command_record(row["id"]) if row else None

    def command_result(self, identifier: str, result: dict[str, Any]) -> None:
        with self.db:
            self.db.execute("UPDATE commands SET result=? WHERE id=?", (canonical_json(result), identifier))

    def command_ack(self, identifier: str) -> None:
        with self.db:
            self.db.execute("UPDATE commands SET cloud_ack=1 WHERE id=?", (identifier,))

    def get(self, attempt_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        return dict(row) if row else None

    def intent(self, attempt_id: str, workflow_digest: str) -> str:
        with self.db:
            row = self.get(attempt_id)
            if not row or row["stage"] != "accepted":
                raise DomainError("resubmit_forbidden", "已有提交意图；必须核对，禁止自动重提")
            association = str(uuid4())
            self.db.execute("UPDATE attempts SET stage='submit_intent',association_id=?,workflow_digest=? WHERE id=?", (association, workflow_digest, attempt_id))
        return association

    def submitted(self, attempt_id: str, prompt_id: str) -> None:
        with self.db:
            cursor = self.db.execute("UPDATE attempts SET stage='submitted',prompt_id=? WHERE id=? AND stage='posting'", (prompt_id, attempt_id))
            if cursor.rowcount != 1:
                raise DomainError("journal_conflict", "提交回执与 journal 不匹配")

    def begin_post(self, attempt_id: str, association: str) -> None:
        with self.db:
            cursor = self.db.execute("UPDATE attempts SET stage='posting' WHERE id=? AND stage='submit_intent' AND association_id=?", (attempt_id, association))
            if cursor.rowcount != 1:
                raise DomainError("resubmit_forbidden", "该提交已开始或结果未知，禁止再次 POST")

    def enqueue(self, attempt_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        with self.db:
            row = self.get(attempt_id)
            if not row:
                raise DomainError("attempt_missing", "journal 未记录该执行")
            event = {**envelope, "event_id": str(uuid4()), "journal_seq": row["next_seq"]}
            self.db.execute("INSERT INTO events(event_id,attempt_id,seq,envelope) VALUES(?,?,?,?)", (event["event_id"], attempt_id, event["journal_seq"], canonical_json(event)))
            self.db.execute("UPDATE attempts SET next_seq=next_seq+1 WHERE id=?", (attempt_id,))
        return event

    def pending(self) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT envelope FROM events WHERE ack=0 ORDER BY attempt_id,seq LIMIT 1000")]

    def acknowledge(self, event_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE events SET ack=1 WHERE event_id=?", (event_id,))

    def recovery(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM attempts WHERE cloud_ack=0 ORDER BY id")]

    def finish(self, attempt_id: str, *, retention_seconds: int = 3600, now: int | None = None) -> None:
        if type(retention_seconds) is not int or not 3600 <= retention_seconds <= 30 * 86400:
            raise ValueError("output retention must be at least one hour")
        with self.db:
            package = self.outputs(attempt_id)
            row = self.get(attempt_id)
            if row and not row["cloud_ack"] and package is not None and "confirmed_at" not in package and "retain_until" not in package:
                confirmed = int(time.time()) if now is None else now
                package = {**package, "confirmed_at": confirmed, "retain_until": confirmed + retention_seconds}
                self.db.execute("UPDATE output_packages SET package=? WHERE attempt_id=?", (canonical_json(package), attempt_id))
            self.db.execute("UPDATE attempts SET cloud_ack=1,stage='closed' WHERE id=?", (attempt_id,))

    def save_outputs(self, attempt_id: str, package: dict[str, Any]) -> None:
        with self.db:
            row = self.get(attempt_id)
            if not row or row["cloud_ack"]:
                raise DomainError("output_package_closed", "已确认的产物包不可覆盖")
            self.db.execute("INSERT INTO output_packages(attempt_id,package) VALUES(?,?) ON CONFLICT(attempt_id) DO UPDATE SET package=excluded.package", (attempt_id, canonical_json(package)))

    def capacity_usage(self, fallback: int) -> tuple[int, int]:
        from generation_contracts.capacity import MAX_BYTES, output_reservation
        reserved, uploads = 0, 0
        rows = self.db.execute("SELECT a.claim,a.stage,p.package FROM attempts a LEFT JOIN output_packages p ON p.attempt_id=a.id WHERE a.cloud_ack=0 LIMIT 10001").fetchall()
        if len(rows) > 10000:
            return MAX_BYTES, 10000
        for row in rows:
            if row["package"] is not None or row["stage"] == "uploading":
                uploads += 1
            else:
                try:
                    reserved += output_reservation(json.loads(row["claim"]).get("workflow"), fallback)
                except (ValueError, TypeError, AttributeError):
                    reserved += fallback
        return min(MAX_BYTES, reserved), uploads

    def cleanup_outputs(self, *, now: int | None = None, limit: int = 50) -> int:
        """Only acknowledged, expired journal-owned files. No discovery-based deletion."""
        if not 1 <= limit <= 100:
            raise ValueError("cleanup batch out of range")
        current = int(time.time()) if now is None else now
        query = """SELECT p.attempt_id,p.package FROM output_packages p
            JOIN attempts a ON a.id=p.attempt_id WHERE a.cloud_ack=1
            AND json_type(p.package,'$.confirmed_at')='integer'
            AND json_type(p.package,'$.retain_until')='integer'
            AND json_extract(p.package,'$.retain_until')<=?
            AND coalesce(json_extract(p.package,'$.retention_cleaned'),0)!=1
            AND p.attempt_id>?
            ORDER BY p.attempt_id LIMIT ?"""
        rows = self.db.execute(query, (current, self.cleanup_cursor, limit)).fetchall()
        if not rows and self.cleanup_cursor:
            self.cleanup_cursor = ""
            rows = self.db.execute(query, (current, self.cleanup_cursor, limit)).fetchall()
        if rows:
            self.cleanup_cursor = rows[-1]["attempt_id"]
        cleaned = 0
        for row in rows:
            package = json.loads(row["package"])
            confirmed, until = package["confirmed_at"], package["retain_until"]
            aid, names = row["attempt_id"], package.get("files")
            if not (0 <= confirmed <= until <= 2**53 - 1 and until >= confirmed + 3600 and current >= until
                    and re.fullmatch(r"att_[0-9a-f]{32}", aid) and isinstance(names, list) and 0 < len(names) <= 1000):
                continue
            pattern = re.compile(r"outputs/" + re.escape(aid) + r"/[0-9]{1,4}\.[a-z0-9]{1,8}")
            if any(not isinstance(name, str) or not pattern.fullmatch(name) for name in names):
                continue
            try:
                for parent in (self.root, self.root / "outputs", self.root / "outputs" / aid):
                    try:
                        info = parent.lstat()
                    except FileNotFoundError:
                        continue
                    if is_pointer(info) or not stat.S_ISDIR(info.st_mode):
                        raise OSError("unsafe output parent")
                # Validate the entire package before deleting its first file.
                for name in names:
                    path = self.root / name
                    try:
                        info = path.lstat()
                    except FileNotFoundError:
                        continue
                    if is_pointer(info) or not stat.S_ISREG(info.st_mode):
                        raise OSError("unsafe output file")
                for name in names:
                    (self.root / name).unlink(missing_ok=True)
                directory = self.root / "outputs" / aid
                if directory.exists() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                continue
            package["retention_cleaned"] = True
            with self.db:
                self.db.execute("UPDATE output_packages SET package=? WHERE attempt_id=?", (canonical_json(package), aid))
            cleaned += 1
        return cleaned

    def outputs(self, attempt_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT package FROM output_packages WHERE attempt_id=?", (attempt_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def rebase(self, claim: dict[str, Any]) -> None:
        persisted = {**claim, "channel": {k: v for k, v in claim["channel"].items() if k != "authorization"}}
        with self.db:
            self.db.execute("DELETE FROM events WHERE attempt_id=?", (claim["attempt_id"],))
            self.db.execute("UPDATE attempts SET claim=?,stage=?,prompt_id=?,next_seq=? WHERE id=? AND execution_digest=?", (canonical_json(persisted), claim["resume_state"], claim.get("prompt_id"), claim["journal_seq"] + 1, claim["attempt_id"], claim["execution_digest"]))

    def stage(self, attempt_id: str, value: str) -> None:
        with self.db:
            self.db.execute("UPDATE attempts SET stage=? WHERE id=?", (value, attempt_id))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.db.close()
        finally:
            try:
                self._lock.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock.close()
