"""Fresh offline candidate install, native PG backup/migration and HTTPS smoke."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from generation_infrastructure.settings import Settings
from prometheus_client.parser import text_string_to_metric_families
from psycopg import sql
from release_bundle import (
    clean_environment,
    digest_file,
    remove_created,
    verify_archive,
    verify_payload,
)
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence" / datetime.now().date().isoformat()


def available_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--evidence-dir", type=Path, help="New directory beneath docs/evidence; preserve previous installation evidence")
    args = parser.parse_args()
    evidence = EVIDENCE
    if args.evidence_dir is not None:
        evidence = args.evidence_dir.resolve()
        if (ROOT.parent / "docs/evidence").resolve() not in evidence.parents:
            raise SystemExit("Evidence directory must be beneath project docs/evidence")
        evidence.mkdir(parents=True, exist_ok=False)
    args.archive = args.archive.resolve(strict=True)
    started = time.monotonic()
    run_id = uuid4().hex[:12]
    root = ROOT / ".local" / ("release-smoke-" + run_id)
    root.mkdir(exist_ok=False)
    state = root / "state"
    state.mkdir()
    cwd = root / "outside-project"
    cwd.mkdir()
    # Any implicit .env load would fail. Explicit release configuration must win.
    (cwd / ".env").write_text("GEN_DATABASE_URL=INVALID_IMPLICIT_DOTENV\n", encoding="ascii")
    destination = root / "installation"
    database = "render_release_" + run_id
    checks: list[str] = []
    report: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "passed": False, "run_id": run_id, "checks": checks, "virtualization_used": False, "production_accepted": False}
    native_spec = importlib.util.spec_from_file_location("release_owned_processes", ROOT / "scripts/test-sub2api-native.py")
    assert native_spec and native_spec.loader
    native = importlib.util.module_from_spec(native_spec)
    native_spec.loader.exec_module(native)
    processes = native.Processes(root)
    admin = None
    created = False
    client = None

    def execute(name: str, arguments: list[str], *, env: dict[str, str], expected: int = 0) -> subprocess.CompletedProcess:
        completed = subprocess.run(arguments, env=env, cwd=cwd, capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed isolated interpreter and owned test paths
        (root / (name + ".stdout")).write_bytes(completed.stdout)
        (root / (name + ".stderr")).write_bytes(completed.stderr)
        assert (completed.returncode == 0) == (expected == 0), name + "_exit_code"
        return completed

    try:
        manifest = verify_archive(args.archive, args.sha256)
        report.update({"archive_sha256": args.sha256, "release_id": manifest["release_id"], "source_tree_sha256": manifest["source_tree_sha256"], "payload_files": len(manifest["files"]), "database_head": manifest["database"]["head"]})
        base_python = str(Path(sys.base_prefix) / "python.exe")
        environment = clean_environment()
        # Deliberately exclude pre-existing ffmpeg/ffprobe from this rehearsal.
        environment["PATH"] = "C:/Windows/System32"
        environment["PIP_INDEX_URL"] = "https://127.0.0.1:9/unreachable"
        installer = args.archive.parent / "release_bundle.py"
        installed = execute("offline-install", [base_python, "-I", str(installer), "install", "--archive", str(args.archive), "--sha256", args.sha256, "--destination", str(destination)], env=environment)
        report["installation"] = json.loads(installed.stdout)
        checks.append("fresh_offline_wheels_seven_modules_frozen_contracts_no_editable_dependencies")
        python = str(destination / "venv/Scripts/python.exe")
        payload = destination / "payload"
        entry = str(payload / "scripts/release-entrypoint.py")
        s3_probe = """
import json
from io import BytesIO
from generation_domain.errors import DomainError
from generation_infrastructure.storage import S3Store
class Client:
    def get_object(self, **kwargs):
        return self.reply
store = S3Store.__new__(S3Store)
store.bucket, store.client = 'synthetic-private', Client()
for wrong in (False, True):
    body = BytesIO(b'abc')
    store.client.reply = {'Body':body,'VersionId':'wrong' if wrong else 'version','ContentLength':3,'ContentRange':'bytes 0-2/3','ResponseMetadata':{'HTTPStatusCode':206}}
    if wrong:
        try:
            next(store.read('outputs/synthetic','version',0,2))
        except DomainError as error:
            assert error.code == 'storage_response_invalid'
        else:
            raise AssertionError('installed_s3_version_guard_missing')
    else:
        assert b''.join(store.read('outputs/synthetic','version',0,2)) == b'abc'
    assert body.closed
print(json.dumps({'passed':True,'isolated_installed_module':True,'valid_stream_and_wrong_version_guard':True,'response_bodies_closed':True,'real_s3':False}))
"""
        s3_result = execute("installed-s3-boundary", [python, "-I", "-c", s3_probe], env=environment)
        report["s3_boundary"] = json.loads(s3_result.stdout)
        assert report["s3_boundary"]["passed"]
        checks.append("installed_s3_exact_version_guard_and_bounded_read_close_without_source_checkout")
        if manifest["contracts"]["version"] == "0.5.0":
            media_result = execute("installed-audio", [python, "-I", str(ROOT / "scripts/release-media-smoke.py"), "--payload", str(payload), "--output", str(state)], env=environment)
            report["audio_media"] = json.loads(media_result.stdout)
            assert report["audio_media"]["passed"] and len(report["audio_media"]["formats"]) == 4
            checks.append("installed_four_audio_formats_decode_without_global_ffmpeg")
        execute("existing-target", [base_python, "-I", str(installer), "install", "--archive", str(args.archive), "--sha256", args.sha256, "--destination", str(destination)], env=environment, expected=1)
        verify_payload(payload)
        checks.append("existing_installation_refused_without_modification")
        settings = Settings()
        url = make_url(settings.database_url.get_secret_value())
        assert settings.environment == "development" and (url.host, url.port, url.database) == ("127.0.0.1", 55438, "render_control")
        assert re.fullmatch(r"render_release_[0-9a-f]{12}", database)
        options = {"host": url.host, "port": url.port, "user": url.username, "password": url.password, "connect_timeout": 5, "sslmode": "disable"}
        admin = psycopg.connect(**{**options, "dbname": "postgres"}, autocommit=True)
        assert admin.execute("SELECT current_database(),host(inet_server_addr()),current_setting('port')").fetchone() == ("postgres", "127.0.0.1", "55438")
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
        created = True
        port = available_port()
        origin = f"https://127.0.0.1:{port}"
        password = secrets.token_urlsafe(32)
        pepper = secrets.token_urlsafe(48)
        values = {"GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": url.set(database=database).render_as_string(hide_password=False), "GEN_CREDENTIAL_PEPPER": pepper, "GEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GEN_PUBLIC_ORIGIN": origin, "GEN_STORAGE_BACKEND": "local_development", "GEN_LOCAL_STORAGE_ROOT": str(state / "objects"), "GEN_SPOOL_ROOT": str(state / "spool")}
        config = state / "cloud.env"
        config.write_text("\n".join(f'{name}={json.dumps(value)}' for name, value in values.items()) + "\n", encoding="utf-8")
        monitor_token_file = state / "monitoring.token"
        monitor_cli = [python, "-I", "-m", "control_api.monitoring_cli"]
        generated = execute("installed-monitoring-token", [*monitor_cli, "issue-token", "--output", str(monitor_token_file)], env=environment)
        monitoring_token = monitor_token_file.read_text(encoding="utf-8").strip()
        generated_data = json.loads(generated.stdout)
        assert monitoring_token not in generated.stdout.decode() and generated_data["credential_written"]
        refused = execute("installed-monitoring-token-existing", [*monitor_cli, "issue-token", "--output", str(monitor_token_file)], env=environment, expected=1)
        assert refused.stderr.strip() == b"monitoring_output_exists" and monitor_token_file.read_text(encoding="utf-8").strip() == monitoring_token
        with config.open("a", encoding="utf-8") as output:
            output.write("GEN_MONITORING_TOKEN_SHA256=" + json.dumps([generated_data["sha256"]]) + "\n")
        projected = execute("installed-monitoring-config", [*monitor_cli, "configuration", "--env-file", str(config)], env=environment)
        assert json.loads(projected.stdout)["credential_slots"] == 1
        assert (destination / "venv/Scripts/gen-monitoring.exe").is_file()
        assert (payload / "deploy/observability/prometheus.example.yml").is_file()
        rules_output = state / "monitoring-rules.json"
        generated_rules = execute("installed-monitoring-rules", [*monitor_cli, "rules", "--env-file", str(config), "--output", str(rules_output)], env=environment)
        assert json.loads(generated_rules.stdout)["alerts"] == 10 and len(json.loads(rules_output.read_text())["groups"][0]["rules"]) == 10
        checks.append("installed_monitoring_cli_external_config_secret_once_no_implicit_database_or_dotenv")
        common = [python, "-I", entry]
        cloud = ["--config", str(config), "--state-dir", str(state)]
        missing = execute("missing-revision", [*common, "runner", *cloud, "--once"], env=environment, expected=1)
        assert json.loads(missing.stderr)["error"] == "release_startup_failed"
        with psycopg.connect(**{**options, "dbname": database}, autocommit=True) as connection:
            assert connection.execute("SELECT to_regclass('public.alembic_version')").fetchone() == (None,)
        checks.append("startup_on_empty_database_refuses_without_implicit_ddl")
        backup = state / "backups"
        tools = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
        execute("migration", [python, "-I", str(payload / "scripts/migrate.py"), "--env-file", str(config), "--pg-tools", str(tools), "--backup-dir", str(backup)], env=environment)
        backups = list(backup.glob("*.json"))
        assert len(backups) == 1
        receipt = json.loads(backups[0].read_text(encoding="utf-8"))
        assert receipt["archive_list_verified"] and digest_file(backups[0].with_suffix(".dump")) == receipt["backup_sha256"]
        report["pre_migration_backup"] = {"archive_list_verified": True, "sha256": receipt["backup_sha256"], "created_before_ddl": True}
        checks.append("installed_migration_takes_native_pg18_backup_and_verifies_archive_before_ddl")
        # Provision through installed code; no dev import path is supplied.
        password_file = state / "password.txt"
        password_file.write_text(password, encoding="utf-8")
        bootstrap_env = {**environment, **values}
        execute("bootstrap", [python, "-I", "-m", "control_api.cli", "bootstrap-admin", "--username", "release-test", "--password-file", str(password_file)], env=bootstrap_env)
        recovery_entry = str(payload / "scripts/verify-recovery.py")
        recovery_key = state / "recovery.key"
        checkpoint = state / "checkpoint.json"
        generated = execute("recovery-keygen", [python, "-I", recovery_entry, "keygen", "--recovery-key-file", str(recovery_key)], env=environment)
        assert json.loads(generated.stdout) == {"recovery_key_created": True, "secret_displayed": False}
        original_key = recovery_key.read_bytes()
        execute("recovery-existing-key", [python, "-I", recovery_entry, "keygen", "--recovery-key-file", str(recovery_key)], env=environment, expected=1)
        assert recovery_key.read_bytes() == original_key
        checks.append("installed_recovery_keygen_is_independent_private_and_never_overwrites")
        recovery_args = ["--recovery-key-file", str(recovery_key), "--config", str(config), "--checkpoint", str(checkpoint)]
        execute("recovery-snapshot", [python, "-I", recovery_entry, "snapshot", *recovery_args], env=environment)
        checked = execute("recovery-verify", [python, "-I", recovery_entry, "verify", *recovery_args], env=environment)
        report["recovery_verification"] = json.loads(checked.stdout)
        assert report["recovery_verification"]["passed"] and report["recovery_verification"]["rows"] == 1
        assert report["recovery_verification"]["objects"] == 0 and report["recovery_verification"]["reconciliation_required_before_dispatch"]
        checks.append("installed_readonly_recovery_snapshot_and_verify_use_exact_candidate_modules")
        authentic = checkpoint.read_bytes()
        tampered = json.loads(authentic)
        tampered["signature"] = "0" * 64
        checkpoint.write_text(json.dumps(tampered), encoding="utf-8")
        rejected = execute("recovery-bad-signature", [python, "-I", recovery_entry, "verify", "--recovery-key-file", str(recovery_key), "--checkpoint", str(checkpoint), "--config", str(state / "never-created.env")], env=environment, expected=1)
        assert json.loads(rejected.stderr) == {"error": "recovery_signature_invalid"}
        checkpoint.write_bytes(authentic)
        assert original_key.strip() not in checked.stdout + rejected.stderr
        checks.append("installed_recovery_rejects_tampering_before_any_config_database_or_storage_access")
        # Guarded TLS and bad configuration failures must not echo input values.
        failed = execute("tls-required", [*common, "api", *cloud], env=environment, expected=1)
        assert json.loads(failed.stderr)["error"] == "explicit_tls_required"
        invalid = state / "invalid.env"
        invalid.write_text(config.read_text(encoding="utf-8").replace("GEN_PUBLIC_ORIGIN=", "GEN_UNKNOWN_SECRET_FIELD="), encoding="utf-8")
        bad = execute("invalid-config", [*common, "runner", "--config", str(invalid), "--state-dir", str(state), "--once"], env=environment, expected=1)
        assert json.loads(bad.stderr) == {"error": "release_startup_failed"}
        assert pepper.encode() not in bad.stderr and password.encode() not in bad.stderr
        checks.append("invalid_configuration_and_missing_tls_fail_without_secret_output")
        cert, key = ROOT / ".local/tls/localhost.crt", ROOT / ".local/tls/localhost.key"
        api = processes.start("installed-api", [*common, "api", *cloud, "--port", str(port), "--tls-cert", str(cert), "--tls-key", str(key)], environment)
        client = httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(cert)), trust_env=False, timeout=10)
        native.wait_http(client, "/health/ready", api)
        assert client.get("/health/live").status_code == 200
        html = client.get("/admin/")
        assert html.status_code == 200
        assets = re.findall(r'(?:src|href)="(/admin/assets/[^" ]+)"', html.text)
        assert assets and all(client.get(path).status_code == 200 for path in assets)
        checks.append("installed_https_health_and_bundled_admin_assets")
        assert client.get("/internal/monitoring/v1/metrics").status_code == 401
        scrape = client.get("/internal/monitoring/v1/metrics", headers={"Authorization": "Bearer " + monitoring_token})
        assert scrape.status_code == 200 and monitoring_token not in scrape.text
        snapshot_values = [s.value for f in text_string_to_metric_families(scrape.text) for s in f.samples if s.name == "render_control_snapshot_success"]
        assert snapshot_values == [1]
        checks.append("installed_https_monitoring_own_identity_dependency_and_readonly_snapshot")
        for path in ("/admin/api/v1/session", "/admin/api/v1/audit-retention", "/admin/api/v1/content", "/admin/api/v1/queues/statistics", "/admin/api/v1/channels/ch_" + "f" * 32 + "/deletion-check", "/v1/models"):
            assert client.get(path).status_code == 401
        assert client.get("/admin/api/v1/content", headers={"Authorization": "Bearer sk-synthetic-invalid"}).status_code == 401
        checks.append("unauthenticated_and_business_bearer_cannot_enter_admin")
        login = client.post("/admin/api/v1/session", headers={"Origin": origin}, json={"username": "release-test", "password": password_file.read_text(encoding="utf-8").strip()})
        assert login.status_code == 200
        lifetime = login.json()["lifetime"]
        assert lifetime["idle_timeout_seconds"] == 1800 and lifetime["idle_expires_at"] <= lifetime["absolute_expires_at"]
        headers = {"Origin": origin, "X-CSRF-Token": login.json()["csrf_token"]}
        retention = client.get("/admin/api/v1/audit-retention")
        assert retention.status_code == 200
        assert retention.json()["enabled"] is False and retention.json()["retention_days"] is None and retention.json()["batch_limit"] == 500
        assert client.post("/admin/api/v1/audit-retention", headers=headers).status_code == 405
        checks.append("installed_audit_retention_defaults_disabled_and_status_is_read_only")
        selected_channels = client.get("/admin/api/v1/channels", params={"q": "%_", "status": "deleted"})
        assert selected_channels.status_code == 200 and selected_channels.json() == {"data": [], "page": 1, "has_more": False}
        assert client.get("/admin/api/v1/channels", params={"page": 10001}).status_code == 422
        assert client.get("/admin/api/v1/channels", params={"unknown": "PRIVATE"}).status_code == 422
        checks.append("installed_channel_query_literal_filters_bounds_and_unknown_fields")
        deletion = client.get("/admin/api/v1/channels/ch_" + "f" * 32 + "/deletion-check")
        assert deletion.status_code == 404 and deletion.json()["error"]["code"] == "not_found"
        invalid_deletion = client.get("/admin/api/v1/channels/invalid/deletion-check")
        assert invalid_deletion.status_code == 404 and invalid_deletion.json()["error"]["code"] == "not_found"
        checks.append("installed_channel_deletion_endpoint_requires_identity_and_validates_identifiers")
        reapply_path = "/admin/api/v1/channels/ch_" + "f" * 32 + "/reapply"
        assert client.post(reapply_path, headers={**headers, "Idempotency-Key": "installed-reapply-test", "If-Match": '"1"'}, json={"expected_revision": 1}).status_code == 404
        assert client.post(reapply_path, headers={**headers, "Idempotency-Key": "installed-reapply-test"}, json={"expected_revision": 1}).status_code == 428
        recovery_path = "/internal/v1/commands/cmd_" + "f" * 32 + "/reconciliations"
        assert client.post(recovery_path, headers=headers, json={}).status_code == 401
        checks.append("installed_reapply_revision_and_worker_reconciliation_identity_boundaries")
        invalid = client.post("/admin/api/v1/session/activity", headers=headers, json={"activity": "interactive", "server_time": 1})
        assert invalid.status_code == 422
        renewed = client.post("/admin/api/v1/session/activity", headers=headers, json={"activity": "interactive"})
        assert renewed.status_code == 200 and renewed.json()["absolute_expires_at"] == lifetime["absolute_expires_at"]
        assert "set-cookie" not in renewed.headers
        assert client.delete("/admin/api/v1/session", headers=headers).status_code == 204
        assert client.post("/admin/api/v1/session/activity", headers=headers, json={"activity": "interactive"}).status_code == 401
        checks.append("installed_session_lifetime_csrf_explicit_activity_and_logout_without_source_checkout")
        for role in ("runner", "gc"):
            execute(role + "-once", [*common, role, *cloud, "--once"], env=environment)
        checks.append("installed_runner_and_independent_gc_execute_once")
        worker_state = root / "worker-state"
        worker_state.mkdir()
        credential = root / "worker-credential.json"
        credential.write_text(json.dumps({"node_id": "node_" + uuid4().hex, "token": "worker_" + uuid4().hex + "." + "A" * 43}), encoding="utf-8")
        worker_config = root / "worker.json"
        worker_config.write_text(json.dumps({"control_origin": origin, "credential_file": str(credential), "state_root": str(worker_state), "ca_file": str(cert), "gpus": [], "channels": []}), encoding="utf-8")
        # Real SQLite journal with an unresolved POST; candidate validation must preserve it.
        journal = "from pathlib import Path;from windows_worker.journal import Journal;j=Journal(Path(__import__('sys').argv[1]));j.accept_claim({'attempt_id':'synthetic-unresolved-attempt','execution_digest':'b'*64});association=j.intent('synthetic-unresolved-attempt','a'*64);j.begin_post('synthetic-unresolved-attempt',association);j.close()"
        execute("journal", [python, "-I", "-c", journal, str(worker_state)], env=environment)
        before = {path.relative_to(worker_state).as_posix(): digest_file(path) for path in worker_state.rglob("*") if path.is_file()}
        execute("worker-validate", [*common, "worker", "--config", str(worker_config), "--state-dir", str(worker_state), "--validate-config"], env=environment)
        assert before == {path.relative_to(worker_state).as_posix(): digest_file(path) for path in worker_state.rglob("*") if path.is_file()}
        checks.append("worker_explicit_config_preserves_unresolved_durable_journal_without_comfy_call")
        unbound = execute("worker-unbound", [*common, "worker", "--config", str(worker_config), "--state-dir", str(worker_state)], env=environment, expected=1)
        assert json.loads(unbound.stderr) == {"error": "release_startup_failed"}
        assert before == {path.relative_to(worker_state).as_posix(): digest_file(path) for path in worker_state.rglob("*") if path.is_file()}
        checks.append("installed_worker_refuses_unbound_legacy_journal_without_changing_execution_facts")
        # The explicit, backed-up adoption completes locally; synthetic credentials
        # are then rejected by the real installed API, exercising failure cleanup.
        adopted = execute("worker-adopt", [*common, "worker", "--config", str(worker_config), "--state-dir", str(worker_state), "--adopt-legacy-journal"], env=environment, expected=1)
        assert json.loads(adopted.stderr) == {"error": "release_startup_failed"}
        probe = "import json,sqlite3,sys;from pathlib import Path;p=Path(sys.argv[1]);c=sqlite3.connect(p/'journal.sqlite3');assert c.execute('SELECT COUNT(*) FROM worker_identity').fetchone()[0]==1;assert c.execute('SELECT stage,cloud_ack FROM attempts').fetchone()==('posting',0);c.close();b=list((p/'identity-backups').glob('*.sqlite3'));assert len(b)==1;c=sqlite3.connect(b[0]);assert c.execute('PRAGMA quick_check').fetchone()==('ok',);assert c.execute('SELECT COUNT(*) FROM worker_identity').fetchone()[0]==0;assert c.execute('SELECT stage,cloud_ack FROM attempts').fetchone()==('posting',0);c.close();print(json.dumps({'backup_valid':True,'unknown_submit_preserved':True}))"
        execute("worker-adoption-proof", [python, "-I", "-c", probe, str(worker_state)], env=environment)
        checks.append("installed_worker_explicit_adoption_verifies_backup_and_retains_unknown_submit")
        # Startup revision guard checks a deliberately wrong revision without changing DDL.
        with psycopg.connect(**{**options, "dbname": database}, autocommit=True) as connection:
            connection.execute("UPDATE alembic_version SET version_num='000000000000'")
        wrong = execute("wrong-revision", [*common, "runner", *cloud, "--once"], env=environment, expected=1)
        assert json.loads(wrong.stderr)["error"] == "database_revision_incompatible"
        with psycopg.connect(**{**options, "dbname": database}, autocommit=True) as connection:
            connection.execute("UPDATE alembic_version SET version_num=%s", (manifest["database"]["head"],))
        checks.append("incompatible_database_revision_blocks_startup_without_migration")
        fixture = root / "browser-private.json"
        browser_report = evidence / "native-release-installed-browser.json"
        fixture.write_text(json.dumps({"origin": origin, "password": password, "report": str(browser_report), "screenshot_desktop": str(evidence / "native-release-installed-desktop.png"), "screenshot_mobile": str(evidence / "native-release-installed-mobile.png")}), encoding="utf-8")
        # Playwright is an existing test-host user-site dependency. Only this
        # external browser driver may load it; installed product commands above
        # keep -I and PYTHONNOUSERSITE=1 throughout.
        browser_environment = {**environment}
        browser_environment.pop("PYTHONNOUSERSITE", None)
        execute("browser", [base_python, str(ROOT / "scripts/browser-release-install-smoke.py"), "--fixture", str(fixture)], env=browser_environment)
        browser = json.loads(browser_report.read_text(encoding="utf-8"))
        assert browser["passed"] and browser["browser_closed"] and browser["closed_contexts"] == 2
        report["browser"] = {key: browser[key] for key in ("passed", "checks", "errors", "closed_contexts", "browser_closed")}
        checks.append("fresh_installed_real_browser_desktop_mobile_admin_and_content")
        verify_payload(payload)
        # Verify after real operation and test a tampered immutable member.
        index = payload / "admin/index.html"
        original = index.read_bytes()
        try:
            index.write_bytes(original + b"tampered")
            denied = execute("tampered-payload", [*common, "runner", *cloud, "--once"], env=environment, expected=1)
            assert json.loads(denied.stderr)["error"] == "payload_digest_mismatch"
        finally:
            index.write_bytes(original)
        verify_payload(payload)
        checks.append("runtime_tampered_payload_is_rejected_and_manifest_stays_immutable")
        report["passed"] = True
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "code": str(exc) if isinstance(exc, AssertionError) else "native_release_smoke_failed"}
        raise
    finally:
        if client:
            client.close()
        processes.close()
        report["processes"] = processes.stops
        if admin:
            if created:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                report["temporary_database_removed"] = admin.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (database,)).fetchone()[0] == 0
            admin.close()
        # Preserve non-sensitive logs for a failed run; private configuration,
        # credentials, venv and owned databases are always cleaned.
        if report["passed"]:
            remove_created(root, ROOT / ".local")
            report["temporary_root_removed"] = not root.exists()
        else:
            for name in ("installation", "state", "worker-state", "outside-project"):
                target = root / name
                if target.is_dir():
                    remove_created(target, root)
            for path in (root / "browser-private.json", root / "worker-credential.json", root / "worker.json"):
                path.unlink(missing_ok=True)
            report["private_resources_cleaned"] = True
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        (evidence / "native-release-installation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "run_id": run_id, "checks": len(checks), "cleaned": report.get("temporary_root_removed", report.get("private_resources_cleaned", False))}))


if __name__ == "__main__":
    main()
