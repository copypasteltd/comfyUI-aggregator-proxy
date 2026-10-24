"""Restore a consistent development backup into a disposable native PG database."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from generation_infrastructure.models import Base
from generation_infrastructure.settings import Settings
from psycopg import sql
from sqlalchemy.engine import make_url


def counts(connection: psycopg.Connection[tuple[object, ...]]) -> dict[str, int]:
    return {name: int(connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))).fetchone()[0]) for name in sorted(Base.metadata.tables)}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if settings.environment != "development" or url.host != "127.0.0.1" or url.port != 55438 or url.database != "render_control":
        raise SystemExit("This rehearsal is restricted to this project's loopback development database")
    options = {"host": url.host, "port": str(url.port), "user": url.username, "password": url.password, "dbname": url.database, "connect_timeout": "5", **url.query}
    tools = root / ".local/toolchains/postgres-18.6/pgsql/bin"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = root / ".local/backups" / (stamp + "-rehearsal.dump")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_database = "render_control_restore_" + uuid4().hex[:12]
    environment = {**os.environ, "PGPASSWORD": url.password or ""}
    base_dsn = {k: v for k, v in options.items() if k != "password" and v is not None}
    created, cleaned = False, False
    started = datetime.now(UTC)
    with psycopg.connect(**{**options, "dbname": "postgres"}, autocommit=True) as admin:
        if not admin.execute("SELECT pg_try_advisory_lock(634720182)").fetchone()[0]:
            raise SystemExit("Another migration or rehearsal holds the project lock")
        try:
            with psycopg.connect(**options) as source:
                source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
                expected = counts(source)
                revision = source.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                subprocess.run([str(tools / "pg_dump.exe"), "--format=custom", "--snapshot=" + str(snapshot), "--file=" + str(destination), "--dbname=" + psycopg.conninfo.make_conninfo(**base_dsn), "--no-password"], env=environment, check=True, timeout=120, capture_output=True)  # noqa: S603 -- verified project-native executable, configured development database
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(temporary_database)))
            created = True
            restore_dsn = psycopg.conninfo.make_conninfo(**{**base_dsn, "dbname": temporary_database})
            subprocess.run([str(tools / "pg_restore.exe"), "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error", "--dbname=" + restore_dsn, "--no-password", str(destination)], env=environment, check=True, timeout=120, capture_output=True)  # noqa: S603 -- restore only into the freshly created, tracked disposable database
            with psycopg.connect(**{**options, "dbname": temporary_database}) as restored:
                actual = counts(restored)
                actual_revision = restored.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            if actual != expected or actual_revision != revision:
                raise RuntimeError("Restored schema revision or table counts did not match the exported snapshot")
        finally:
            if created:
                # A literal identifier created above, never the configured source.
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(temporary_database)))
                cleaned = True
            admin.execute("SELECT pg_advisory_unlock(634720182)")
    with destination.open("rb") as backup:
        digest = hashlib.file_digest(backup, "sha256").hexdigest()
    evidence = {"at": datetime.now(UTC).isoformat(), "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 2), "source": "project-native PostgreSQL development database", "consistent_exported_snapshot": True, "schema_revision": revision, "table_count": len(expected), "total_rows": sum(expected.values()), "row_counts_match": True, "backup_sha256": digest, "temporary_database_dropped": cleaned, "object_bytes_and_master_keys_restored": False, "production_recovery_accepted": False}
    target = root.parent / "docs/evidence" / datetime.now().strftime("%Y-%m-%d") / "native-restore-rehearsal.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"restored_tables": len(expected), "row_counts_match": True, "temporary_database_dropped": cleaned}))


if __name__ == "__main__":
    main()
