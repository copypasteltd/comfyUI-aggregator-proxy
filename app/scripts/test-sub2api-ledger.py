"""Test our additive Sub2API ledger overlay against an isolated native PG database."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

import psycopg
from generation_infrastructure.settings import Settings
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def materialize(lock: dict) -> tuple[Path, dict[str, str], bool]:
    contracts = ROOT.parent / "docs/contracts"
    inputs = ROOT / "integrations/sub2api/overlay/backend/internal/rendercontrol/testdata"
    cases = json.loads((contracts / "fixtures.json").read_text(encoding="utf-8"))
    maintained = json.loads((inputs / "job-create-fixtures.json").read_text(encoding="utf-8"))
    expected = {"contract_version": cases["contract_version"], "source": "docs/contracts/fixtures.json", "cases": [case for case in cases["cases"] if case["schema"] == "job-create.schema.json"]}
    if maintained != expected or (inputs / "job-create-schema.sha256").read_text(encoding="ascii").strip() != sha256(contracts / "job-create.schema.json"):
        raise RuntimeError("Public Provider parser fixtures/schema require review against the frozen contract")
    archive = ROOT / (".local/research/sub2api-" + lock["commit"] + ".zip")
    if sha256(archive) != lock["archive_sha256"] or archive.stat().st_size != lock["archive_bytes"]:
        raise RuntimeError("Pinned source archive does not match the lock")
    patch_spec = json.loads((ROOT / "integrations/sub2api/overlay/backend/render_control_patches.json").read_text(encoding="utf-8"))
    allowed_patches = {"internal/server/middleware/api_key_auth.go", "cmd/server/wire_gen.go"}
    if patch_spec["source_commit"] != lock["commit"] or len(patch_spec["patches"]) != 2 or {item["path"] for item in patch_spec["patches"]} != allowed_patches:
        raise RuntimeError("Unsupported stock patch scope")
    patches = {item["path"]: item for item in patch_spec["patches"]}
    patched_payloads: dict[str, bytes] = {}
    parent = (ROOT / ".local/upstream").resolve()
    target = parent / ("rc-ledger-" + uuid4().hex[:12])
    previous_sources: dict[str, str] = {}
    reused = False
    marker = ROOT / ".local/sub2api-overlay-workspace.json"
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        candidate = Path(previous["backend"])
        if candidate.parent == parent and re.fullmatch(r"rc-ledger-[a-f0-9]{12}", candidate.name) and candidate.is_dir() and not candidate.is_symlink() and candidate.resolve() == candidate:
            target, previous_sources, reused = candidate, previous["overlay_files_sha256"], True
    if not target.resolve().is_relative_to(parent):
        raise RuntimeError("Overlay workspace escaped the project")
    if not reused:
        target.mkdir(exist_ok=False)
    print(json.dumps({"phase": "verify_overlay_workspace", "reuse": reused}), flush=True)
    total = 0
    seen: set[PurePosixPath] = set()
    verified_parents: dict[PurePosixPath, Path] = {PurePosixPath("."): target}
    verification_started = time.monotonic()
    prefix = "sub2api-" + lock["commit"] + "/backend/"
    with zipfile.ZipFile(archive) as snapshot:
        for item in snapshot.infolist():
            if not item.filename.startswith(prefix) or item.is_dir():
                continue
            relative = PurePosixPath(item.filename[len(prefix):])
            # Native Windows file inspection can exceed three minutes under
            # concurrent test I/O. Keep every archive/hash/path check and a
            # finite overall budget instead of accepting a partial inspection.
            if time.monotonic() - verification_started > 600:
                raise TimeoutError("Overlay source verification exceeded 600 seconds")
            total += item.file_size
            mode = item.external_attr >> 16
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative) or ":" in str(relative) or relative in seen or stat.S_ISLNK(mode) or total > 256 << 20 or len(seen) >= 10000:
                raise RuntimeError("Unsupported pinned archive entry")
            seen.add(relative)
            if relative.parent not in verified_parents:
                source_parent = (target / relative.parent).resolve()
                if not source_parent.is_relative_to(target):
                    raise RuntimeError("Stock source escaped the owned overlay directory")
                verified_parents[relative.parent] = source_parent
            destination = verified_parents[relative.parent] / relative.name
            if spec := patches.get(relative.as_posix()):
                original = snapshot.read(item)
                if hashlib.sha256(original).hexdigest() != spec["stock_sha256"]:
                    raise RuntimeError("Stock patch input differs from the reviewed source")
                replacements = spec.get("replacements", [{"old": spec.get("old"), "new": spec.get("new")}])
                if not 1 <= len(replacements) <= 3:
                    raise RuntimeError("Unsupported stock replacement count")
                patched = original
                for replacement in replacements:
                    before, after = replacement["old"].encode("utf-8"), replacement["new"].encode("utf-8")
                    if not before or len(before) > 2048 or len(after) > 2048 or patched.count(before) != 1:
                        raise RuntimeError("Stock patch replacement differs from the reviewed source")
                    patched = patched.replace(before, after)
                if hashlib.sha256(patched).hexdigest() != spec["patched_sha256"]:
                    raise RuntimeError("Stock patch output differs from the reviewed source")
                patched_payloads[relative.as_posix()] = patched
            if reused:
                with snapshot.open(item) as source:
                    expected = hashlib.file_digest(source, "sha256").hexdigest()
                if relative.as_posix() in patches:
                    expected = previous_sources.get(relative.as_posix(), expected)
                if not destination.is_file() or destination.is_symlink() or sha256(destination) != expected:
                    raise RuntimeError("Cached stock source differs from the fixed archive")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with snapshot.open(item) as source, destination.open("xb") as output:
                    while chunk := source.read(1 << 20):
                        output.write(chunk)
            if len(seen) % 512 == 0:
                print(json.dumps({"phase": "verify_overlay_workspace", "stock_files_checked": len(seen)}), flush=True)
    expected_paths = {name.as_posix() for name in seen} | set(previous_sources)
    actual_paths = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
    if actual_paths != expected_paths:
        raise RuntimeError("Overlay workspace contains extra or missing files")
    for name, expected in lock["source_sha256"].items():
        relative = PurePosixPath(name).relative_to("backend")
        if sha256(target / relative) != expected:
            raise RuntimeError("Materialized stock source differs from the pinned lock")
    overlay = ROOT / "integrations/sub2api/overlay/backend"
    sources = sorted(path for path in overlay.rglob("*") if path.is_file())
    if set(patched_payloads) != set(patches):
        raise RuntimeError("Reviewed stock patch target missing from archive")
    manifest: dict[str, str] = {}
    for name, payload in patched_payloads.items():
        destination = target / name
        if sha256(destination) != hashlib.sha256(payload).hexdigest():
            destination.write_bytes(payload)
        manifest[name] = hashlib.sha256(payload).hexdigest()
    for source in sources:
        relative = source.relative_to(overlay)
        destination = target / relative
        if PurePosixPath(relative.as_posix()) in seen or source.is_symlink() or not source.resolve().is_relative_to(overlay.resolve()) or not destination.resolve().is_relative_to(target):
            raise RuntimeError("Overlay must add regular new files without modifying pinned stock files")
        current_digest = sha256(source)
        if destination.exists() and (relative.as_posix() not in previous_sources or destination.is_symlink() or sha256(destination) != previous_sources[relative.as_posix()]):
            raise RuntimeError("Previously generated overlay was edited outside the maintained source")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or sha256(destination) != current_digest:
            destination.write_bytes(source.read_bytes())
        manifest[relative.as_posix()] = current_digest
    # Compile the existing protocol client from its maintained source; do not
    # create a second independently edited implementation or alter go.mod.
    for client in sorted((ROOT / "integrations/sub2api/controlclient").glob("*.go")):
        if client.name.endswith("_test.go"):
            continue
        name = "internal/rendercontrol/controlclient/" + client.name
        generated = target / name
        if client.is_symlink() or not client.resolve().is_relative_to(ROOT / "integrations/sub2api/controlclient"):
            raise RuntimeError("Protocol client must be a maintained regular file")
        if generated.exists() and (generated.is_symlink() or sha256(generated) != previous_sources.get(name)):
            raise RuntimeError("Generated protocol client was edited outside its maintained source")
        generated.parent.mkdir(parents=True, exist_ok=True)
        client_digest = sha256(client)
        if not generated.exists() or sha256(generated) != client_digest:
            generated.write_bytes(client.read_bytes())
        manifest[name] = client_digest
    if set(previous_sources) - set(manifest):
        raise RuntimeError("Removed overlay files require a fresh generated workspace")
    return target, manifest, reused


def native_environment() -> tuple[Path, dict[str, str]]:
    toolchain = json.loads((ROOT / "config/native-toolchains.lock.json").read_text(encoding="utf-8"))["sub2api_go"]
    if sha256(ROOT / ".local/toolchains/go1.27.3.windows-amd64.zip") != toolchain["sha256"]:
        raise RuntimeError("Native Go archive differs from the official SHA-256")
    go = ROOT / ".local/toolchains/go-1.27.3/go/bin/go.exe"
    environment = os.environ.copy()
    for name in ("RC_TEST_CHILD_STAGE", "RC_TEST_USER", "RC_TEST_KEY", "RC_TEST_INTENT", "RC_TEST_INSTANCE", "RC_TEST_GATEWAY_ORIGIN", "RC_TEST_GATEWAY_CERT"):
        environment.pop(name, None)
    environment.update({"GOENV": "off", "GOTOOLCHAIN": "local", "GOTELEMETRY": "off", "GOWORK": "off", "GOFLAGS": "", "CGO_ENABLED": "0", "GOOS": "windows", "GOARCH": "amd64", "GOPROXY": "off", "GOSUMDB": "off", "GOAUTH": "off", "GOVCS": "*:off", "GOCACHE": str(ROOT / ".local/go/build-cache"), "GOPATH": str(ROOT / ".local/go/path"), "GOMODCACHE": str(ROOT / ".local/go/module-cache"), "GOTMPDIR": str(ROOT / ".local/go/tmp")})
    for name in ("GOCACHE", "GOPATH", "GOMODCACHE", "GOTMPDIR"):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(go), "version"], env=environment, capture_output=True, check=True, timeout=30).stdout.decode("utf-8").strip()  # noqa: S603 -- fixed officially hash-verified native compiler
    if version != "go version go1.27.3 windows/amd64":
        raise RuntimeError("Unexpected native Go version")
    return go, environment


def binary_identity(lock: dict, sources: dict[str, str], environment: dict[str, str]) -> str:
    toolchain = json.loads((ROOT / "config/native-toolchains.lock.json").read_text(encoding="utf-8"))["sub2api_go"]
    inputs = {"archive": lock["archive_sha256"], "toolchain": toolchain["sha256"], "modules": {name: lock["source_sha256"]["backend/" + name] for name in ("go.mod", "go.sum")}, "overlay": sources, "flags": ["-trimpath", "-vet=off", "-mod=readonly", "-p=2"], "environment": {name: environment.get(name, "") for name in ("CGO_ENABLED", "GOOS", "GOARCH", "GOAMD64", "GOEXPERIMENT", "GODEBUG")}}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_owned(command: list[str], backend: Path, environment: dict[str, str], log: Path, timeout: int) -> tuple[int, bool]:
    timed_out = False
    with log.open("wb") as output, subprocess.Popen(command, cwd=backend, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW) as process:  # noqa: S603 -- fixed package and verified project-native executable
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if process.poll() is None:
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=15)  # noqa: S603 -- live Popen-owned test/compile process tree only
            if process.poll() is None:
                process.kill()
            result = process.wait(timeout=15)
    return result, timed_out


def execute_tests(backend: Path, go: Path, environment: dict[str, str], log: Path, lock: dict, sources: dict[str, str]) -> tuple[int, bool, dict]:
    # A verified binary avoids repeating the entire upstream module graph when
    # only runtime fixtures or this Python harness change. Source verification
    # still runs on every invocation; Go/source/module/toolchain changes rebuild.
    binary = ROOT / ".local/bin/sub2api-ledger.test.exe"
    marker = ROOT / ".local/sub2api-ledger-binary-cache.json"
    identity = binary_identity(lock, sources, environment)
    cached = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    reused = bool(binary.is_file() and cached.get("input_sha256") == identity and cached.get("binary_sha256") == sha256(binary))
    log.write_bytes(b"")
    build_seconds = 0.0
    if not reused:
        binary.parent.mkdir(parents=True, exist_ok=True)
        command = [str(go), "test", "-c", "-trimpath", "-vet=off", "-mod=readonly", "-p=2", "-o", str(binary), "./internal/rendercontrol"]
        print(json.dumps({"phase": "compile_native_ledger_test_binary", "timeout_seconds": 900}), flush=True)
        build_started = time.monotonic()
        result, timed_out = run_owned(command, backend, environment, ROOT / ".local/sub2api-ledger-build.log", 900)
        build_seconds = round(time.monotonic() - build_started, 2)
        if result != 0 or timed_out or not binary.exists():
            return result or 1, timed_out, {"compiled": False, "binary_reused": False, "build_seconds": build_seconds}
        stable = all(sha256(backend / name) == expected for name, expected in sources.items()) and all(sha256(backend / name) == lock["source_sha256"]["backend/" + name] for name in ("go.mod", "go.sum"))
        if not stable:
            return 1, False, {"compiled": False, "binary_reused": False, "build_seconds": build_seconds}
        cached = {"input_sha256": identity, "binary_sha256": sha256(binary)}
        marker.write_text(json.dumps(cached, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"phase": "execute_native_ledger_test_binary", "binary_reused": reused, "timeout_seconds": 420}), flush=True)
    # Go 1.27 ships the source of this tool and builds it lazily. Build it with
    # the project-local cache and a fixed output, rather than assuming an exe
    # exists in GOROOT or letting a default user cache receive the artifact.
    converter = ROOT / ".local/bin/sub2api-test2json.exe"
    converter_marker = ROOT / ".local/sub2api-test2json-cache.json"
    converter_cached = json.loads(converter_marker.read_text(encoding="utf-8-sig")) if converter_marker.exists() else {}
    toolchain = json.loads((ROOT / "config/native-toolchains.lock.json").read_text(encoding="utf-8"))["sub2api_go"]
    converter_reused = bool(converter.is_file() and converter_cached.get("toolchain_sha256") == toolchain["sha256"] and converter_cached.get("binary_sha256") == sha256(converter))
    if not converter_reused:
        command = [str(go), "build", "-trimpath", "-mod=readonly", "-p=2", "-o", str(converter), "cmd/test2json"]
        result, timed_out = run_owned(command, backend, environment, ROOT / ".local/sub2api-test2json-build.log", 300)
        if result != 0 or timed_out:
            return result or 1, timed_out, {"compiled": True, "binary_reused": reused, "converter_build_failed": True}
        converter_cached = {"toolchain_sha256": toolchain["sha256"], "binary_sha256": sha256(converter)}
        converter_marker.write_text(json.dumps(converter_cached, indent=2) + "\n", encoding="utf-8")
    command = [str(converter), "-t", "-p", "github.com/Wei-Shaw/sub2api/internal/rendercontrol", str(binary), "-test.v=test2json", "-test.count=1", "-test.timeout=240s"]
    result, timed_out = run_owned(command, backend, environment, log, 420)
    return result, timed_out, {"compiled": True, "binary_reused": reused, "build_seconds": build_seconds, "binary_sha256": cached["binary_sha256"], "input_sha256": identity, "converter_binary_sha256": converter_cached["binary_sha256"], "converter_reused": converter_reused}


def main() -> None:
    started = time.monotonic()
    harness_digest = sha256(Path(__file__))
    lock = json.loads((ROOT / "integrations/sub2api/upstream-lock.json").read_text(encoding="utf-8"))
    print(json.dumps({"phase": "materialize_pinned_additive_overlay"}), flush=True)
    backend, sources, workspace_reused = materialize(lock)
    go, environment = native_environment()
    format_files = [str(backend / name) for name in sources if name.endswith(".go")]
    formatted = subprocess.run([str(go.with_name("gofmt.exe")), "-l", *format_files], capture_output=True, check=True, timeout=30)  # noqa: S603 -- constant formatter with project-local Go source files
    if formatted.stdout:
        raise RuntimeError("Maintained overlay Go sources require gofmt")
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if settings.environment != "development" or url.host != "127.0.0.1" or url.port != 55438 or url.database != "render_control":
        raise RuntimeError("Ledger tests are restricted to this project's native loopback PG development instance")
    options = {"host": url.host, "port": str(url.port), "user": url.username, "password": url.password, "connect_timeout": "5", **url.query}
    # lib/pq defaults to TLS=require, unlike psycopg's prefer. Only this already
    # guarded loopback development instance uses an explicit non-TLS connection.
    options["sslmode"] = "disable"
    database = "render_sub2api_test_" + uuid4().hex[:12]
    if not database.startswith("render_sub2api_test_") or database in ("render_control", "render_control_test", "postgres"):
        raise RuntimeError("Unsafe disposable database target")
    options["dbname"] = database
    environment["RC_SUB2API_TEST_DSN"] = psycopg.conninfo.make_conninfo(**{k: v for k, v in options.items() if v is not None})
    environment["PGPASSWORD"] = url.password or ""
    tools = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
    backup = ROOT / ".local/backups" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-sub2api-empty.dump")
    backup.parent.mkdir(parents=True, exist_ok=True)
    log = ROOT / ".local/sub2api-ledger-tests.jsonl"
    created, cleaned = False, False
    result, timed_out = 2, False
    binary_build: dict = {}
    fingerprints = {name: sha256(backend / name) for name in ("go.mod", "go.sum")}
    with psycopg.connect(**{**options, "dbname": "postgres"}, autocommit=True) as admin:
        if not admin.execute("SELECT pg_try_advisory_lock(634720182)").fetchone()[0]:
            raise RuntimeError("Another project migration or rehearsal is running")
        try:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            created = True
            safe_dsn = psycopg.conninfo.make_conninfo(**{k: v for k, v in options.items() if k != "password" and v is not None})
            subprocess.run([str(tools / "pg_dump.exe"), "--format=custom", "--file=" + str(backup), "--dbname=" + safe_dsn, "--no-password"], env=environment, capture_output=True, check=True, timeout=60)  # noqa: S603 -- backup of our freshly created empty database before any DDL
            subprocess.run([str(tools / "pg_restore.exe"), "--list", str(backup)], env=environment, capture_output=True, check=True, timeout=30)  # noqa: S603 -- validate project-local backup archive, no restore mutation
            print(json.dumps({"phase": "native_ledger_tests", "database": "fresh disposable native PG", "backup_verified": True, "network_module_downloads": False}), flush=True)
            result, timed_out, binary_build = execute_tests(backend, go, environment, log, lock, sources)
        finally:
            if created:
                # Only the exact database created by this invocation is dropped.
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                cleaned = True
            admin.execute("SELECT pg_advisory_unlock(634720182)")
    tests: dict[str, dict] = {}
    schema = ""
    package_started = False
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("Action") == "start" and entry.get("Package", "").endswith("/internal/rendercontrol"):
            package_started = True
        if "native upstream schema:" in entry.get("Output", ""):
            schema = entry["Output"].strip()
        if entry.get("Test") and entry.get("Action") in ("pass", "fail", "skip"):
            tests[entry["Test"]] = {"result": entry["Action"], "seconds": entry.get("Elapsed", 0)}
    unchanged = all(sha256(backend / name) == value for name, value in fingerprints.items())
    snapshot_unchanged = all(sha256(backend / name) == value for name, value in sources.items())
    overlay = ROOT / "integrations/sub2api/overlay/backend"
    current_sources = {path.relative_to(overlay).as_posix(): sha256(path) for path in overlay.rglob("*") if path.is_file()}
    current_sources.update({"internal/rendercontrol/controlclient/" + path.name: sha256(path) for path in (ROOT / "integrations/sub2api/controlclient").glob("*.go") if not path.name.endswith("_test.go")})
    patches = json.loads((overlay / "render_control_patches.json").read_text(encoding="utf-8"))["patches"]
    current_sources.update({item["path"]: item["patched_sha256"] for item in patches})
    maintained_matches = current_sources == sources and sha256(Path(__file__)) == harness_digest
    passed = result == 0 and not timed_out and unchanged and snapshot_unchanged and maintained_matches and bool(tests) and all(item["result"] != "fail" for item in tests.values())
    report = {"at": datetime.now(UTC).isoformat(), "source_commit": lock["commit"], "source_archive_sha256": lock["archive_sha256"], "harness_sha256": harness_digest, "compiler": "go version go1.27.3 windows/amd64", "command": "go test -json -trimpath -vet=off -mod=readonly -count=1 -p=2 -timeout=240s ./internal/rendercontrol", "duration_seconds": round(time.monotonic() - started, 2), "passed": passed, "exit_code": result, "timed_out": timed_out, "production_repository_compiled": package_started, "upstream_tests_run": False, "vet_enabled": False, "gofmt_verified": True, "schema": schema, "migrations_replayed": bool(schema), "backup_sha256": sha256(backup), "backup_list_verified": True, "temporary_database_dropped": cleaned, "module_files_unchanged": unchanged, "overlay_snapshot_unchanged": snapshot_unchanged, "maintained_sources_match": maintained_matches, "module_downloads_enabled": False, "virtualization_used": False, "overlay_files_sha256": sources, "top_level_tests_passed": sum(item["result"] == "pass" and "/" not in name for name, item in tests.items()), "subtests_passed": sum(item["result"] == "pass" and "/" in name for name, item in tests.items()), "tests": tests, "provider_implemented": False, "runtime_integration_verified": False, "coverage_boundary": "Additive ledger, leased recovery/cancellation coordinator and native task HTTP ingress against actual upstream auth/service/repository and schema, native PG, HTTPS fixtures and native subprocess crash boundaries; no stock route/hosting integration, compatibility image/video endpoints, full control-plane runtime, usage/cache consumer, subscription or media delivery integration"}
    report.update({"overlay_workspace_reused": workspace_reused, "native_loopback_pg_tls": False, "public_request_contract_schema_sha256": sha256(ROOT.parent / "docs/contracts/job-create.schema.json"), "public_request_fixtures_match_frozen_contract": True})
    report["coverage_boundary"] = "Ledger, recovery, hosted lifecycle, image/video/reference inputs, asset/content access, discovery/collections, durable native usage/cache effects, exact media/group/user multipliers and native subscription reservations/settlement against pinned upstream authentication/schema/repositories, native PostgreSQL, HTTPS protocol fixtures and an in-process Redis protocol service; native crash/restart tests retained. Stock cmd/server compiled separately. Full cross-service runtime, mixed stock-provider inflight guarantees, real Redis, real media/storage/hardware remain unverified"
    report["reviewed_stock_patches"] = [{key: item[key] for key in ("path", "stock_sha256", "patched_sha256")} for item in patches]
    report.update({"command": "verified native ledger binary via test2json -test.count=1 -test.timeout=240s", "build_command": "go test -c -trimpath -vet=off -mod=readonly -p=2 -o <app-local binary> ./internal/rendercontrol", "binary_build": binary_build, "production_repository_compiled": binary_build.get("compiled", False) and package_started})
    evidence = ROOT.parent / "docs/evidence" / datetime.now().strftime("%Y-%m-%d")
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "sub2api-ledger-tests.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (ROOT / ".local/sub2api-overlay-workspace.json").write_text(json.dumps({"backend": str(backend), "passed": passed, "overlay_files_sha256": sources}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "test_results": {result: sum(item["result"] == result for item in tests.values()) for result in ("pass", "fail", "skip")}, "duration_seconds": report["duration_seconds"], "temporary_database_dropped": cleaned}), flush=True)
    if not passed:
        # The test database and fixtures contain no real customers or credentials.
        # Never print the inherited environment, DSN or subprocess command args.
        print(log.read_text(encoding="utf-8", errors="replace")[-18000:])
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except (psycopg.Error, subprocess.SubprocessError):
        # Do not expose connection details or subprocess DSN arguments through
        # an exception traceback. Test output is separately bounded above.
        raise SystemExit("Native ledger verification failed during a database or subprocess operation") from None
