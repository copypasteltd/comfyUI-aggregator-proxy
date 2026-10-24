"""Own a disposable native PG/API fixture for catalog UI checks and recording."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from generation_infrastructure.models import (
    AdminUser,
    ApiKey,
    Channel,
    GatewayService,
    Project,
    PublicModel,
    Tenant,
    WorkflowBinding,
)
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence" / datetime.now().date().isoformat()
MEDIA = ROOT.parent / "docs/media/gateway-catalog"


def module(name: str, filename: str) -> Any:
    spec = spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    result = module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-python", type=Path, default=Path(sys.base_prefix) / "python.exe")
    args = parser.parse_args()
    native = module("catalog_browser_owned_native", "test-sub2api-native.py")
    fixtures = module("catalog_browser_protocol_seed", "native-provider-fixtures.py")
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if (settings.environment, url.host, url.port, url.database) != ("development", "127.0.0.1", 55438, "render_control"):
        raise SystemExit("Requires the owned loopback development PostgreSQL")
    run = uuid4().hex[:12]
    private = ROOT / ".local/gateway-catalog-browser" / run
    private.mkdir(parents=True, exist_ok=False)
    MEDIA.mkdir(parents=True, exist_ok=True)
    database = "render_catalog_browser_" + run
    origin = "https://127.0.0.1:" + str(native.port())
    pepper, password = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    env = {**os.environ, "PYTHONUTF8": "1", "GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": url.set(database=database).render_as_string(hide_password=False), "GEN_CREDENTIAL_PEPPER": pepper, "GEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GEN_STORAGE_BACKEND": "local_development", "GEN_LOCAL_STORAGE_ROOT": str(private / "objects"), "GEN_SPOOL_ROOT": str(private / "spool"), "GEN_PUBLIC_ORIGIN": origin}
    env.pop("GEN_AUDIT_RETENTION_DAYS", None)
    owned = native.Processes(private)
    report: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "passed": False, "synthetic_data": True, "actual_admin_api": True, "provider_receipts_are_protocol_fixtures": True, "real_comfyui": False, "physical_fleet_validated": False, "virtualization_used": False, "cleanup": {"processes": False, "database": False, "credentials": False}}
    created, engine = False, None
    phase = "prepare"
    try:
        with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname="postgres", autocommit=True) as admin:
            try:
                admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
                created = True
                phase = "backup_before_migration"
                pg_env = {**env, "PGPASSWORD": url.password or ""}
                dsn = psycopg.conninfo.make_conninfo(host=url.host, port=url.port, user=url.username, dbname=database)
                backup = private / "empty.dump"
                tools = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
                for command in ([str(tools / "pg_dump.exe"), "--format=custom", "--file=" + str(backup), "--dbname=" + dsn, "--no-password"], [str(tools / "pg_restore.exe"), "--list", str(backup)]):
                    result = subprocess.run(command, env=pg_env, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- only newly created, owned DB
                    if result.returncode:
                        raise RuntimeError("catalog_browser_backup_failed")
                report["empty_database_backup_verified"] = True
                phase = "migrate"
                with (private / "migration.log").open("wb") as log:
                    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- owned disposable DB
                if result.returncode:
                    raise RuntimeError("catalog_browser_migration_failed")
                engine = create_engine(env["GEN_DATABASE_URL"], hide_parameters=True)
                sessions = sessionmaker(engine, expire_on_commit=False)
                seed = fixtures.seed_control(sessions, pepper, "http://127.0.0.1:18188", "0" * 40, {"keys": [1, 2], "user": 1})
                with sessions.begin() as session:
                    for binding in session.scalars(select(WorkflowBinding)):
                        binding.runtime_approval = {"profile_id": "synthetic-browser-fixture", "physical_runtime_approval": False}
                    for channel in session.scalars(select(Channel)):
                        channel.applied_revision = channel.verified_revision = channel.desired_revision
                    for model in session.scalars(select(PublicModel)):
                        model.alias = "demo-" + model.task_type.replace("_", "-")
                    # Native protocol seeds bypass Admin DTO serialization. Add
                    # its explicit nullable value for this UI-only fixture.
                    for key in session.scalars(select(ApiKey)):
                        policy = json.loads(json.dumps(key.policy))
                        policy["limits"]["daily_audio_milliseconds"].setdefault("value", None)
                        key.policy = policy
                    session.scalar(select(Tenant)).name = "DEMO 合成租户"
                    session.scalar(select(Project)).name = "图片 / 视频 / 音频"
                    session.scalar(select(GatewayService)).name = "DEMO · Sub2API"
                    session.add_all([AdminUser(username="demo-admin", password_hash=password_hash(password), role="administrator"), AdminUser(username="demo-observer", password_hash=password_hash(password), role="observer")])
                phase = "start_https_api"
                process = owned.start("catalog-browser-api", [sys.executable, "-m", "uvicorn", "control_api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", origin.rsplit(":", 1)[1], "--ssl-keyfile", str(ROOT / ".local/tls/localhost.key"), "--ssl-certfile", str(ROOT / ".local/tls/localhost.crt"), "--no-access-log", "--no-proxy-headers"], env)
                with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(ROOT / ".local/tls/localhost.crt")), trust_env=False) as http:
                    native.wait_http(http, "/health/ready", process)
                fixture = private / "capture.secret.json"
                fixture.write_text(json.dumps({"origin": origin, "password": password, "gateway": seed["gateway_id"], "gateway_credential": seed["gateway_token"], "media": str(MEDIA), "private": str(private)}), encoding="utf-8")
                phase = "browser"
                browser_env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("GEN_", "PYTHON"))}
                browser = owned.start("catalog-browser-recorder", [str(args.browser_python.resolve(strict=True)), "-X", "utf8", str(ROOT / "scripts/record-gateway-catalog-browser.py"), "--fixture", str(fixture)], browser_env)
                try:
                    browser_code = browser.wait(timeout=600)
                except subprocess.TimeoutExpired:
                    owned.stop(browser)
                    raise RuntimeError("catalog_browser_deadline") from None
                browser_report = MEDIA / "capture.json"
                if browser_report.exists():
                    report["browser"] = json.loads(browser_report.read_text(encoding="utf-8"))
                if browser_code or not report.get("browser", {}).get("passed"):
                    raise RuntimeError("catalog_browser_checks_failed")
                report["passed"] = True
            finally:
                owned.close()
                report["cleanup"]["processes"] = all(item["stopped"] for item in owned.stops)
                if engine is not None:
                    engine.dispose()
                secret = private / "capture.secret.json"
                secret.unlink(missing_ok=True)
                report["cleanup"]["credentials"] = not secret.exists()
                if created:
                    admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (database,))
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
                    report["cleanup"]["database"] = True
    except Exception as exc:
        report["failure"] = {"phase": phase, "type": type(exc).__name__}
        (private / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
    report["passed"] = report["passed"] and all(report["cleanup"].values())
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "gateway-catalog-browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (private / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "phase": phase, "cleanup": report["cleanup"], "private_run": run}), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
