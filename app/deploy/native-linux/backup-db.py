"""Create and list-verify one native PostgreSQL 18 archive; never print its DSN."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from generation_infrastructure.settings import Settings
from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, help="Explicit operator configuration; otherwise use the service environment only")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(_env_file=args.config if args.config else None)
    if settings.environment != "production":
        raise SystemExit("Requires explicit production configuration")
    url = make_url(settings.database_url.get_secret_value())
    options = {"host": url.host, "port": str(url.port or 5432), "user": url.username, "password": url.password, "dbname": url.database, "connect_timeout": "5", **url.query}
    with psycopg.connect(**options) as connection:
        if not 180000 <= connection.info.server_version < 190000:
            raise SystemExit("PostgreSQL 18 required")
    binary = Path("/usr/lib/postgresql/18/bin")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / ("control-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".dump")
    partial = target.with_suffix(".partial")
    dsn = psycopg.conninfo.make_conninfo(**{k: v for k, v in options.items() if k != "password" and v is not None})
    subprocess.run([str(binary / "pg_dump"), "--format=custom", "--no-password", "--file=" + str(partial), "--dbname=" + dsn], env={**os.environ, "PGPASSWORD": url.password or ""}, check=True, capture_output=True, timeout=300)  # noqa: S603 -- fixed native operation with trusted deployment configuration
    subprocess.run([str(binary / "pg_restore"), "--list", str(partial)], check=True, capture_output=True, timeout=30)  # noqa: S603 -- fixed native operation with trusted deployment configuration
    partial.replace(target)
    with target.open("rb") as source:
        record = {"at": datetime.now(UTC).isoformat(), "file": target.name, "bytes": target.stat().st_size, "sha256": hashlib.file_digest(source, "sha256").hexdigest(), "archive_list_verified": True, "full_disaster_recovery_verified": False}
    target.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Native backup failed; inspect protected diagnostics without logging configuration values") from None
