"""Known lock faults against this run's actual HTTPS API, never a live catalog."""
from __future__ import annotations

import json
import re
import secrets
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
from generation_infrastructure.models import AdminUser
from generation_infrastructure.security import password_hash
from sqlalchemy.orm import Session, sessionmaker


def check(value: Any, code: str) -> None:
    if not value:
        raise RuntimeError(code)


def blocked(connection: psycopg.Connection[Any], blocker: int, request: Future[httpx.Response]) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        row = connection.execute("SELECT a.wait_event_type,b.state FROM pg_stat_activity a JOIN pg_stat_activity b ON b.pid=%s WHERE a.datname=current_database() AND %s=ANY(pg_blocking_pids(a.pid))", (blocker, blocker)).fetchone()
        if row:
            check(row == ("Lock", "idle in transaction"), "database_probe_blocker_state_invalid")
            return {"chain_observed": True, "wait_type": "Lock", "blocker_state": "idle in transaction"}
        check(not request.done(), "database_probe_wait_ended_before_observation")
        time.sleep(0.02)
    raise RuntimeError("database_probe_blocker_not_observed")


def log_event(path: Path, request_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 1048576))
            payload = stream.read(1048576).decode("utf-8", errors="replace")
        for line in payload.splitlines():
            if line.startswith("{"):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "http_request" and event.get("request_id") == request_id:
                    return event
        time.sleep(0.05)
    raise RuntimeError("database_probe_event_not_observed")


def exercise(client: httpx.Client, options: dict[str, Any], database: str, factory: sessionmaker[Session], gateway_id: str, gateway_token: str, log: Path) -> list[dict[str, Any]]:
    check(options.get("host") == "127.0.0.1" and options.get("port") == 55438 and re.fullmatch(r"render_control_native_[a-f0-9]{12}", database), "database_probe_scope_invalid")
    offset = log.stat().st_size
    cases = []
    headers = {"Authorization": "Bearer " + gateway_token}
    check(client.get("/gateway/v1/settlements", headers=headers).status_code == 200, "database_probe_gateway_not_ready")
    username, password = "diagnostics-" + uuid4().hex[:12], secrets.token_urlsafe(32)
    with factory.begin() as session:
        session.add(AdminUser(username=username, password_hash=password_hash(password), role="administrator"))
    login = client.post("/admin/api/v1/session", json={"username": username, "password": password}, headers={"Origin": str(client.base_url).rstrip("/")})
    check(login.status_code == 200, "database_probe_admin_login_failed")
    try:
        with psycopg.connect(**{**options, "dbname": database, "options": "-c statement_timeout=1000 -c lock_timeout=1000"}) as holder, psycopg.connect(**{**options, "dbname": database, "options": "-c statement_timeout=1000 -c lock_timeout=100"}, autocommit=True) as monitor, ThreadPoolExecutor(max_workers=1) as pool:
            check(holder.execute("SELECT current_database(),host(inet_server_addr()),current_setting('port')").fetchone() == (database, "127.0.0.1", "55438"), "database_probe_identity_mismatch")
            holder.rollback()
            rate_id = "gateway:" + str(UUID(gateway_id.removeprefix("gateway_")))
            for operation, path, status in (("gateway_auth", "/gateway/v1/settlements", 500), ("audit_snapshot", "/admin/api/v1/audit-events", 503)):
                with holder.transaction():
                    if operation == "gateway_auth":
                        before = holder.execute("SELECT tokens,updated_at,recent FROM rate_states WHERE identity=%s FOR UPDATE", (rate_id,)).fetchone()
                        check(before is not None, "database_probe_rate_state_missing")
                    else:
                        holder.execute("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE")
                    pid = holder.execute("SELECT pg_backend_pid()").fetchone()[0]
                    request = pool.submit(client.get, path, headers=headers if operation == "gateway_auth" else {}, timeout=10)
                    chain = blocked(monitor, pid, request)
                    response = request.result(timeout=12)
                    check(response.status_code == status, "database_probe_unexpected_status")
                    event = log_event(log, response.headers["x-request-id"])
                    check(event.get("database_failure") == {"operation": operation, "reason": "lock_unavailable", "sqlstate": "55P03", "connection_invalidated": False}, "database_probe_wrong_classification")
                    check(event["completion"] == "completed" and "database_failure" not in response.text, "database_probe_diagnostic_boundary_failed")
                    if operation == "gateway_auth":
                        check(holder.execute("SELECT tokens,updated_at,recent FROM rate_states WHERE identity=%s", (rate_id,)).fetchone() == before, "database_probe_failed_request_consumed_rate")
                recovered = client.get(path, headers=headers if operation == "gateway_auth" else {}, timeout=10)
                check(recovered.status_code == 200, "database_probe_recovery_failed")
                check("database_failure" not in log_event(log, recovered.headers["x-request-id"]), "database_probe_context_leaked")
                cases.append({"case": operation + "_known_lock_fault_and_recovery", "passed": True, "http_status": status, "sqlstate": "55P03", "automatic_retry": False, "budget_increased": False, **chain})
        check(log.stat().st_size - offset <= 1048576, "database_probe_log_budget_exceeded")
        with log.open("rb") as stream:
            stream.seek(offset)
            payload = stream.read(1048576).decode("utf-8", errors="replace")
        check(not any(value in payload for value in ("Traceback (most recent call last)", "Exception in ASGI application", "SELECT rate_states", gateway_token, password)), "database_probe_private_error_leaked")
        return cases
    finally:
        client.cookies.clear()
