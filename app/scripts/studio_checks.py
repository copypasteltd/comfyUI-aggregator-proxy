"""Run focused native Studio checks against this checkout, with a locked test DB backup."""
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
sys.path[:0] = [str(APP), *[str(path) for folder in ("packages", "apps") for path in (APP / folder).glob("*/src")]]
os.chdir(APP)
os.environ["PYTHONPATH"] = os.pathsep.join(sys.path[:1 + sum(1 for folder in ("packages", "apps") for _ in (APP / folder).glob("*/src"))])

from native_ci import database_lock, file_lock, test_url  # noqa: E402

run_id = "studio-" + uuid4().hex
private = APP / ".local" / run_id
private.mkdir(parents=True)
report_dir = APP.parent / "docs" / "evidence" / "studio-release-131"
report_dir.mkdir(parents=True, exist_ok=True)
started = datetime.now(UTC)
mode = sys.argv[1] if len(sys.argv) > 1 else "focused"
exit_code = 1
backup_verified = False
database_durability = None
source_before = {}
source_after = {}


def snapshot() -> dict[str, str]:
    paths = [path for folder in ("packages", "apps") for base in (APP / folder).glob("*/src") for path in base.rglob("*") if path.is_file() and path.suffix in {".py", ".json", ".ts", ".vue"} and "__pycache__" not in path.parts]
    paths.extend(path for folder in ("tests", "scripts", "deploy") for path in (APP / folder).rglob("*.py") if "__pycache__" not in path.parts)
    return {"app/" + path.relative_to(APP).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


try:
    with (private / "raw.log").open("w", encoding="utf-8") as output, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        if mode == "openapi":
            import runpy
            sys.argv = [str(APP / "scripts/export-openapi.py")]
            runpy.run_path(sys.argv[0], run_name="__main__")
            exit_code = 0
        elif mode in ("focused", "compatibility", "recheck-admin"):
            source_before = snapshot()
            import pytest
            with file_lock(APP / ".local/studio-checks.lock"), database_lock():
                url = test_url()
                import psycopg
                with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname=url.database, sslmode="disable", gssencmode="disable") as inspection:
                    flags = inspection.execute("SELECT current_setting('fsync'), current_setting('synchronous_commit')").fetchone()
                    database_durability = {"fsync": flags[0], "synchronous_commit": flags[1], "crash_durability_tested": False}
                source = Path(sys.executable).parents[2] / ".local/toolchains/postgres-18.6"
                dump_tool = source / "pgsql/bin/pg_dump.exe"
                if not dump_tool.is_file():
                    raise RuntimeError("native_pg_backup_tool_missing")
                dump = private / "before-test.dump"
                env = {**os.environ, "PGPASSWORD": url.password or "", "PGSSLMODE": "disable", "PGGSSENCMODE": "disable"}
                # Fixed native tools and argv; test_url() rejects any non-loopback/non-test DSN.
                subprocess.run([str(dump_tool), "-Fc", "--host", str(url.host), "--port", str(url.port), "--username", str(url.username), "--dbname", str(url.database), "--file", str(dump)], env=env, stdout=output, stderr=output, check=True)  # noqa: S603
                subprocess.run([str(dump_tool.with_name("pg_restore.exe")), "--list", str(dump)], stdout=output, stderr=output, check=True)  # noqa: S603
                backup_verified = True
                selected = ["tests/unit/test_domain.py", "tests/unit/test_model3d.py", "tests/integration/test_studio_release_guard.py", "tests/integration/test_http_auth.py", "tests/integration/test_http_cors.py"] if mode == "focused" else ["tests/integration/test_http_auth.py::test_admin_content_metadata_range_delete_and_observer_access"]
                if mode == "compatibility":
                    selected = ["tests/unit", "tests/integration/test_studio_release_guard.py", "tests/integration/test_http_auth.py", "tests/integration/test_http_cors.py", "tests/integration/test_discovery.py", "tests/integration/test_model_documentation.py", "tests/integration/test_model_catalog.py", "tests/integration/test_model3d_pipeline.py", "tests/integration/test_asset_idempotency.py", "tests/integration/test_gateway_catalogs.py", "tests/integration/test_content.py", "tests/integration/test_content_retention.py", "tests/integration/test_content_management_operations.py", "tests/integration/test_job_cancellations.py"]
                exit_code = int(pytest.main([*selected, "--basetemp", str(private / "temp"), "--junitxml", str(private / "results.xml"), "-q"]))
            source_after = snapshot()
        else:
            raise RuntimeError("unknown_check_mode")
except BaseException as error:
    (private / "failure-type.txt").write_text(type(error).__name__, encoding="ascii")
    (private / "failure-private.txt").write_text(traceback.format_exc(), encoding="utf-8")
    print(json.dumps({"passed": False, "error_type": type(error).__name__, "private_run": run_id}))

counts = {}
inventory = {}
if (private / "results.xml").exists():
    root = ET.parse(private / "results.xml").getroot()  # noqa: S314 - this process's own pytest output, never an imported file
    counts = {key: sum(int(suite.get(key, 0)) for suite in root.iter("testsuite")) for key in ("tests", "failures", "errors", "skipped")}
    for case in root.iter("testcase"):
        path = "app/" + case.get("classname", "").replace(".", "/") + ".py"
        symbol = case.get("name", "").split("[", 1)[0]
        key = (path, symbol)
        inventory[key] = inventory.get(key, 0) + 1
result = {"run_id": run_id, "started_at": started.isoformat(), "finished_at": datetime.now(UTC).isoformat(), "mode": mode, "passed": exit_code == 0 and not any(counts.get(key, 0) for key in ("failures", "errors", "skipped")), "exit_code": exit_code, "counts": counts, "test_database_backup_verified": backup_verified, "database_durability": database_durability, "virtualization_used": False, "scope": "native unit/HTTP/PostgreSQL transaction/row-lock regression; not crash durability or live GPU evidence"}
if counts:
    unchanged = bool(source_before) and source_before == source_after
    result.update({"format_version": 1, "sources_unchanged": unchanged, "source_sha256": source_after, "real_resource_acceptance": False, "full_acceptance": "pending", "python": counts, "python_tests": [{"path": path, "symbol": symbol, "passed_cases": count} for (path, symbol), count in sorted(inventory.items())], "junit_sha256": hashlib.sha256((private / "results.xml").read_bytes()).hexdigest(), "python_log_sha256": hashlib.sha256((private / "raw.log").read_bytes()).hexdigest()})
    result["passed"] = result["passed"] and unchanged
(report_dir / f"{run_id}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps({key: value for key, value in result.items() if key not in {"source_sha256", "python_tests"}}))
raise SystemExit(0 if result["passed"] else 1)
