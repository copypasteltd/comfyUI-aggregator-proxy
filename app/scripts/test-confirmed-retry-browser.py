"""Native PostgreSQL/API fixture for confirmed OOM retry Admin evidence."""
from __future__ import annotations

import json
import os
import secrets
import ssl
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from generation_contracts.queue_queries import QueueQuery
from generation_domain.values import parse_id
from generation_infrastructure.admin_jobs import JobAdministration
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import AdminUser, Job, UsageLedger
from generation_infrastructure.security import SecretVault, password_hash
from generation_infrastructure.settings import Settings
from psycopg import sql
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib.util import module_from_spec, spec_from_file_location  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence/2026-10-25"


def load(name: str, source: Path):
    spec = spec_from_file_location(name, source)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    started = time.monotonic()
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    assert settings.environment == "development" and (url.host, url.port, url.database) == ("127.0.0.1", 55438, "render_control")
    native = load("retry_ui_native", ROOT / "scripts/test-sub2api-native.py")
    fixtures = load("retry_ui_catalog", ROOT / "tests/conftest.py")
    run_id = uuid4().hex[:12]
    root = ROOT / ".local/confirmed-retry-browser" / run_id
    root.mkdir(parents=True)
    processes = native.Processes(root)
    database = "render_control_oomui_" + run_id
    options = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    report = {"passed": False, "synthetic_data": True, "real_comfyui": False, "real_gpu_generation": False, "run_id": run_id, "checks": [], "cleanup": {"processes": False, "database": False, "private_fixture": False}}
    engine = None
    created = False
    environment = {**os.environ, "GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": url.set(database=database).render_as_string(hide_password=False), "GEN_CREDENTIAL_PEPPER": secrets.token_urlsafe(48), "GEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GEN_STORAGE_BACKEND": "local_development", "GEN_LOCAL_STORAGE_ROOT": str(root / "objects"), "GEN_SPOOL_ROOT": str(root / "spool"), "PYTHONUTF8": "1"}
    with psycopg.connect(**options, dbname="postgres", autocommit=True) as admin:
        assert admin.execute("SELECT pg_try_advisory_lock(634720182)").fetchone()[0]
        try:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            created = True
            tools = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
            safe_dsn = psycopg.conninfo.make_conninfo(**{**{k: v for k, v in options.items() if k != "password"}, "dbname": database})
            backup = root / "empty.dump"
            for command in ([str(tools / "pg_dump.exe"), "--format=custom", "--file=" + str(backup), "--dbname=" + safe_dsn, "--no-password"], [str(tools / "pg_restore.exe"), "--list", str(backup)]):
                subprocess.run(command, env={**environment, "PGPASSWORD": url.password or ""}, check=True, timeout=60, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- only the owned native database is backed up before DDL
            report["backup_before_ddl"] = {"sha256": native.sha(backup), "archive_list_verified": True}
            with (root / "migrate.log").open("wb") as stream:
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed native migration command for the new disposable database
            engine = create_engine(environment["GEN_DATABASE_URL"])
            sessions = sessionmaker(engine, expire_on_commit=False)
            catalog = fixtures.catalog.__wrapped__(sessions)
            runtime_settings = Settings(_env_file=None, environment="test", database_url=environment["GEN_DATABASE_URL"], credential_pepper=environment["GEN_CREDENTIAL_PEPPER"], encryption_key=environment["GEN_ENCRYPTION_KEY"], public_origin=settings.public_origin, storage_backend="local_development", local_storage_root=root / "objects", spool_root=root / "spool")
            jobs = JobRepository(sessions)
            leases = LeaseRepository(sessions, SecretVault(runtime_settings.encryption_key.get_secret_value()), allow_synthetic_catalog=True)
            sys.modules["conftest"] = fixtures
            scenarios = load("confirmed_retry_ui_scenarios", ROOT / "tests/integration/test_confirmed_failure_retries.py")
            scenarios.alternative(sessions, catalog)
            identifiers = []
            for _index in range(2):
                result = jobs.create(catalog["identity"], {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Owned synthetic OOM browser fixture"}}, str(uuid4()), "synthetic-browser")
                claim = leases.claim(catalog["node_id"], catalog["boot_id"])
                assert claim and claim["job_id"] == result["id"]
                _, outcome = scenarios.emit(leases, claim, "execution_failed", scenarios.running(leases, claim), 4)
                assert outcome["status"] == "retry_wait"
                identifiers.append(result["id"])
            password = secrets.token_urlsafe(32)
            with sessions.begin() as session:
                session.add(AdminUser(username="retry-browser", password_hash=password_hash(password), role="administrator"))
            origin = "https://127.0.0.1:" + str(native.port())
            environment["GEN_PUBLIC_ORIGIN"] = origin
            cert = ROOT / ".local/tls/localhost.crt"
            api = processes.start("retry-api", [sys.executable, "-m", "uvicorn", "control_api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", origin.rsplit(":", 1)[1], "--ssl-certfile", str(cert), "--ssl-keyfile", str(ROOT / ".local/tls/localhost.key"), "--no-access-log", "--no-proxy-headers"], environment)
            with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(cert)), trust_env=False) as client:
                native.wait_http(client, "/health/ready", api)
            media = ROOT.parent / "docs/media/confirmed-retry"
            media.mkdir(parents=True, exist_ok=True)
            fixture = {"origin": origin, "password": password, "jobs": identifiers, "report": str(media / "capture.json"), "media": str(media), "private": str(root)}
            native.write(root / "fixture.json", fixture)
            browser_environment = {key: value for key, value in os.environ.items() if not key.upper().startswith(("GEN_", "PYTHON"))}
            browser = processes.start("confirmed-retry-browser", [str(Path(sys.base_prefix) / "python.exe"), "-X", "utf8", str(ROOT / "scripts/record-confirmed-retry-browser.py"), "--fixture", str(root / "fixture.json")], browser_environment)
            assert browser.wait(timeout=240) == 0, "owned_browser_checks_failed"
            browser_report = json.loads(Path(fixture["report"]).read_text(encoding="utf-8"))
            assert browser_report["passed"] and browser_report["closed_contexts"] == 2 and browser_report["browser_closed"]
            with sessions() as session:
                remaining = session.get(Job, parse_id("job", identifiers[0]))
                cancelled = session.get(Job, parse_id("job", identifiers[1]))
                assert remaining.state == "retry_wait" and remaining.priority == 31 and remaining.retry_count == 1
                assert cancelled.state == "cancelled" and cancelled.retry_after is None
                assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1
            assert JobAdministration(sessions, runtime_settings).list(QueueQuery())["data"]
            report["checks"] = browser_report["checks"]
            report["database_mutations_verified"] = True
            report["runner_intentionally_not_started"] = True
            report["passed"] = True
        finally:
            processes.close()
            report["cleanup"]["processes"] = all(item["stopped"] for item in processes.stops)
            if engine is not None:
                engine.dispose()
            if created:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                report["cleanup"]["database"] = True
            admin.execute("SELECT pg_advisory_unlock(634720182)")
            for name in ("fixture.json", "empty.dump"):
                (root / name).unlink(missing_ok=True)
            for name in ("objects", "spool"):
                target = root / name
                if target.exists():
                    assert root.resolve().parent == (ROOT / ".local/confirmed-retry-browser").resolve()
                    assert target.resolve().parent == root.resolve() and not target.is_symlink()
                    target.rmdir()
            report["cleanup"]["private_fixture"] = not (root / "fixture.json").exists()
            report["duration_seconds"] = round(time.monotonic() - started, 2)
            report["owned_processes"] = processes.stops
            report["passed"] = report["passed"] and all(report["cleanup"].values())
            native.write(EVIDENCE / "confirmed-retry-browser-fixture.json", report)
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "cleanup": report["cleanup"]}))


if __name__ == "__main__":
    main()
