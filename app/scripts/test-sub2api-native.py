"""Run stock Sub2API, control API and Windows Worker as owned native processes.

The Redis and Comfy services are explicitly synthetic protocol fixtures. This
script cannot target an existing database, remote machine or customer catalog.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.models import (
    AdminUser,
    Channel,
    Gpu,
    ManagementOperation,
    Node,
    WorkerCommand,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from prometheus_client.parser import text_string_to_metric_families
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence" / datetime.now().date().isoformat()


def management_recovery_case(factory: Any, catalog: dict[str, Any], root: Path, processes: Any, worker: Any, env: dict[str, str], outcomes: list[dict[str, Any]]) -> None:
    """Inject only into this run's stopped synthetic Worker and private DB."""
    from datetime import timedelta

    from windows_worker.journal import Journal

    processes.stop(worker)
    state = root / "worker-state"
    before = {str(p.relative_to(state)): sha(p) for p in (state / "config").rglob("*") if p.is_file()}
    with factory.begin() as session:
        actor = AdminUser(username="native-command-recovery", password_hash=secrets.token_hex(32), role="administrator")
        session.add(actor)
        session.flush()
        actor_id = actor.id

    def inject(*, completed: bool) -> Any:
        with factory.begin() as session:
            node = session.get(Node, catalog["node_uuid"])
            channel = session.get(Channel, catalog["channel_uuid"])
            assert node and channel
            operation = ManagementOperation(actor_id=actor_id, command="apply_channel_config")
            session.add(operation)
            session.flush()
            payload = {"revision": channel.desired_revision, "base_url": channel.base_url, "channel_id": public_id("ch", channel.id), "gpu_id": public_id("gpu", channel.gpu_id)}
            command = WorkerCommand(operation_id=operation.id, node_id=node.id, channel_id=channel.id, command="apply_channel_config", payload=payload, state="running", boot_id=node.current_boot_id, lease_expires_at=utcnow() - timedelta(seconds=1))
            session.add(command)
            session.flush()
            session.get(Gpu, channel.gpu_id).management_hold = command.id
            identifier, revision = command.id, channel.desired_revision
        journal = Journal(state)
        try:
            journal.command_intent(public_id("cmd", identifier), fingerprint({"type": "apply_channel_config", "payload": payload}))
            if completed:
                journal.command_result(public_id("cmd", identifier), {"success": True, "applied_revision": revision})
        finally:
            journal.close()
        return identifier

    completed = inject(completed=True)
    command = [sys.executable, "-m", "windows_worker.main", "--config", str(root / "worker.json")]
    worker = processes.start("worker-command-recovery", command, env)
    deadline = time.monotonic() + 30
    while True:
        check(worker.poll() is None, "command_recovery_worker_exited")
        with factory() as session:
            row = session.get(WorkerCommand, completed)
            finished = row.state == "failed" and row.receipt == {"success": False, "error_code": "command_revalidation_required"}
        with closing(sqlite3.connect(state / "journal.sqlite3")) as journal:
            ack = journal.execute("SELECT cloud_ack FROM commands WHERE id=?", (public_id("cmd", completed),)).fetchone()
        if finished and ack and ack[0] == 1:
            break
        check(time.monotonic() < deadline, "command_recovery_ack_timeout")
        time.sleep(0.2)
    with factory() as session:
        channel = session.get(Channel, catalog["channel_uuid"])
        check(channel.verified_revision is None and session.get(Gpu, channel.gpu_id).management_hold is None, "command_recovery_restored_old_approval")
    outcomes.append({"name": "native_management_durable_result_cross_boot_reconciled_without_reexecution", "passed": True})
    processes.stop(worker)
    unknown = inject(completed=False)
    worker = processes.start("worker-command-unknown", command, env)
    deadline = time.monotonic() + 30
    while True:
        check(worker.poll() is None, "command_unknown_worker_exited")
        with factory() as session:
            node = session.get(Node, catalog["node_uuid"])
            row = session.get(WorkerCommand, unknown)
            registered = node.current_boot_id != row.boot_id
        if registered:
            break
        check(time.monotonic() < deadline, "command_unknown_boot_timeout")
        time.sleep(0.2)
    time.sleep(5)  # More than two management iterations; bounded fixture wait.
    with factory() as session:
        row = session.get(WorkerCommand, unknown)
        channel = session.get(Channel, catalog["channel_uuid"])
        check(row.state == "running" and row.receipt is None and session.get(Gpu, channel.gpu_id).management_hold == unknown, "unknown_management_execution_released")
    with closing(sqlite3.connect(state / "journal.sqlite3")) as journal:
        check(journal.execute("SELECT result,cloud_ack FROM commands WHERE id=?", (public_id("cmd", unknown),)).fetchone() == (None, 0), "unknown_management_intent_executed")
    after = {str(p.relative_to(state)): sha(p) for p in (state / "config").rglob("*") if p.is_file()}
    check(before == after, "management_unknown_or_cached_result_executed_config")
    outcomes.append({"name": "native_management_unknown_intent_keeps_gpu_hold_and_never_reexecutes", "passed": True})


def module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def check(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def remove_fixture_tree(target: Path, run_root: Path) -> None:
    check(run_root.resolve().parent == (ROOT / ".local/native-provider").resolve() and target.resolve().parent == run_root.resolve(), "fixture_cleanup_path_invalid")
    if target.exists():
        def writable(function: Any, filename: str, error: BaseException) -> None:
            # Native Git marks its object files read-only on Windows.
            resolved = Path(filename).resolve()
            check(resolved.is_relative_to(target.resolve()), "fixture_cleanup_child_invalid")
            if not isinstance(error, PermissionError):
                raise error
            os.chmod(resolved, stat.S_IWRITE | stat.S_IREAD)
            function(filename)
        shutil.rmtree(target, onexc=writable)


class Processes:
    """Retain live Popen handles; kill only our live process and descendant tree."""
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.items: list[tuple[str, subprocess.Popen[bytes], Any]] = []
        self.stops: list[dict[str, Any]] = []

    def start(self, name: str, command: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
        stream = (self.directory / (name + ".log")).open("wb")
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- exact owned native executable, no shell
        self.items.append((name, process, stream))
        return process

    def stop(self, process: subprocess.Popen[bytes]) -> None:
        for name, candidate, stream in self.items:
            if candidate is not process or stream.closed:
                continue
            if process.poll() is None:
                # A live Popen handle protects this PID from reuse. /T also stops
                # Python's Windows venv redirector child. Never find/kill by name.
                taskkill = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
                result = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- only retained live owned PID
                check(result.returncode == 0 or process.poll() is not None, "owned_tree_cleanup_failed")
            process.wait(timeout=15)
            stream.close()
            self.stops.append({"name": name, "pid": process.pid, "stopped": process.poll() is not None, "exit_code": process.returncode})

    def close(self) -> None:
        for _, process, _ in reversed(self.items):
            self.stop(process)

    def alive(self) -> None:
        check(all(process.poll() is None for _, process, stream in self.items if not stream.closed), "owned_service_exited")


def wait_http(client: httpx.Client, path: str, process: subprocess.Popen[bytes], *, expected: int = 200) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        check(process.poll() is None, "native_server_startup_failed")
        try:
            if client.get(path).status_code == expected:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("native_server_startup_timeout")


def wait_redis(port_number: int, password: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    command = f"*2\r\n$4\r\nAUTH\r\n${len(password)}\r\n{password}\r\n*1\r\n$4\r\nPING\r\n".encode("ascii")
    while time.monotonic() < deadline:
        check(process.poll() is None, "redis_protocol_fixture_exited")
        try:
            with socket.create_connection(("127.0.0.1", port_number), timeout=1) as connection:
                connection.sendall(command)
                with connection.makefile("rb") as stream:
                    check(stream.readline(256) == b"+OK\r\n" and stream.readline(256) == b"+PONG\r\n", "redis_protocol_auth_failed")
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("redis_protocol_startup_timeout")


def seed_upstream(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    user = conn.execute("INSERT INTO users(email,password_hash,balance) VALUES (%s,'synthetic-protocol-hash',100) RETURNING id", ("native-" + uuid4().hex + "@invalid.test",)).fetchone()[0]
    group = conn.execute("INSERT INTO groups(name,allow_image_generation) VALUES (%s,TRUE) RETURNING id", ("native-protocol-" + uuid4().hex,)).fetchone()[0]
    credentials, keys = [], []
    for index in range(2):
        token = "sk-" + secrets.token_urlsafe(40)
        keys.append(conn.execute("INSERT INTO api_keys(user_id,group_id,key,name) VALUES (%s,%s,%s,%s) RETURNING id", (user, group, token, f"Protocol key {index}")).fetchone()[0])
        credentials.append(token)
    account = conn.execute("INSERT INTO accounts(name,platform,type,schedulable) VALUES ('synthetic-protocol','openai','apikey',FALSE) RETURNING id").fetchone()[0]
    conn.commit()
    return {"user": user, "group": group, "keys": keys, "credentials": credentials, "account": account}


class NativeCatalog:
    """Admin HTTP publication and automatic Provider receipts; never fake applied."""

    def __init__(self, api: httpx.Client, gateway: str, upstream: dict[str, Any], outcomes: list[dict[str, Any]]) -> None:
        self.api, self.path, self.upstream, self.outcomes = api, "/admin/api/v1/gateways/" + gateway + "/catalog", upstream, outcomes
        response = api.get("/admin/api/v1/public-models", params={"status": "published", "page": 1})
        check(response.status_code == 200 and len(response.json()["data"]) == 6, "catalog_native_models_missing")
        self.models = response.json()["data"]
        self.first: dict[str, Any] = {}

    def publish(self, expected: int, price: str = "1.00000000", *, empty: bool = False, unavailable: bool = False) -> dict[str, Any]:
        body = {"instance_id": "native-protocol", "expected_revision": expected, "models": [] if empty else [{"usage_account_id": "9223372036854775807" if unavailable else str(self.upstream["account"]), "group_ids": [str(self.upstream["group"])], "price_version": f"catalog-v{expected + 1}", "unit_price": price if model["task_type"] == "text_to_image" else "1.00000000", "model_bindings": [{"model_id": model["id"], "expected_revision": model["revision"]}]} for model in self.models]}
        token = str(uuid4())
        response = self.api.post(self.path + "/releases", json=body, headers={"If-Match": f'"{expected}"', "Idempotency-Key": token})
        check(response.status_code == 201, "catalog_native_publication_failed_" + str(response.status_code))
        release = response.json()
        check(release["revision"] == expected + 1 and sha_bytes(release["configuration_json"].encode()) == release["digest"], "catalog_native_frozen_bytes_changed")
        replay = self.api.post(self.path + "/releases", json=body, headers={"If-Match": f'"{expected}"', "Idempotency-Key": token})
        check(replay.status_code == 201 and replay.json() == release, "catalog_native_admin_replay_changed")
        return release

    def wait(self, pg: psycopg.Connection[Any], revision: int, *, rejected: bool = False) -> dict[str, Any]:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = self.api.get(self.path)
            check(response.status_code == 200, "catalog_native_state_failed")
            value = response.json()
            row = pg.execute("SELECT confirmed_revision FROM render_control_catalogs WHERE instance='native-protocol'").fetchone()
            pg.commit()
            if rejected and value["status"] == "rejected" and value["last_receipt"]["revision"] == revision:
                check(value["last_receipt"]["error_code"] == "account_unavailable", "catalog_native_rejection_not_fixed")
                return value
            if not rejected and value["confirmed"] and value["confirmed"]["revision"] == revision and row == (revision,):
                return value
            time.sleep(0.25)
        raise RuntimeError("catalog_native_confirmation_timeout")

    def exercise(self, pg: psycopg.Connection[Any], call: Any, poll: Any, jobs: list[str], fixtures: Any, image_body: dict[str, Any]) -> None:
        previous = pg.execute("SELECT count(*),count(*) FILTER(WHERE catalog_revision=1 AND catalog_digest=%s) FROM render_control_intents", (self.first["digest"],)).fetchone()
        pg.commit()
        check(previous == (len(jobs), len(jobs)), "catalog_native_six_types_not_stamped")
        self.outcomes.append({"case": "managed_six_types_and_compatibility_stamp", "passed": True, "intent_count": len(jobs), "revision": 1})
        self.publish(1, "2.00000000")
        self.wait(pg, 2)
        body = {"model": "protocol-text-to-image", "task_type": "text_to_image", "input": {"prompt": "Synthetic managed catalog pricing"}, "parameters": fixtures.defaults("text_to_image")}
        identifier = call("POST", "/v1/jobs", 202, json=body, headers={"Idempotency-Key": "catalog-managed-price"}).json()["id"]
        poll(identifier)
        jobs.append(identifier)
        self.publish(2, "3.00000000", unavailable=True)
        rejected = self.wait(pg, 3, rejected=True)
        check(rejected["confirmed"]["revision"] == 2, "catalog_native_rejection_removed_previous")
        replay = call("POST", "/v1/jobs", 202, json=body, headers={"Idempotency-Key": "catalog-managed-price"}).json()
        check(replay["id"] == identifier, "catalog_native_rejection_lost_original")
        self.outcomes.append({"case": "managed_price_and_dependency_rejection", "passed": True, "confirmed_kept": 2, "rejected": 3})
        self.publish(3, empty=True)
        self.wait(pg, 4)
        call("POST", "/v1/jobs", 404, json=body, headers={"Idempotency-Key": "catalog-withdrawn-new"})
        check(call("POST", "/v1/jobs", 202, json=body, headers={"Idempotency-Key": "catalog-managed-price"}).json()["id"] == identifier, "catalog_native_withdrawal_lost_replay")
        # A completed synchronous request must remain retrievable after the rule
        # is withdrawn, including its protected inline media response.
        check(call("POST", "/v1/images/generations", json=image_body, headers={"Idempotency-Key": "compatible-sync-image"}).json()["data"][0]["b64_json"], "catalog_native_withdrawal_lost_compatible_result")
        check(not any(item["id"].startswith("protocol-") for item in call("GET", "/v1/models").json()["data"]), "catalog_native_withdrawal_discovery_stale")
        check(call("GET", "/v1/jobs/" + jobs[0]).json()["id"] == jobs[0], "catalog_native_withdrawal_orphaned_task")
        self.outcomes.append({"case": "managed_empty_catalog_and_original_content", "passed": True, "revision": 4, "stock_fallback_blocked": True})
        response = self.api.post(self.path + "/replay", json={"action": "rollback", "expected_revision": 4, "source_revision": 1}, headers={"If-Match": '"4"', "Idempotency-Key": str(uuid4())})
        check(response.status_code == 201 and response.json()["revision"] == 5, "catalog_native_rollback_not_new_version")
        self.wait(pg, 5)
        identifier = call("POST", "/v1/jobs", 202, json=body, headers={"Idempotency-Key": "catalog-after-rollback"}).json()["id"]
        poll(identifier)
        jobs.append(identifier)
        facts = pg.execute("SELECT catalog_revision,price_version,unit_price::text FROM render_control_intents ORDER BY created_at,id").fetchall()
        pg.commit()
        check(sum(row == (1, "catalog-v1", "1.00000000") for row in facts) == len(jobs) - 2 and (2, "catalog-v2", "2.00000000") in facts and (5, "catalog-v1", "1.00000000") in facts, "catalog_native_frozen_price_versions_changed")
        self.outcomes.append({"case": "managed_rollback_preserves_original_prices", "passed": True, "new_revision": 5, "source_revision": 1})


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def scenario(client: httpx.Client, upstream: dict[str, Any], pg: psycopg.Connection[Any], control: psycopg.Connection[Any], fixtures: Any, processes: Processes, provider: subprocess.Popen[bytes], restart: Any, root: Path, outcomes: list[dict[str, Any]], pepper: str, comfy: Any, managed: NativeCatalog | None = None, confirmed_retry: bool = False) -> None:
    tokens = upstream["credentials"]
    client.headers["Authorization"] = "Bearer " + tokens[0]

    def call(method: str, path: str, expected: int | tuple[int, ...] = 200, **kwargs: Any) -> httpx.Response:
        processes.alive()
        response = client.request(method, path, **kwargs)
        statuses = (expected,) if isinstance(expected, int) else expected
        if response.status_code not in statuses:
            try:
                error = response.json().get("error", {}).get("code", "unknown")
            except (ValueError, AttributeError):
                error = "non_json"
            raise RuntimeError(f"http_{method}_{path.split('?')[0]}_{response.status_code}_{error}")
        return response

    def poll(identifier: str) -> dict[str, Any]:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            value = call("GET", "/v1/jobs/" + identifier).json()
            if value["status"] == "succeeded":
                return value
            check(value["status"] not in ("failed", "cancelled", "expired", "needs_attention"), "native_job_" + value["status"])
            time.sleep(1)
        raise RuntimeError("native_job_timeout")

    model_ids = {item["id"] for item in call("GET", "/v1/models").json()["data"]}
    check({"protocol-" + task.replace("_", "-") for task in fixtures.TASKS} <= model_ids, "model_catalog_missing")
    outcomes.append({"case": "actual_stock_and_provider_model_discovery", "passed": True, "protocol_models": len(fixtures.TASKS)})
    image = (root / "comfy/output/result.png").read_bytes()
    asset_ids = []
    for index in range(2):
        response = call("POST", "/v1/assets", 201, files={"file": (f"input-{index}.png", image, "image/png")}, headers={"Idempotency-Key": "asset-" + str(index)})
        asset_ids.append(response.json()["id"])
    outcomes.append({"case": "public_asset_upload", "passed": True, "count": len(asset_ids)})
    jobs = []
    for task in fixtures.TASKS:
        body: dict[str, Any] = {"model": "protocol-" + task.replace("_", "-"), "task_type": task, "input": {"prompt": "Synthetic native protocol check"}, "parameters": fixtures.defaults(task)}
        if task == "image_to_image":
            body["input"]["source_image"] = {"asset_id": asset_ids[0]}
        if task.startswith("references_"):
            body["input"]["references"] = [{"asset_id": asset_ids[0], "role": "character"}, {"asset_id": asset_ids[1], "role": "scene"}]
        if task.endswith("video"):
            body["parameters"]["seconds"] = 5
        submissions_before = comfy.submissions
        if confirmed_retry:
            with comfy.lock:
                comfy.fail_next_generations = 1
        if task == "image_to_image":
            with comfy.lock:
                comfy.fail_next_uploads = 1
        identifier = call("POST", "/v1/jobs", 202, json=body, headers={"Idempotency-Key": "native-" + task}).json()["id"]
        replay = call("POST", "/v1/jobs", 202, json=body, headers={"Idempotency-Key": "native-" + task}).json()
        check(replay["id"] == identifier, "native_idempotency_changed_identity")
        retry_job = None
        if task == "image_to_image":
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                row = pg.execute("SELECT remote_job_id,state FROM render_control_intents WHERE id=%s", (identifier,)).fetchone()
                pg.commit()
                if row and row[0]:
                    retry_job = row[0]
                    saved = control.execute("SELECT state,retry_count,current_attempt_id,retry_after FROM jobs WHERE id=%s", (retry_job.removeprefix("job_"),)).fetchone()
                    control.commit()
                    if saved and saved[0] == "retry_wait":
                        check(saved[1] == 1 and saved[2] is None and saved[3] is not None, "native_retry_metadata_invalid")
                        check(row[1] != "settled" and comfy.submissions == submissions_before, "native_retry_submitted_or_settled_early")
                        released = control.execute("SELECT generation_released_at IS NOT NULL AND closed_at IS NOT NULL AND association_id IS NULL AND prompt_id IS NULL FROM attempts WHERE job_id=%s", (retry_job.removeprefix("job_"),)).fetchall()
                        check(released == [(True,)], "native_retry_source_not_closed")
                        check(control.execute("SELECT count(*) FROM usage_ledger WHERE job_id=%s", (retry_job.removeprefix("job_"),)).fetchone()[0] == 0, "native_retry_usage_early")
                        control.commit()
                        break
                time.sleep(0.2)
            else:
                raise RuntimeError("native_retry_wait_not_observed")
        oom_job = None
        if confirmed_retry:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                row = pg.execute("SELECT remote_job_id,state FROM render_control_intents WHERE id=%s", (identifier,)).fetchone()
                pg.commit()
                if row and row[0]:
                    saved = control.execute("SELECT state,retry_count,retry_error_code FROM jobs WHERE id=%s", (row[0].removeprefix("job_"),)).fetchone()
                    control.commit()
                    if saved and saved[0] == "retry_wait" and saved[2] == "comfy_oom":
                        oom_job = row[0]
                        check(saved[1] == (2 if retry_job else 1) and row[1] != "settled", "native_oom_retry_budget_or_money_changed")
                        attempts = control.execute("SELECT association_id,prompt_id,generation_released_at,closed_at,generation_evidence FROM attempts WHERE job_id=%s ORDER BY created_at,id", (oom_job.removeprefix("job_"),)).fetchall()
                        check(len(attempts) == (2 if retry_job else 1), "native_oom_early_duplicate_attempt")
                        source = attempts[-1]
                        from generation_domain.retries import confirmed_oom_evidence
                        check(source[2] is not None and source[3] is not None and confirmed_oom_evidence(source[4]["release"], prompt_id=source[1], association_id=str(source[0])), "native_oom_stop_evidence_missing")
                        check(control.execute("SELECT count(*) FROM usage_ledger WHERE job_id=%s", (oom_job.removeprefix("job_"),)).fetchone()[0] == 0, "native_oom_settled_early")
                        control.commit()
                        break
                time.sleep(0.15)
            else:
                raise RuntimeError("native_oom_retry_wait_not_observed")
        result = poll(identifier)
        if oom_job:
            facts = control.execute("SELECT retry_count,(SELECT count(*) FROM attempts WHERE job_id=jobs.id),(SELECT count(DISTINCT gpu_id) FROM attempts WHERE job_id=jobs.id),(SELECT count(*) FROM usage_ledger WHERE job_id=jobs.id) FROM jobs WHERE id=%s", (oom_job.removeprefix("job_"),)).fetchone()
            control.commit()
            check(facts == ((2, 3, 2, 1) if retry_job else (1, 2, 2, 1)), "native_oom_physical_route_or_unique_usage_failed")
            with closing(sqlite3.connect(root / "worker-state/journal.sqlite3")) as journal:
                snapshots = journal.execute("SELECT claim FROM attempts WHERE json_extract(claim,'$.job_id')=?", (oom_job,)).fetchall()
            executions = [json.loads(record[0])["execution_digest"] for record in snapshots]
            check(len(executions) == facts[1] and len(set(executions)) == 1, "native_oom_changed_frozen_execution")
            outcomes.append({"case": task + "_confirmed_oom_cross_gpu", "passed": True, "attempts": facts[1], "scheduled_retries": facts[0], "physical_gpus": facts[2], "usage_rows": facts[3], "same_execution_digest": True, "real_oom": False})
        if retry_job:
            with closing(sqlite3.connect(root / "worker-state/journal.sqlite3")) as journal:
                records = journal.execute("SELECT association_id,prompt_id,stage FROM attempts WHERE json_extract(claim,'$.job_id')=? ORDER BY rowid", (retry_job,)).fetchall()
            check(len(records) == (3 if confirmed_retry else 2) and records[0][:2] == (None, None) and records[1][0] and records[1][1], "native_retry_journal_submission_evidence_invalid")
            check(comfy.submissions == submissions_before + 1 and comfy.preparation_failures == 1, "native_retry_prompt_not_exactly_once")
            settled_job = control.execute("SELECT retry_count,(SELECT count(*) FROM usage_ledger WHERE job_id=jobs.id),(SELECT count(*) FROM attempts WHERE job_id=jobs.id) FROM jobs WHERE id=%s", (retry_job.removeprefix("job_"),)).fetchone()
            control.commit()
            check(settled_job == ((2, 1, 3) if confirmed_retry else (1, 1, 2)), "native_retry_not_single_job_usage")
            outcomes.append({"case": "preparation_503_and_oom_share_job_and_budget" if confirmed_retry else "preparation_503_retry_same_job_one_prompt_one_usage", "passed": True, "attempts": settled_job[2], "scheduled_retries": settled_job[0], "real_backoff_seconds": [5, 15] if confirmed_retry else [5], "pre_intent_journal_verified": True})
        jobs.append(identifier)
        artifacts = result.get("artifacts", [])
        check(len(artifacts) == 1, "native_artifact_count")
        artifact = artifacts[0]
        content_id = artifact["id"]
        downloaded = call("GET", f"/v1/content/{content_id}/download")
        expected = (root / ("comfy/output/result.wav" if task == "text_to_audio" else "comfy/output/result.mp4" if task.endswith("video") else "comfy/output/result.png")).read_bytes()
        check(downloaded.content == expected, "native_delivered_media_changed")
        if task == "text_to_audio":
            check(downloaded.headers["Content-Type"].split(";")[0] == "audio/wav", "audio_download_mime_changed")
            partial = call("GET", f"/v1/content/{content_id}/download", 206, headers={"Range": "bytes=0-3"})
            check(partial.content == b"RIFF", "audio_range_changed")
            call("HEAD", f"/v1/content/{content_id}/download")
        other = call("GET", "/v1/jobs/" + identifier, (403, 404), headers={"Authorization": "Bearer " + tokens[1]})
        check(other.status_code in (403, 404), "cross_key_job_visible")
        call("GET", f"/v1/content/{content_id}/download", (403, 404), headers={"Authorization": "Bearer " + tokens[1]})
        outcomes.append({"case": task, "passed": True, "idempotent_replay": True, "job_id": identifier, "media_bytes_verified": True, "cross_key_isolation": True})
        print(json.dumps({"case": task, "generation_and_delivery_succeeded": True}), flush=True)

    def sync_result(response: httpx.Response) -> None:
        check(base64.b64decode(response.json()["data"][0]["b64_json"], validate=True) == image, "sync_image_bytes_changed")
        rows = {row[0] for row in pg.execute("SELECT id FROM render_control_intents").fetchall()}
        pg.commit()
        added = rows - set(jobs)
        check(len(added) == 1, "sync_intent_count_wrong")
        identifier = added.pop()
        poll(identifier)
        jobs.append(identifier)

    image_body = {"model": "protocol-text-to-image", "prompt": "Synthetic native compatible image", "size": "1024x1024", "quality": "standard", "n": 1, "response_format": "b64_json"}
    sync_result(call("POST", "/v1/images/generations", json=image_body, headers={"Idempotency-Key": "compatible-sync-image"}))
    outcomes.append({"case": "compatible_image_sync_bytes", "passed": True})
    identifier = call("POST", "/v1/images/generations/async", 202, json=image_body, headers={"Idempotency-Key": "compatible-async-image"}).json()["id"]
    poll(identifier)
    check(call("GET", "/v1/images/tasks/" + identifier).json()["status"] == "completed", "compatible_image_task_status")
    jobs.append(identifier)
    outcomes.append({"case": "compatible_image_async_and_task", "passed": True})
    sync_result(call("POST", "/v1/images/edits", data={"model": "protocol-image-to-image", "prompt": "Synthetic native edit", "response_format": "b64_json"}, files={"image": ("source.png", image, "image/png")}, headers={"Idempotency-Key": "compatible-edit-multipart"}))
    outcomes.append({"case": "compatible_edit_multipart_sync", "passed": True})
    identifier = call("POST", "/v1/images/edits/async", 202, json={"model": "protocol-references-to-image", "prompt": "Synthetic references", "references": [{"asset_id": asset_ids[0], "role": "character"}, {"asset_id": asset_ids[1], "role": "scene"}]}, headers={"Idempotency-Key": "compatible-edit-references"}).json()["id"]
    poll(identifier)
    jobs.append(identifier)
    outcomes.append({"case": "compatible_edit_references_json_async", "passed": True})
    identifier = call("POST", "/v1/videos", 202, json={"model": "protocol-references-to-video", "prompt": "Synthetic compatible video", "seconds": "5", "size": "1024x1024", "references": [{"asset_id": asset_ids[0], "role": "character"}, {"asset_id": asset_ids[1], "role": "scene"}]}, headers={"Idempotency-Key": "compatible-video"}).json()["id"]
    poll(identifier)
    check(call("GET", "/v1/videos/" + identifier).json()["status"] == "completed", "compatible_video_task_status")
    expected_video = (root / "comfy/output/result.mp4").read_bytes()
    partial = call("GET", f"/v1/videos/{identifier}/content", 206, headers={"Range": "bytes=0-63"})
    check(partial.content == expected_video[:64] and partial.headers["Content-Range"] == f"bytes 0-63/{len(expected_video)}", "compatible_video_range_changed")
    call("GET", f"/v1/videos/{identifier}/content", (403, 404), headers={"Authorization": "Bearer " + tokens[1]})
    jobs.append(identifier)
    outcomes.append({"case": "compatible_video_and_range_delivery", "passed": True})

    if managed:
        managed.exercise(pg, call, poll, jobs, fixtures, image_body)
    # Reuse original frozen quote and real stock auth/cache after process restart.
    processes.stop(provider)
    credential_id, rotated = fixtures.issue_secret("gateway")
    gateway = control.execute("UPDATE gateway_services SET credential_identifier=%s,credential_verifier=%s,revision=revision+1 RETURNING id", (credential_id, fixtures.verifier(rotated, pepper))).fetchall()
    check(len(gateway) == 1, "gateway_rotation_scope_wrong")
    control.commit()
    (root / "gateway.key").write_text(rotated, encoding="ascii")
    provider = restart()
    wait_http(client, "/health", provider)
    check(poll(jobs[0])["id"] == jobs[0], "native_restart_lost_job")
    outcomes.append({"case": "provider_crash_restart_and_gateway_credential_rotation", "passed": True, "stable_gateway_id": "gateway_" + gateway[0][0].hex})
    # Force current control-side Key revocation after successful requests have
    # populated the real upstream L1 and Redis-protocol caches.
    control.execute("UPDATE api_keys SET enabled=FALSE,revision=revision+1 WHERE id=(SELECT key_id FROM external_identity_bindings WHERE external_key_ref=%s)", (f"native-protocol:key:{upstream['keys'][0]}",))
    control.commit()
    call("GET", "/v1/jobs/" + jobs[0], (401, 403))
    outcomes.append({"case": "current_control_key_revocation", "passed": True})
    deadline = time.monotonic() + 60
    counts: dict[str, int] = {}
    while time.monotonic() < deadline:
        row = pg.execute("SELECT count(*),count(*) FILTER(WHERE state='settled') FROM render_control_intents").fetchone()
        effect = pg.execute("SELECT count(*),count(*) FILTER(WHERE acknowledged_at IS NOT NULL) FROM render_control_effects").fetchone()
        pg.commit()
        counts = {"intents": row[0], "settled": row[1], "effects": effect[0], "effects_consumed": effect[1]}
        if row[0] == row[1] == len(jobs) and effect[0] == effect[1]:
            break
        time.sleep(0.5)
    check(counts["intents"] == counts["settled"] == len(jobs), "native_settlement_incomplete")
    check(counts["effects"] == counts["effects_consumed"], "native_effects_incomplete")
    balance = pg.execute("SELECT balance::text,frozen_balance::text FROM users WHERE id=%s", (upstream["user"],)).fetchone()
    quota = pg.execute("SELECT quota_used::text FROM api_keys WHERE id=%s", (upstream["keys"][0],)).fetchone()[0]
    usage = pg.execute("SELECT count(*) FROM usage_logs WHERE user_id=%s", (upstream["user"],)).fetchone()[0]
    pg.commit()
    expected_balance, expected_quota = ("74.00000000", "26.00000000") if managed else ("77.00000000", "23.00000000")
    check(balance == (expected_balance, "0.00000000") and quota == expected_quota and usage == len(jobs), "native_usage_or_money_mismatch")
    audio_usage = pg.execute("SELECT audio_count,audio_duration_milliseconds,image_count,video_count,actual_cost=1,billing_mode FROM usage_logs WHERE user_id=%s AND model='protocol-text-to-audio'", (upstream["user"],)).fetchall()
    check(audio_usage == [(1, 500, 0, 0, True, "audio")], "audio_native_metering_not_independent")
    control_audio = control.execute("SELECT u.audio_milliseconds,u.image_count,u.video_milliseconds FROM usage_ledger u JOIN jobs j ON j.id=u.job_id WHERE j.model_alias='protocol-text-to-audio'").fetchall()
    check(control_audio == [(500, 0, 0)], "audio_control_metering_not_actual")
    pg.commit()
    control.commit()
    outcomes.append({"case": "audio_independent_control_and_sub2api_metering", "passed": True, "audio_milliseconds": 500, "charged_started_seconds": 1, "image_count": 0, "video_count": 0})
    outcomes.append({"case": "unique_hold_settlement_usage_and_cache_effects", "passed": True, **counts, "usage_logs": usage, "balance": balance[0], "frozen_balance": balance[1], "quota_used": quota})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--managed-catalog", action="store_true", help="Exercise Admin publication and automatic Provider catalog confirmation")
    modes.add_argument("--confirmed-oom", action="store_true", help="Inject confirmed OOM across all six task types using two protocol GPUs")
    parser.add_argument("--evidence-dir", type=Path, help="New directory beneath docs/evidence; preserve previous runtime evidence")
    args = parser.parse_args()
    evidence = EVIDENCE
    if args.evidence_dir is not None:
        evidence = args.evidence_dir.resolve()
        check((ROOT.parent / "docs/evidence").resolve() in evidence.parents, "evidence_directory_outside_project")
        evidence.mkdir(parents=True, exist_ok=False)
    check(os.name == "nt", "native_windows_required")
    started = time.monotonic()
    run_id = uuid4().hex[:12]
    root = ROOT / ".local/native-provider" / run_id
    root.mkdir(parents=True, exist_ok=False)
    ledger = module("native_ledger_harness", "test-sub2api-ledger.py")
    fixtures = module("native_provider_fixtures", "native-provider-fixtures.py")
    source_paths = [Path(__file__), ROOT / "scripts/native-provider-fixtures.py", ROOT / "integrations/sub2api/protocol-fixture/main.go"]
    for directory in ("packages", "apps/control_api", "apps/control_runner", "apps/windows_worker", "migrations"):
        source_paths.extend(sorted((ROOT / directory).rglob("*.py")))
    source_paths.extend(sorted((ROOT / "packages/contracts/src/generation_contracts/schemas").glob("*.json")))
    source_paths.extend([ROOT / "config/contract-hashes.json", ROOT / "packages/domain/src/generation_domain/job-state-machine.json", ROOT / "packages/contracts/src/generation_contracts/endpoint-catalog.json"])
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in source_paths}
    report: dict[str, Any] = {"passed": False, "run_id": run_id, "started_at": datetime.now(UTC).isoformat(), "sources_sha256": source_hashes, "native_windows": True, "virtualization_used": False, "real_redis": False, "real_comfyui": False, "real_s3": False, "physical_fleet_validated": False, "production_runtime_validated": False, "browser_used": False, "cases": [], "backups": [], "cleanup": {"processes": False, "databases": False}}
    processes = Processes(root)
    created: list[str] = []
    comfy, alternative_comfy, engine, api_client, provider_client = None, None, None, None, None
    admin = None
    managed: NativeCatalog | None = None
    phase = "verify_sources"
    failure: BaseException | None = None
    try:
        lock = json.loads((ROOT / "integrations/sub2api/upstream-lock.json").read_text(encoding="utf-8"))
        backend, sources, _ = ledger.materialize(lock)
        go, env = ledger.native_environment()
        report["overlay_sha256"] = sources
        report["upstream_commit"] = lock["commit"]
        provider_binary = ROOT / ".local/bin/sub2api-provider.exe"
        build_report = json.loads((EVIDENCE / "sub2api-provider-native-build.json").read_text(encoding="utf-8"))
        check(build_report["passed"] and build_report["overlay_files_sha256"] == sources, "provider_build_sources_stale")
        check(sha(provider_binary) == build_report["binary_sha256"], "provider_binary_changed")
        test_binary = ROOT / ".local/bin/sub2api-ledger.test.exe"
        test_marker = json.loads((ROOT / ".local/sub2api-ledger-binary-cache.json").read_text(encoding="utf-8"))
        check(test_marker["input_sha256"] == ledger.binary_identity(lock, sources, env) and sha(test_binary) == test_marker["binary_sha256"], "migration_binary_changed")
        redis_binary = ROOT / ".local/bin/sub2api-redis-protocol.exe"
        phase = "build_redis_protocol_fixture"
        code, timed_out = ledger.run_owned([str(go), "build", "-trimpath", "-mod=readonly", "-p=2", "-o", str(redis_binary), str(source_paths[2])], backend, env, root / "redis-build.log", 300)
        check(code == 0 and not timed_out, "redis_fixture_build_failed")
        report["binaries_sha256"] = {"stock_provider": sha(provider_binary), "migration_test": sha(test_binary), "redis_protocol": sha(redis_binary)}
        settings = Settings()
        url = make_url(settings.database_url.get_secret_value())
        check(settings.environment == "development" and url.host == "127.0.0.1" and url.port == 55438 and url.database == "render_control", "native_project_database_required")
        options = {"host": url.host, "port": url.port, "user": url.username, "password": url.password, "sslmode": "disable", "connect_timeout": 5}
        admin = psycopg.connect(**{**options, "dbname": "postgres"}, autocommit=True)
        check(admin.execute("SELECT current_database(),host(inet_server_addr()),current_setting('port')").fetchone() == ("postgres", "127.0.0.1", "55438"), "native_database_identity_mismatch")
        check(admin.execute("SELECT pg_try_advisory_lock(634720182)").fetchone()[0], "project_migration_lock_busy")
        control_database, upstream_database = "render_control_native_" + run_id, "render_sub2api_test_" + run_id
        phase = "backup_before_ddl"
        for database in (control_database, upstream_database):
            check(re.fullmatch(r"render_(?:control_native|sub2api_test)_[a-f0-9]{12}", database) is not None, "unsafe_database_name")
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            created.append(database)
            backup = root / (database + "-empty.dump")
            pg_env = {**env, "PGPASSWORD": url.password or ""}
            safe_dsn = psycopg.conninfo.make_conninfo(**{k: v for k, v in {**options, "dbname": database}.items() if k != "password" and v is not None})
            tools = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
            for command in ([str(tools / "pg_dump.exe"), "--format=custom", "--file=" + str(backup), "--dbname=" + safe_dsn, "--no-password"], [str(tools / "pg_restore.exe"), "--list", str(backup)]):
                result = subprocess.run(command, env=pg_env, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- exact created database backup/list only, before DDL
                check(result.returncode == 0, "pre_ddl_backup_failed")
            report["backups"].append({"database_namespace": database, "sha256": sha(backup), "archive_verified_before_ddl": True, "bytes": backup.stat().st_size})
        phase = "migrate_isolated_databases"
        control_url = url.set(database=control_database).render_as_string(hide_password=False)
        pepper = secrets.token_urlsafe(48)
        api_port, redis_port, provider_port, comfy_port = port(), port(), port(), port()
        api_origin = f"https://127.0.0.1:{api_port}"
        monitoring_token = "mon_" + secrets.token_hex(16) + "." + secrets.token_urlsafe(32)
        env["GEN_MONITORING_TOKEN_SHA256"] = json.dumps([hashlib.sha256(monitoring_token.encode()).hexdigest()])
        env["GEN_MONITORING_ALLOWED_NETWORKS"] = '["127.0.0.1/32"]'
        env.update({"GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": control_url, "GEN_CREDENTIAL_PEPPER": pepper, "GEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GEN_PUBLIC_ORIGIN": api_origin, "GEN_STORAGE_BACKEND": "local_development", "GEN_LOCAL_STORAGE_ROOT": str(root / "objects"), "GEN_SPOOL_ROOT": str(root / "spool"), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "RC_SUB2API_TEST_DSN": psycopg.conninfo.make_conninfo(**{**options, "dbname": upstream_database})})
        for name, command in (("control-migrations", [sys.executable, "-m", "alembic", "upgrade", "head"]), ("upstream-migrations", [str(test_binary), "-test.run=^$", "-test.timeout=180s"])):
            code, timeout = ledger.run_owned(command, ROOT, env, root / (name + ".log"), 240)
            check(code == 0 and not timeout, name + "_failed")
        phase = "seed_synthetic_catalog"
        commit = fixtures.media_root(root / "comfy")
        comfy = fixtures.ComfyProtocol(root / "comfy", comfy_port)
        with psycopg.connect(**{**options, "dbname": upstream_database}) as pg:
            upstream = seed_upstream(pg)
        engine = create_engine(control_url, pool_pre_ping=True)
        factory = sessionmaker(engine, expire_on_commit=False)
        if args.confirmed_oom:
            alternative_port = port()
            alternative_comfy = fixtures.ComfyProtocol(root / "comfy", alternative_port)
        catalog = fixtures.seed_control(factory, pepper, f"http://127.0.0.1:{comfy_port}", commit, upstream, alternative_origin=f"http://127.0.0.1:{alternative_port}" if args.confirmed_oom else None)
        write(root / "worker-credential.json", catalog["worker_credential"])
        tls = ROOT / ".local/tls"
        cert, key = tls / "localhost.crt", tls / "localhost.key"
        worker_config = {"control_origin": api_origin, "credential_file": str(root / "worker-credential.json"), "state_root": str(root / "worker-state"), "ca_file": str(cert), "gpus": [{"hardware_id": fixtures.HARDWARE, "name": "Synthetic protocol GPU"}], "channels": [{"base_url": f"http://127.0.0.1:{comfy_port}", "hardware_id": fixtures.HARDWARE, "comfy_root": str(root / "comfy")}]}
        write(root / "worker.json", worker_config)
        if alternative_comfy:
            worker_config["gpus"].append({"hardware_id": fixtures.HARDWARE + "-ALT", "name": "Synthetic alternative GPU"})
            worker_config["channels"].append({"base_url": f"http://127.0.0.1:{alternative_port}", "hardware_id": fixtures.HARDWARE + "-ALT", "comfy_root": str(root / "comfy")})
            write(root / "worker.json", worker_config)
        phase = "start_native_services"
        api = processes.start("control-api", [sys.executable, "-m", "uvicorn", "control_api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(api_port), "--ssl-certfile", str(cert), "--ssl-keyfile", str(key), "--no-proxy-headers", "--no-access-log"], env)
        api_client = httpx.Client(base_url=api_origin, verify=ssl.create_default_context(cafile=str(cert)), timeout=5, trust_env=False, follow_redirects=False)
        wait_http(api_client, "/health/ready", api)
        metrics_path = "/internal/monitoring/v1/metrics"
        check(api_client.get(metrics_path).status_code == 401, "monitoring_requires_own_identity")
        metrics = api_client.get(metrics_path, headers={"Authorization": "Bearer " + monitoring_token})
        check(metrics.status_code == 200 and "version=0.0.4" in metrics.headers.get("content-type", ""), "native_monitoring_scrape_failed")
        metric_values = {sample.name: sample.value for family in text_string_to_metric_families(metrics.text) for sample in family.samples if not sample.labels}
        check(metric_values.get("render_control_snapshot_success") == 1, "native_monitoring_snapshot_unavailable")
        check(monitoring_token not in metrics.text and catalog["gateway_token"] not in metrics.text, "native_monitoring_secret_exposure")
        monitoring_configuration = api_client.get("/internal/monitoring/v1/configuration", headers={"Authorization": "Bearer " + monitoring_token})
        check(monitoring_configuration.status_code == 200, "native_monitoring_configuration_failed")
        selected_samples = [{"name": sample.name, "labels": sample.labels, "value": sample.value} for family in text_string_to_metric_families(metrics.text) for sample in family.samples if sample.name in {"render_control_snapshot_success", "render_control_gpu_resources", "render_control_channels", "render_control_worker_capacity_samples"}]
        write(evidence / "monitoring-api-responses.json", {"scope": "Actual native HTTPS responses with synthetic protocol resources", "real_comfyui": False, "real_redis": False, "real_s3": False, "metrics": {"status": metrics.status_code, "content_type": metrics.headers["content-type"], "request_id_present": "x-request-id" in metrics.headers, "selected_samples": selected_samples}, "configuration": {"status": monitoring_configuration.status_code, "body": monitoring_configuration.json()}})
        report["cases"].append({"case": "native_https_monitoring_identity_and_readonly_snapshot", "passed": True, "prometheus_parsed": True, "snapshot_success": True, "payload_bytes": len(metrics.content)})
        worker = processes.start("windows-worker", [sys.executable, "-m", "windows_worker.main", "--config", str(root / "worker.json")], env)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            check(worker.poll() is None, "worker_registration_failed")
            with factory.begin() as session:
                node = session.get(Node, catalog["node_uuid"])
                if node and node.current_boot_id:
                    # This is an explicit env=test synthetic fixture. A physical
                    # release still requires the separate runtime approval flow.
                    channel = session.get(Channel, catalog["channel_uuid"])
                    assert channel
                    channel.applied_revision = channel.verified_revision = 1
                    channel_ids = [channel.id]
                    if args.confirmed_oom:
                        other = session.get(Channel, catalog["alternative_channel_uuid"])
                        assert other
                        other.applied_revision = other.verified_revision = 1
                        channel_ids.append(other.id)
                    bindings = list(session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id.in_(channel_ids))))
                    check(len(bindings) == len(fixtures.TASKS) * len(channel_ids), "synthetic_boot_bindings_missing")
                    for binding in bindings:
                        release = session.get(WorkflowRelease, binding.release_id)
                        check(release is not None and release.validation.get("test_only") is True and binding.desired_digest == binding.applied_digest, "synthetic_boot_approval_invalid")
                        binding.verified_digest = binding.desired_digest
                        if args.managed_catalog:
                            binding.runtime_approval = {"profile_id": "synthetic-protocol-fixture", "physical_runtime_approval": False}
                    report["synthetic_boot_fixture"] = {"approved_after_registration": True, "workflow_bindings": len(bindings), "physical_runtime_approval": False}
                    break
            time.sleep(0.2)
        else:
            raise RuntimeError("worker_registration_timeout")
        if args.managed_catalog:
            password = secrets.token_urlsafe(32)
            with factory.begin() as session:
                session.add(AdminUser(username="native-catalog-admin", password_hash=password_hash(password), role="administrator"))
            response = api_client.post("/admin/api/v1/session", json={"username": "native-catalog-admin", "password": password}, headers={"Origin": api_origin})
            check(response.status_code == 200, "catalog_native_admin_login_failed")
            api_client.headers.update({"Origin": api_origin, "X-CSRF-Token": response.json()["csrf_token"]})
            managed = NativeCatalog(api_client, catalog["gateway_id"], upstream, report["cases"])
            managed.first = managed.publish(0)
        processes.start("control-runner", [sys.executable, "-m", "control_runner.main"], env)
        redis_password = secrets.token_urlsafe(40)
        write(root / "redis.json", {"protocol_test": True, "address": f"127.0.0.1:{redis_port}", "password": redis_password})
        redis = processes.start("redis-protocol", [str(redis_binary), "--config", str(root / "redis.json")], env)
        wait_redis(redis_port, redis_password, redis)
        (root / "gateway.key").write_text(catalog["gateway_token"], encoding="ascii")
        (root / "provider.key").write_text(base64.b64encode(secrets.token_bytes(32)).decode(), encoding="ascii")
        provider_config = {"version": 1, "instance": "native-protocol", "public_origin": f"https://127.0.0.1:{provider_port}", "sync_wait_milliseconds": 60000, "upload_directory": str(root / "provider-uploads"), "active_key": "protocol-v1", "encryption_key_files": {"protocol-v1": str(root / "provider.key")}, "gateways": [{"id": catalog["gateway_id"], "origin": api_origin, "credential_file": str(root / "gateway.key"), "ca_file": str(cert)}], "models": [{"usage_account_id": upstream["account"], "model": "protocol-" + task.replace("_", "-"), "task_type": task, "gateway_id": catalog["gateway_id"], "price_version": "protocol-v1", "unit_price": "1.00000000", "group_ids": [upstream["group"]], "defaults": fixtures.defaults(task)} for task in fixtures.TASKS], "runtime": {"workers": 1, "poll_milliseconds": 500, "settlement_milliseconds": 1000, "call_milliseconds": 5000, "lease_milliseconds": 60000}}
        write(root / "provider.json", provider_config)
        if managed:
            provider_config["models"] = []
            write(root / "provider.json", provider_config)
        stock_config = {"run_mode": "standard", "server": {"host": "127.0.0.1", "port": provider_port, "mode": "release"}, "database": {"host": url.host, "port": url.port, "user": url.username, "password": url.password, "dbname": upstream_database, "sslmode": "disable"}, "redis": {"host": "127.0.0.1", "port": redis_port, "password": redis_password, "db": 0}, "jwt": {"secret": secrets.token_urlsafe(48)}, "log": {"level": "info", "format": "json"}}
        write(root / "config.yaml", stock_config)
        provider_env = {**env, "SKIP_SETUP": "true", "AUTO_SETUP": "false", "DATA_DIR": str(root), "CONFIG_FILE": str(root / "config.yaml"), "SUB2API_RENDER_CONTROL_CONFIG": str(root / "provider.json")}
        restarts = 0

        def restart() -> subprocess.Popen[bytes]:
            nonlocal restarts
            restarts += 1
            return processes.start(f"sub2api-{restarts}", [str(provider_binary)], provider_env)

        provider = restart()
        provider_client = httpx.Client(base_url=f"http://127.0.0.1:{provider_port}", timeout=70, trust_env=False, follow_redirects=False)
        wait_http(provider_client, "/health", provider)
        phase = "public_generation_and_delivery"
        print(json.dumps({"phase": phase, "native_servers_started": True, "redis_is_protocol_fixture": True, "comfy_is_protocol_fixture": True}), flush=True)
        with psycopg.connect(**{**options, "dbname": upstream_database}) as pg, psycopg.connect(**{**options, "dbname": control_database}) as control:
            if managed:
                managed.wait(pg, 1)
                report["managed_catalog"] = {"initial_revision": 1, "empty_bootstrap_models": True, "receipts_generated_by_actual_provider": True, "physical_runtime_approval": False}
                report["cases"].append({"case": "managed_catalog_empty_bootstrap_onboarding", "passed": True})
            scenario(provider_client, upstream, pg, control, fixtures, processes, provider, restart, root, report["cases"], pepper, comfy, managed, args.confirmed_oom)
        phase = "worker_retention_after_cloud_ack"
        state = root / "worker-state"
        expected_outputs = len(fixtures.TASKS) + 5 + (2 if managed else 0)
        deadline = time.monotonic() + 30
        while True:
            with closing(sqlite3.connect(state / "journal.sqlite3")) as journal:
                rows = journal.execute("SELECT a.cloud_ack,p.package FROM attempts a JOIN output_packages p ON p.attempt_id=a.id").fetchall()
            if len(rows) >= expected_outputs and all(row[0] == 1 for row in rows):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        report["worker_retention_observed"] = {"expected_packages": expected_outputs, "packages": len(rows), "acknowledged": sum(row[0] == 1 for row in rows)}
        check(len(rows) == expected_outputs and all(row[0] == 1 for row in rows), "worker_outputs_not_confirmed")
        kept = 0
        for _, raw in rows:
            package = json.loads(raw)
            check(package["retain_until"] >= package["confirmed_at"] + 3600 and package["retain_until"] > time.time(), "worker_retention_deadline_invalid")
            check(not package.get("retention_cleaned"), "worker_outputs_cleaned_early")
            for name, spec in zip(package["files"], package["specs"], strict=True):
                path = (state / name).resolve(strict=True)
                check(path.is_relative_to(state.resolve()) and path.stat().st_size == spec["bytes"] and sha(path) == spec["sha256"], "retained_worker_output_mismatch")
                kept += 1
        report["worker_retention"] = {"confirmed_packages": len(rows), "retained_files_verified": kept, "minimum_retention_seconds": 3600, "journal_and_files_removed_only_by_test_resource_cleanup": True}
        report["comfy_protocol"] = {"submissions": comfy.submissions, "input_uploads": comfy.uploads, "injected_preparation_failures": comfy.preparation_failures}
        if alternative_comfy:
            report["comfy_protocol"].update(confirmed_oom_failures=comfy.oom_failures, alternative_gpu_submissions=alternative_comfy.submissions, distinct_physical_gpu_fixtures=2)
        phase = "management_command_durable_recovery"
        management_recovery_case(factory, catalog, root, processes, worker, env, report["cases"])
        report["source_unchanged"] = all(sha(ROOT / name) == digest for name, digest in source_hashes.items()) and all(sha(backend / name) == digest for name, digest in sources.items())
        check(report["source_unchanged"], "sources_changed_during_run")
        report["passed"] = True
    except BaseException as exc:
        failure = exc
        report["failure"] = {"phase": phase, "type": type(exc).__name__, "code": str(exc) if isinstance(exc, RuntimeError) and len(str(exc)) < 200 else "private_native_diagnostics_retained"}
        if admin and len(created) == 2:
            diagnostics: dict[str, Any] = {}
            for database, queries in ((created[0], {"jobs": "SELECT state,count(*) FROM jobs GROUP BY state", "admissions": "SELECT state,error_code,count(*) FROM gateway_admissions GROUP BY state,error_code"}), (created[1], {"intents": "SELECT state,dispatch_count,admission_id IS NOT NULL,remote_job_id IS NOT NULL FROM render_control_intents", "recovery": "SELECT failures,last_error_code FROM render_control_recovery", "effects": "SELECT kind,acknowledged_at IS NOT NULL,last_error_code FROM render_control_effects"})):
                try:
                    with psycopg.connect(**{**options, "dbname": database}, autocommit=True) as connection:
                        diagnostics[database] = {name: connection.execute(query).fetchall() for name, query in queries.items()}
                except Exception as diagnostic_error:
                    diagnostics[database] = {"unavailable": type(diagnostic_error).__name__}
            report["safe_failure_state"] = diagnostics
    finally:
        for client in (provider_client, api_client):
            if client:
                client.close()
        try:
            processes.close()
            report["cleanup"]["processes"] = True
        except BaseException as exc:
            report["cleanup"]["process_error"] = type(exc).__name__
            report["passed"] = False
            failure = failure or exc
        report["owned_processes"] = processes.stops
        if comfy:
            comfy.close()
        if alternative_comfy:
            alternative_comfy.close()
        if engine:
            engine.dispose()
        if report["cleanup"]["processes"]:
            try:
                # Preserve private logs/backups, remove generated media/secrets.
                for directory in ("comfy", "worker-state", "objects", "spool", "provider-uploads"):
                    remove_fixture_tree(root / directory, root)
                for filename in ("worker-credential.json", "worker.json", "redis.json", "gateway.key", "provider.key", "provider.json", "config.yaml"):
                    (root / filename).unlink(missing_ok=True)
                report["cleanup"]["media_and_credentials"] = True
            except BaseException as exc:
                report["cleanup"]["media_and_credentials"] = False
                report["cleanup"]["media_error"] = type(exc).__name__
                report["passed"] = False
                failure = failure or exc
        if admin:
            try:
                # Do not drop while an owned process cleanup failed.
                if report["cleanup"]["processes"]:
                    for database in reversed(created):
                        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                    report["cleanup"]["databases"] = True
                admin.execute("SELECT pg_advisory_unlock(634720182)")
            finally:
                admin.close()
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        report["passed"] = report["passed"] and all(report["cleanup"].values())
        write(root / "report.json", report)
        write(evidence / ("confirmed-retry-native-protocol.json" if args.confirmed_oom else "provider-catalog-native-protocol.json" if args.managed_catalog else "sub2api-native-protocol.json"), report)
        print(json.dumps({"passed": report["passed"], "phase": phase, "cases": len(report["cases"]), "cleanup": report["cleanup"], "failure": report.get("failure")}, ensure_ascii=False), flush=True)
    if failure:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
