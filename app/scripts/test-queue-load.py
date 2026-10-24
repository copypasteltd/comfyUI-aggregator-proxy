"""Native HTTPS QC15: 20 synthetic protocol workers, 500 jobs, 3 browser sessions.

Owns one temporary database and API/browser process trees. Never targets a real
Worker/ComfyUI/GPU, never mutates the development database, never uses a shell.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from generation_application.ports import BusinessIdentity
from generation_contracts.defaults import default_key_policy, default_limits
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import (
    AdminUser,
    ApiCredential,
    ApiKey,
    Channel,
    Gpu,
    Node,
    Project,
    PublicModel,
    Tenant,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret, password_hash, verifier
from generation_infrastructure.settings import Settings
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


def capacity(sequence: int) -> dict[str, Any]:
    return {"protocol": "native-capacity-v1", "sequence": sequence, "observed_ago_ms": 0, "free_disk_bytes": 100 * 1024**3, "available_memory_bytes": 8 * 1024**3, "spool_bytes": 0, "reserved_bytes": 0, "pending_uploads": 0, "minimum_free_disk_bytes": 5 * 1024**3, "minimum_available_memory_bytes": 1024**3, "maximum_spool_bytes": 20 * 1024**3, "generation_reserve_bytes": 2 * 1024**3, "maximum_uploads": 2, "reasons": [], "status": "ready"}


def seed(factory: Any, pepper: str, password: str) -> tuple[BusinessIdentity, list[dict[str, Any]], str]:
    workers: list[dict[str, Any]] = []
    with factory.begin() as session:
        limits = default_limits()
        for name in limits:
            limits[name] = {"mode": "bounded", "value": 10000}
        tenant = Tenant(name="QC15 synthetic tenant", limits=limits)
        session.add(tenant)
        session.flush()
        project = Project(tenant_id=tenant.id, name="QC15 synthetic project", limits=limits)
        session.add(project)
        session.flush()
        policy = default_key_policy()
        policy["limits"] = deepcopy(limits)
        for name in ("models", "workflow_releases", "channels"):
            policy[name] = {"mode": "all_authorized", "selected_ids": []}
        key = ApiKey(tenant_id=tenant.id, project_id=project.id, name="QC15 synthetic key", policy=policy)
        workflow = Workflow(tenant_id=tenant.id, project_id=project.id, name="QC15 synthetic Workflow", task_type="text_to_image", draft={})
        session.add_all([key, workflow, AdminUser(username="queue-load-admin", password_hash=password_hash(password), role="administrator")])
        session.flush()
        credential_id, token = issue_secret("sk-gen")
        session.add(ApiCredential(key_id=key.id, identifier=credential_id, verifier=verifier(token, pepper)))
        manifest = {"task_type": "text_to_image", "supported_parameters": [], "workflow_api": {"1": {"class_type": "SyntheticOutput", "inputs": {}}}}
        release = WorkflowRelease(tenant_id=tenant.id, project_id=project.id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        session.add(release)
        session.flush()
        channels = []
        for index in range(20):
            identifier, secret = issue_secret("worker")
            boot = uuid4()
            node = Node(name=f"QC15 protocol Worker {index:02}", credential_identifier=identifier, credential_verifier=verifier(secret, pepper), current_boot_id=boot, observed_at=utcnow())
            session.add(node)
            session.flush()
            gpu = Gpu(node_id=node.id, hardware_id=f"SYNTHETIC-QC15-{index:02}", name="Synthetic protocol GPU")
            session.add(gpu)
            session.flush()
            own_channels = []
            for second in range(2):
                channel = Channel(tenant_id=tenant.id, project_id=project.id, node_id=node.id, gpu_id=gpu.id, name=f"QC15 channel {index:02}/{second}", base_url="http://127.0.0.1:1", enabled=True, desired_revision=1, applied_revision=1, verified_revision=1, observed_at=utcnow())
                session.add(channel)
                session.flush()
                session.add(WorkflowBinding(tenant_id=tenant.id, project_id=project.id, channel_id=channel.id, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest, observed_at=utcnow()))
                channels.append(public_id("ch", channel.id))
                own_channels.append(public_id("ch", channel.id))
            workers.append({"node_id": public_id("node", node.id), "boot_id": str(boot), "secret": secret, "channels": own_channels, "sequence": 1})
        session.add(PublicModel(tenant_id=tenant.id, project_id=project.id, alias="qc15-synthetic-image", task_type="text_to_image", release_id=release.id, channel_ids=channels, defaults={"size": "1024x1024", "quality": "standard"}, published=True))
        identity = BusinessIdentity(tenant.id, project.id, key.id, policy)
    return identity, workers, token


def main() -> None:
    assert os.name == "nt"
    spec = importlib.util.spec_from_file_location("queue_native_helpers", ROOT / "scripts/test-sub2api-native.py")
    assert spec and spec.loader
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    run_id, started = uuid4().hex[:12], time.monotonic()
    root = ROOT / ".local/queue-load" / run_id
    root.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {"passed": False, "run_id": run_id, "started_at": datetime.now(UTC).isoformat(), "virtualization_used": False, "physical_fleet_validated": False, "real_comfyui": False, "synthetic_http_workers": 20, "unfinished_jobs": 500, "admin_browser_sessions": 3, "backups": [], "cleanup": {"processes": False, "database": False, "credentials": False}, "worker_errors": [], "worker_heartbeats": 0, "worker_renewals": 0}
    source_paths = [Path(__file__), ROOT / "scripts/browser-queue-load.py", ROOT / "scripts/test-sub2api-native.py"]
    for directory in ("packages", "apps/control_api", "apps/control_runner", "apps/windows_worker", "migrations"):
        source_paths.extend(sorted((ROOT / directory).rglob("*.py")))
    source_paths.extend(sorted((ROOT / "packages/contracts/src/generation_contracts/schemas").glob("*.json")))
    source_paths.extend(sorted((ROOT / "apps/admin_web/dist").rglob("*")))
    hashes = {str(path.relative_to(ROOT)): native.sha(path) for path in source_paths if path.is_file()}
    report["sources_sha256"] = hashes
    processes, admin, engine, client, pool = native.Processes(root), None, None, None, None
    created, failure, browser = False, None, None
    stopped, futures = Event(), []
    database = "render_queue_load_" + run_id
    try:
        settings = Settings()
        url = make_url(settings.database_url.get_secret_value())
        assert settings.environment == "development" and (url.host, url.port, url.database) == ("127.0.0.1", 55438, "render_control")
        assert re.fullmatch(r"render_queue_load_[a-f0-9]{12}", database)
        options = {"host": url.host, "port": url.port, "user": url.username, "password": url.password, "connect_timeout": 5, "sslmode": "disable"}
        admin = psycopg.connect(**{**options, "dbname": "postgres"}, autocommit=True)
        assert admin.execute("SELECT current_database(),host(inet_server_addr()),current_setting('port')").fetchone() == ("postgres", "127.0.0.1", "55438")
        assert admin.execute("SELECT pg_try_advisory_lock(634720182)").fetchone()[0]
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
        created = True
        env = {**os.environ, "PGPASSWORD": url.password or "", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        tools = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
        backup = root / "empty-before-ddl.dump"
        dsn = psycopg.conninfo.make_conninfo(**{k: v for k, v in {**options, "dbname": database}.items() if k != "password" and v is not None})
        for command in ([str(tools / "pg_dump.exe"), "--format=custom", "--file=" + str(backup), "--dbname=" + dsn, "--no-password"], [str(tools / "pg_restore.exe"), "--list", str(backup)]):
            result = subprocess.run(command, env=env, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- exact created database backup/list
            assert result.returncode == 0, "pre_ddl_backup_failed"
        report["backups"].append({"sha256": native.sha(backup), "archive_verified_before_ddl": True, "bytes": backup.stat().st_size})
        control_url = url.set(database=database).render_as_string(hide_password=False)
        pepper, password = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        origin = f"https://127.0.0.1:{native.port()}"
        env.update({"GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": control_url, "GEN_CREDENTIAL_PEPPER": pepper, "GEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GEN_PUBLIC_ORIGIN": origin, "GEN_STORAGE_BACKEND": "local_development", "GEN_LOCAL_STORAGE_ROOT": str(root / "objects"), "GEN_SPOOL_ROOT": str(root / "spool")})
        with (root / "migrations.log").open("wb") as stream:
            result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- owned temporary database
            assert result.returncode == 0, "temporary_migration_failed"
        engine = create_engine(control_url, pool_size=20, max_overflow=5, hide_parameters=True)
        factory = sessionmaker(engine, expire_on_commit=False)
        identity, workers, business_token = seed(factory, pepper, password)
        cert, key = ROOT / ".local/tls/localhost.crt", ROOT / ".local/tls/localhost.key"
        assert cert.is_file() and key.is_file()
        api = processes.start("queue-control-api", [sys.executable, "-m", "uvicorn", "control_api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", origin.rsplit(":", 1)[1], "--ssl-certfile", str(cert), "--ssl-keyfile", str(key), "--no-access-log", "--no-proxy-headers"], env)
        client = httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(cert)), timeout=12, trust_env=False)
        native.wait_http(client, "/health/ready", api)
        for worker in workers:
            worker["client"] = httpx.Client(base_url=origin, headers={"Authorization": "Bearer " + worker["secret"]}, verify=ssl.create_default_context(cafile=str(cert)), timeout=12, trust_env=False)
        def heartbeat(worker: dict[str, Any]) -> None:
            while not stopped.is_set():
                try:
                    worker["sequence"] += 1
                    response = worker["client"].post("/internal/v1/heartbeat", json={"protocol_version": "0.4.0", "boot_id": worker["boot_id"], "observations": [], "capacity": capacity(worker["sequence"])})
                    assert response.status_code == 200, "heartbeat_status"
                    report["worker_heartbeats"] += 1
                    claim = worker.get("claim")
                    if claim:
                        response = worker["client"].post(f"/internal/v1/attempts/{claim['attempt_id']}/renew", json={"protocol_version": "0.4.0", "boot_id": worker["boot_id"], "fencing_token": claim["fencing_token"]})
                        assert response.status_code == 200, "renew_status"
                        report["worker_renewals"] += 1
                except Exception as error:
                    report["worker_errors"].append(type(error).__name__)
                    stopped.set()
                stopped.wait(2)
        pool = ThreadPoolExecutor(max_workers=20)
        futures = [pool.submit(heartbeat, worker) for worker in workers]
        # Real admission and quotas, on the isolated synthetic catalog.
        with ThreadPoolExecutor(max_workers=10) as seeds:
            repository = JobRepository(factory)
            jobs = list(seeds.map(lambda number: repository.create(identity, {"model": "qc15-synthetic-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic QC15 queue workload"}}, "qc15-" + str(number), "qc15-load"), range(500)))
        assert len({job["id"] for job in jobs}) == 500
        def claim(worker: dict[str, Any]) -> dict[str, Any]:
            claim_started = time.monotonic()
            response = worker["client"].post("/internal/v1/claims", json={"protocol_version": "0.4.0", "boot_id": worker["boot_id"], "request_id": uuid4().hex})
            worker["claim_seconds"] = time.monotonic() - claim_started
            assert response.status_code == 200 and response.json()["lease"], "claim_failed"
            worker["claim"] = response.json()["lease"]
            return worker["claim"]
        with ThreadPoolExecutor(max_workers=20) as claims:
            leases = list(claims.map(claim, workers))
        assert len({lease["attempt_id"] for lease in leases}) == len({lease["channel"]["gpu_id"] for lease in leases}) == 20
        report["unique_claims"] = 20
        claim_seconds = sorted(worker["claim_seconds"] for worker in workers)
        report["concurrent_claim_http"] = {"samples": 20, "p95_seconds": round(claim_seconds[18], 4), "max_seconds": round(claim_seconds[-1], 4)}
        with factory() as session:
            cursors = {kind: (virtual, sequence) for kind, virtual, sequence in session.execute(text("SELECT scope_kind,dispatch_virtual_milliseconds,dispatch_sequence FROM quota_buckets WHERE period='active' AND scope_kind IN ('0_global','1_tenant')"))}
            assert cursors == {"0_global": (1_140_000, 20), "1_tenant": (1_200_000, 20)}
            attempts = session.scalar(text("SELECT count(*) FROM attempts"))
            sequences = session.scalar(text("SELECT count(DISTINCT (channel_snapshot->'dispatch'->>'sequence')::bigint) FROM attempts"))
            assert attempts == sequences == 20
        report["dispatch_accounting"] = {"global_sequence": 20, "global_virtual_milliseconds": 1_140_000, "tenant_virtual_milliseconds": 1_200_000, "attempts": attempts, "unique_sequences": sequences, "actual_gpu_time_measured": False}
        login = client.post("/admin/api/v1/session", json={"username": "queue-load-admin", "password": password}, headers={"Origin": origin})
        assert login.status_code == 200
        initial = client.get("/admin/api/v1/queues/snapshot").json()
        assert initial["unfinished"] == 500 and initial["global_unassigned_queued"] == 480 and initial["occupied_physical_gpus"] == 20 and len(initial["channels"]) == 40
        report["initial_snapshot"] = {name: initial[name] for name in ("unfinished", "global_unassigned_queued", "occupied_physical_gpus", "states")}
        for token in (workers[0]["secret"], business_token):
            isolated = httpx.get(origin + "/admin/api/v1/queues/filter-options?kind=tenant", headers={"Authorization": "Bearer " + token}, verify=ssl.create_default_context(cafile=str(cert)), timeout=10, trust_env=False)
            assert isolated.status_code == 401
        native.write(root / "browser-config.json", {"origin": origin, "username": "queue-load-admin", "password": password})
        browser_python = Path("C:/Python313/python.exe")
        assert browser_python.is_file()
        browser = processes.start("queue-load-browser", [str(browser_python), str(ROOT / "scripts/browser-queue-load.py"), str(root)], env)
        deadline = time.monotonic() + 45
        while not (root / "browser-ready.json").is_file():
            assert browser.poll() is None and time.monotonic() < deadline, "browser_startup_failed"
            time.sleep(0.1)
        for wave in range(10):
            before_commit = time.monotonic()
            for worker in workers[wave*2:wave*2+2]:
                lease = worker["claim"]
                body = {"protocol_version": "0.4.0", "event_id": str(uuid4()), "node_id": worker["node_id"], "boot_id": worker["boot_id"], "job_id": lease["job_id"], "attempt_id": lease["attempt_id"], "fencing_token": lease["fencing_token"], "journal_seq": 1, "observed_at": int(time.time()), "type": "preparing", "payload": {"execution_digest": lease["execution_digest"]}}
                response = worker["client"].post(f"/internal/v1/attempts/{lease['attempt_id']}/events", json=body)
                assert response.status_code == 200, "preparing_event_failed"
            phase = root / "phase.json"
            native.write(root / "phase-next.json", {"wave": wave, "preparing": (wave+1)*2, "before_commit_monotonic": before_commit})
            (root / "phase-next.json").replace(phase)
            deadline = time.monotonic() + 18
            while not (root / f"ack-{wave}.json").is_file():
                assert browser.poll() is None and time.monotonic() < deadline and not report["worker_errors"], "browser_update_missing"
                time.sleep(0.05)
        browser.wait(timeout=35)
        assert browser.returncode == 0, "browser_load_failed"
        browser_report = json.loads((root / "browser-report.json").read_text(encoding="utf-8"))
        assert browser_report["passed"] and len(browser_report["samples"]) == 30
        report["browser"] = browser_report
        final = client.get("/admin/api/v1/queues/snapshot").json()
        assert final["unfinished"] == 500 and final["states"] == {"queued": 480, "preparing": 20} and final["occupied_physical_gpus"] == 20
        assert not report["worker_errors"] and report["worker_heartbeats"] >= 40 and report["worker_renewals"] >= 20
        report["source_unchanged"] = all(native.sha(ROOT / name) == digest for name, digest in hashes.items())
        assert report["source_unchanged"]
        report["passed"] = True
    except BaseException as error:
        failure = error
        report["failure"] = {"type": type(error).__name__, "code": str(error) if isinstance(error, AssertionError) and len(str(error)) < 100 else "private_diagnostics_retained"}
    finally:
        stopped.set()
        if pool:
            pool.shutdown(wait=True, cancel_futures=True)
            for future in futures:
                future.result()
        for worker in locals().get("workers", []):
            if worker.get("client"):
                worker["client"].close()
        if client:
            client.close()
        try:
            processes.close()
            report["cleanup"]["processes"] = True
        except BaseException as error:
            report["passed"] = False
            failure = failure or error
        report["owned_processes"] = processes.stops
        if engine:
            engine.dispose()
        if admin:
            try:
                if created and report["cleanup"]["processes"]:
                    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                    report["cleanup"]["database"] = True
                admin.execute("SELECT pg_advisory_unlock(634720182)")
            finally:
                admin.close()
        if report["cleanup"]["processes"]:
            (root / "browser-config.json").unlink(missing_ok=True)
            for name in ("objects", "spool"):
                target = (root / name).resolve()
                assert root.resolve().parent == (ROOT / ".local/queue-load").resolve() and target.parent == root.resolve()
                if target.exists():
                    shutil.rmtree(target)
            report["cleanup"]["credentials"] = True
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        report["passed"] = report["passed"] and all(report["cleanup"].values())
        native.write(root / "report.json", report)
        evidence = ROOT.parent / "docs/evidence/2026-10-23/queue-observability-load.json"
        native.write(evidence, report)
        print(json.dumps({"passed": report["passed"], "run_id": run_id, "duration_seconds": report["duration_seconds"], "cleanup": report["cleanup"], "failure": report.get("failure")}), flush=True)
    if failure:
        raise RuntimeError("queue_load_failed_see_private_diagnostics") from None


if __name__ == "__main__":
    main()
