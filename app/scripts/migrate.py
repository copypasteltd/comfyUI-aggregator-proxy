"""Back up the configured database and migrate under one native advisory lock."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from generation_infrastructure.settings import Settings
from sqlalchemy.engine import make_url

sys.dont_write_bytecode = True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default="head")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--pg-tools", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if (root / "release-manifest.json").exists():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from release_bundle import strict_json, verify_candidate, verify_payload
        record = strict_json((root.parent / "install.json").read_bytes())
        manifest = verify_payload(root, record["manifest_sha256"])
        verify_candidate(root, manifest)
        if arguments.env_file is None or arguments.backup_dir is None or Path(sys.prefix).resolve() != (root.parent / "venv").resolve():
            raise SystemExit("Installed migration requires its own interpreter and explicit configuration/backup paths")
        for path in (arguments.env_file.resolve(), arguments.backup_dir.resolve()):
            if path == root.parent or root.parent in path.parents:
                raise SystemExit("Installed migration configuration and backups must be external")
    if arguments.env_file is not None:
        for key in list(os.environ):
            if key.upper().startswith("GEN_"):
                del os.environ[key]
    settings = Settings(_env_file=arguments.env_file.resolve()) if arguments.env_file is not None else Settings()
    if (settings.environment == "production") != arguments.production:
        raise SystemExit("Environment mismatch: production migration requires explicit --production")
    url = make_url(settings.database_url.get_secret_value())
    project_binary = (arguments.pg_tools / ("pg_dump.exe" if os.name == "nt" else "pg_dump")) if arguments.pg_tools is not None else root / ".local/toolchains/postgres-18.6/pgsql/bin/pg_dump.exe"
    binary = str(project_binary) if project_binary.exists() else (None if arguments.pg_tools is not None else shutil.which("pg_dump"))
    if not binary:
        raise SystemExit("Native PostgreSQL 18 pg_dump is required for pre-migration backup")
    version = subprocess.run([binary, "--version"], check=True, timeout=5, capture_output=True, text=True).stdout  # noqa: S603 -- fixed native executable
    if not re.search(r"\b18\.\d+\b", version):
        raise SystemExit("The migration backup requires PostgreSQL 18 pg_dump")
    backup_root = arguments.backup_dir.resolve() if arguments.backup_dir is not None else root / ".local/backups"
    if (root / "release-manifest.json").exists() and (arguments.backup_dir is None or backup_root == root or root in backup_root.parents):
        raise SystemExit("Installed candidates require an explicit external backup directory")
    target = backup_root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".dump")
    target.parent.mkdir(parents=True, exist_ok=True)
    options = {"host": url.host, "port": str(url.port or 5432), "user": url.username, "password": url.password, "dbname": url.database, "connect_timeout": "5", **url.query}
    # Preserve libpq TLS/certificate and timeout options for both connections.
    # The password is supplied only via the child environment, never argv.
    dump_dsn = psycopg.conninfo.make_conninfo(**{k: v for k, v in options.items() if k != "password" and v is not None})
    with psycopg.connect(**options, autocommit=True) as connection:
        server_version = connection.info.server_version
        if not 180000 <= server_version < 190000:
            raise SystemExit("This migration entry requires a PostgreSQL 18 target")
        if not connection.execute("SELECT pg_try_advisory_lock(634720182)").fetchone()[0]:
            raise SystemExit("Another migration holds the project migration lock")
        try:
            environment = {**os.environ, "PGPASSWORD": url.password or ""}
            partial = target.with_suffix(".partial")
            subprocess.run([binary, "--format=custom", "--file=" + str(partial), "--dbname=" + dump_dsn, "--no-password"], env=environment, check=True, timeout=300, capture_output=True)  # noqa: S603 -- fixed native backup executable and configured database
            restore = Path(binary).with_name("pg_restore.exe" if os.name == "nt" else "pg_restore")
            subprocess.run([str(restore), "--list", str(partial)], check=True, timeout=30, capture_output=True)  # noqa: S603 -- validate the native custom archive before applying migrations
            partial.replace(target)
            with target.open("rb") as backup:
                digest = hashlib.file_digest(backup, "sha256").hexdigest()
            migration_config = Config(str(root / "alembic.ini"))
            migration_config.attributes["settings"] = settings
            command.upgrade(migration_config, arguments.revision)
            evidence = {"at": datetime.now(UTC).isoformat(), "database": url.database, "server_version": server_version, "revision": arguments.revision, "backup_file": target.name, "backup_sha256": digest, "archive_list_verified": True, "restore_rehearsed": False}
            target.with_suffix(".json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print("Backup and migration completed; restore rehearsal is a separate required check")
        finally:
            connection.execute("SELECT pg_advisory_unlock(634720182)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Migration failed; no configuration values are logged", file=sys.stderr)
        raise SystemExit(1) from None
