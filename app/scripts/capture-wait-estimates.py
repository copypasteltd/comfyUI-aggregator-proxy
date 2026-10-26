"""Capture the built Admin against an owned native API and synthetic PostgreSQL observations."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.models import AdminUser, Attempt, Channel, Job, Node
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


def module(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError("owned_helper_missing")
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--browser-python", type=Path, default=Path(sys.base_prefix) / "python.exe")
    args = parser.parse_args()
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", args.run_id) is None:
        raise SystemExit("invalid_run_id")
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if (url.host, url.port, url.database) != ("127.0.0.1", 55438, "render_control"):
        raise SystemExit("owned_native_database_required")
    native = module("wait94_owned_processes", "scripts/test-sub2api-native.py")
    ci = module("wait94_source_inputs", "scripts/native_ci.py")
    sys.path.insert(0, str(ROOT / "tests"))
    fixtures = module("wait94_test_catalog", "tests/conftest.py")
    observations = module("wait94_test_observations", "tests/integration/test_queue_wait_estimates.py")
    private = ROOT / ".local/wait-estimate-browser" / args.run_id
    public = ROOT.parent / "docs/evidence/2026-10-26" / args.run_id
    private.mkdir(parents=True, exist_ok=False)
    public.mkdir(parents=True, exist_ok=False)
    source = ci.snapshot()
    database = "render_wait_" + uuid4().hex[:12]
    origin = "https://127.0.0.1:" + str(native.port())
    password, pepper = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    env = {**os.environ, "PYTHONUTF8": "1", "GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": url.set(database=database).render_as_string(hide_password=False),
           "GEN_CREDENTIAL_PEPPER": pepper, "GEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GEN_STORAGE_BACKEND": "local_development",
           "GEN_LOCAL_STORAGE_ROOT": str(private / "objects"), "GEN_SPOOL_ROOT": str(private / "spool"), "GEN_PUBLIC_ORIGIN": origin}
    env.pop("GEN_AUDIT_RETENTION_DAYS", None)
    owned = native.Processes(private)
    report: dict[str, Any] = {"format_version": 1, "at": datetime.now(UTC).isoformat(), "passed": False, "scope": "Built Admin and actual native HTTPS/API/PostgreSQL; synthetic observed runtimes, no GPU or production resources",
        "source_sha256": source, "sources_unchanged": False, "real_resource_acceptance": False, "full_acceptance": "pending", "virtualization_used": False,
        "cleanup": {"processes": False, "database": False, "credentials": False, "heartbeat_thread": False}}
    (public / "inputs.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    created, engine = False, None
    stopped, heartbeat = Event(), None
    try:
        with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname="postgres", autocommit=True) as admin:
            try:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                created = True
                with (private / "migration.log").open("wb") as log:
                    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW, check=True)  # noqa: S603 -- fixed native migration in owned database
                engine = create_engine(env["GEN_DATABASE_URL"], connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000 -c lock_timeout=1000"})
                sessions = sessionmaker(engine, expire_on_commit=False)
                catalog = fixtures.catalog.__wrapped__(sessions)
                data = observations.seed(sessions, catalog, samples=20, runtime_scale=3)
                with sessions.begin() as session:
                    session.add(AdminUser(username="demo-observer", password_hash=password_hash(password), role="observer"))
                    target = session.get(Job, data["job"])
                    assert target is not None
                    target.model_alias = "合成演示 · 已有运行样本"
                    execution = deepcopy(target.execution)
                    execution["parameters"]["quality"] = "high"
                    unknown = Job(tenant_id=target.tenant_id, project_id=target.project_id, key_id=target.key_id, release_id=target.release_id,
                        model_alias="合成演示 · 样本尚不足", task_type=target.task_type, state="queued", request={}, execution=execution, request_fingerprint="b" * 64,
                        execution_fingerprint=fingerprint(execution), eligible_channels=target.eligible_channels, accepted_at=utcnow(), deadline=target.deadline, accepted_period=target.accepted_period)
                    session.add(unknown)
                    session.flush()
                    unknown_id = unknown.id

                def observe() -> None:
                    while not stopped.wait(2):
                        try:
                            with sessions.begin() as session:
                                node = session.get(Node, catalog["node_id"])
                                assert node is not None
                                inventory = deepcopy(node.inventory)
                                inventory["capacity"]["observed_at_ms"] = int(utcnow().timestamp() * 1000)
                                node.inventory = inventory
                                attempt = session.get(Attempt, data["active"])
                                if attempt is not None:
                                    attempt.lease_expires_at = utcnow() + timedelta(seconds=60)
                                for channel in session.scalars(select(Channel)):
                                    channel.observation = {**channel.observation, "queue_observed_at": int(utcnow().timestamp())}
                        except Exception:
                            stopped.set()

                heartbeat = Thread(target=observe, name="owned-synthetic-capacity", daemon=True)
                heartbeat.start()
                process = owned.start("wait-estimate-api", [sys.executable, "-m", "uvicorn", "control_api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", origin.rsplit(":", 1)[1],
                    "--ssl-keyfile", str(ROOT / ".local/tls/localhost.key"), "--ssl-certfile", str(ROOT / ".local/tls/localhost.crt"), "--no-access-log", "--no-proxy-headers"], env)
                with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(ROOT / ".local/tls/localhost.crt")), trust_env=False) as http:
                    native.wait_http(http, "/health/ready", process)
                with sessions.begin() as session:
                    attempt = session.get(Attempt, data["active"])
                    assert attempt is not None
                    attempt.created_at = utcnow() - timedelta(seconds=60)
                secret = private / "browser.secret.json"
                secret.write_text(json.dumps({"origin": origin, "password": password, "job_id": public_id("job", data["job"]), "unknown_id": public_id("job", unknown_id), "public": str(public), "private": str(private)}), encoding="utf-8")
                browser_env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("GEN_", "PYTHON"))}
                gate = ci.Gate("wait_estimate_browser", [str(args.browser_python.resolve(strict=True)), "-X", "utf8", str(ROOT / "scripts/record-wait-estimates.py"), "--fixture", str(secret)], ROOT, 180)
                report["browser_process"] = ci.execute(gate, private / "browser.log", browser_env, limit=2 * 1024**2)
                if (public / "browser.json").is_file():
                    report.update(json.loads((public / "browser.json").read_text(encoding="utf-8")))
                if report["browser_process"]["status"] != "passed":
                    raise RuntimeError("owned_browser_failed")
            finally:
                stopped.set()
                if heartbeat is not None:
                    heartbeat.join(timeout=6)
                report["cleanup"]["heartbeat_thread"] = heartbeat is None or not heartbeat.is_alive()
                owned.close()
                report["cleanup"]["processes"] = all(item["stopped"] for item in owned.stops)
                if engine is not None:
                    engine.dispose()
                (private / "browser.secret.json").unlink(missing_ok=True)
                report["cleanup"]["credentials"] = not (private / "browser.secret.json").exists()
                if created:
                    admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (database,))
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
                report["cleanup"]["database"] = True
    except Exception as error:
        report["passed"] = False
        report["error_class"] = type(error).__name__
        import traceback
        (private / "failure.trace.txt").write_text("".join(traceback.format_exception(error)), encoding="utf-8")
    report["sources_unchanged"] = ci.snapshot() == source
    report["passed"] = bool(report["passed"] and report["sources_unchanged"] and all(report["cleanup"].values()))
    (public / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cleanup": report["cleanup"], "report": str(public.relative_to(ROOT.parent) / "summary.json")}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
