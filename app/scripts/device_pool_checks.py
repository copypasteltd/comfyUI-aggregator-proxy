"""137 native control-plane evidence; raw test output and backup stay private."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), *[str(base) for folder in ("packages", "apps") for base in (APP / folder).glob("*/src")]]
os.chdir(APP)
os.environ["PYTHONPATH"] = os.pathsep.join(sys.path[:1 + sum(1 for folder in ("packages", "apps") for _ in (APP / folder).glob("*/src"))])
from native_ci import database_lock, file_lock, snapshot, test_url  # noqa: E402


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "focused"
    if mode not in {"focused", "compatibility"}:
        raise SystemExit("Choose focused or compatibility")
    run_id = "device-pool-" + uuid4().hex
    private = APP / ".local" / run_id
    private.mkdir(parents=True)
    report_dir = APP.parent / "docs/evidence/studio-release-137"
    report_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    code, backup_verified, source_before, source_after, durability = 1, False, {}, {}, None
    try:
        with (private / "raw.log").open("w", encoding="utf-8") as output, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            import pytest
            with file_lock(APP / ".local/studio-checks.lock"), database_lock():
                url = test_url()
                import psycopg
                with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname=url.database, sslmode="disable", gssencmode="disable") as inspection:
                    flags = inspection.execute("SELECT current_setting('fsync'),current_setting('synchronous_commit')").fetchone()
                    durability = {"fsync": flags[0], "synchronous_commit": flags[1], "crash_durability_tested": False}
                dump_tool = Path(sys.executable).parents[2] / ".local/toolchains/postgres-18.6/pgsql/bin/pg_dump.exe"
                env = {**os.environ, "PGPASSWORD": url.password or "", "PGSSLMODE": "disable", "PGGSSENCMODE": "disable"}
                dump = private / "before-test.dump"
                subprocess.run([str(dump_tool), "-Fc", "--host", str(url.host), "--port", str(url.port), "--username", str(url.username), "--dbname", str(url.database), "--file", str(dump)], env=env, stdout=output, stderr=output, check=True)  # noqa: S603
                subprocess.run([str(dump_tool.with_name("pg_restore.exe")), "--list", str(dump)], stdout=output, stderr=output, check=True)  # noqa: S603
                backup_verified = True
                source_before = snapshot()
                selected = ["tests/unit/test_device_pool.py", "tests/integration/test_device_pool_transactions.py"]
                if mode == "compatibility":
                    selected = ["tests/unit", *["tests/integration/" + name + ".py" for name in (
                        "test_device_pool_transactions", "test_dispatch_transactions", "test_internal_retry_transactions", "test_confirmed_failure_retries",
                        "test_twenty_workers", "test_recovery", "test_worker_capacity", "test_joint_recovery", "test_model_catalog", "test_model_documentation",
                        "test_gateway_catalogs", "test_gateway", "test_queue", "test_http_auth", "test_http_cors", "test_studio_release_guard",
                        "test_studio_submission_lookup", "test_memory_reclamation", "test_job_cancellations", "test_spec_isolation_operations", "test_database_failure_http",
                    )]]
                if any(not (APP / name).exists() for name in selected):
                    raise RuntimeError("required_test_path_missing")
                code = int(pytest.main([*selected, "--basetemp", str(private / "temp"), "--junitxml", str(private / "results.xml"), "-q", "--tb=short"]))
                source_after = snapshot()
    except BaseException as error:
        (private / "failure-private.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(json.dumps({"error_type": type(error).__name__, "private_run": run_id}))

    counts, inventory = {}, {}
    if (private / "results.xml").exists():
        root = ET.parse(private / "results.xml").getroot()  # noqa: S314 - own pytest artifact
        counts = {key: sum(int(suite.get(key, 0)) for suite in root.iter("testsuite")) for key in ("tests", "failures", "errors", "skipped")}
        for case in root.iter("testcase"):
            key = ("app/" + case.get("classname", "").replace(".", "/") + ".py", case.get("name", "").split("[", 1)[0])
            inventory[key] = inventory.get(key, 0) + 1
    result = {"format_version": 1, "run_id": run_id, "mode": mode, "started_at": started.isoformat(), "finished_at": datetime.now(UTC).isoformat(), "passed": code == 0 and bool(counts) and not any(counts.get(key, 0) for key in ("failures", "errors", "skipped")) and bool(source_before) and source_before == source_after, "exit_code": code, "counts": counts, "test_database_backup_verified": backup_verified, "database_durability": durability, "sources_unchanged": bool(source_before) and source_before == source_after, "source_sha256": source_after, "python_tests": [{"path": path, "symbol": symbol, "passed_cases": count} for (path, symbol), count in sorted(inventory.items())], "scope": "native PostgreSQL/control-plane only; synthetic resources are not a physical GPU fleet", "virtualization_used": False, "real_resource_acceptance": False, "full_acceptance": "pending"}
    for name in ("raw.log", "results.xml"):
        if (private / name).exists():
            result[name + "_sha256"] = hashlib.sha256((private / name).read_bytes()).hexdigest()
    (report_dir / (run_id + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"source_sha256", "python_tests"}}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
