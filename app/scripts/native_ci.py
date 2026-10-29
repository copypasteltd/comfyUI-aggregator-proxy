"""Serial native CI; raw child output stays private, only fixed facts are public."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import msvcrt
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
MEDIA_NAMES = {"bin/ffmpeg.exe", "bin/ffprobe.exe", "LICENSE", "README.txt"}
PROJECTS = {"generation-domain", "generation-application", "generation-contracts", "generation-infrastructure", "render-control-api", "render-control-runner", "render-windows-worker"}
# This session lock must never share a key with dispatch or migration work.
CI_DATABASE_LOCK = 634720199


class CIError(ValueError):
    """Only fixed error codes may cross the public report boundary."""


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_bytes((json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode())
    temporary.replace(path)


def run_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", value):
        raise CIError("invalid_run_id")
    return value


def inside(path: Path, parent: Path) -> Path:
    if parent.is_symlink() or parent.is_junction() or path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(parent.resolve()) or path.resolve() == parent.resolve():
        raise CIError("unsafe_ci_path")
    for part in [path, *path.parents]:
        if part == parent:
            break
        if part.is_symlink() or part.is_junction():
            raise CIError("unsafe_ci_path")
    return path.resolve()


def media(source: Path | None = None) -> int:
    target = inside(APP / ".local/toolchains/ffmpeg-8.1", APP / ".local")
    lock = json.loads((APP / "config/media-tools.lock.json").read_bytes())
    if set(lock["files"]) != MEDIA_NAMES:
        raise CIError("invalid_media_lock")
    origin = source.resolve() if source else target
    for name, entry in lock["files"].items():
        item = inside(origin / name, origin)
        if not item.is_file() or item.stat().st_size != entry["bytes"] or sha(item) != entry["sha256"]:
            raise CIError("media_source_mismatch")
    for name, entry in lock["files"].items():
        destination = inside(target / name, target)
        if destination.exists():
            if destination.stat().st_size != entry["bytes"] or sha(destination) != entry["sha256"]:
                raise CIError("existing_media_mismatch")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin / name, destination)
        if sha(destination) != entry["sha256"]:
            raise CIError("media_copy_mismatch")
    return len(MEDIA_NAMES)


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise CIError("ci_checkout_busy") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def test_url():
    from generation_infrastructure.settings import Settings
    from sqlalchemy.engine import make_url

    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if settings.environment not in ("development", "test") or url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1" or url.port != 55438 or url.database not in ("render_control", "render_control_test"):
        raise CIError("unsafe_ci_database")
    return url.set(database="render_control_test")


@contextmanager
def database_lock():
    import psycopg

    url = test_url()
    # Exact loopback-only native test database: avoid Windows SSPI/GSS discovery.
    with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname="render_control_test", connect_timeout=5, sslmode="disable", gssencmode="disable", autocommit=True) as conn:
        identity = conn.execute("SELECT current_database(),host(inet_server_addr()),current_setting('port')").fetchone()
        if identity != ("render_control_test", "127.0.0.1", "55438") or not 180000 <= conn.info.server_version < 190000:
            raise CIError("unsafe_ci_database")
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (CI_DATABASE_LOCK,)).fetchone()[0]:
            raise CIError("ci_database_busy")
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (CI_DATABASE_LOCK,))


def migrate_test() -> None:
    import runpy

    url = test_url()
    os.environ["GEN_DATABASE_URL"] = url.render_as_string(hide_password=False)
    sys.argv = [str(APP / "scripts/migrate.py")]
    runpy.run_path(sys.argv[0], run_name="__main__")


class ProcessTree:
    """A Windows kernel job owns descendants even if the launcher exits."""

    def __init__(self) -> None:
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", w.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise CIError("process_tree_setup_failed")

    def assign(self, process: subprocess.Popen) -> None:
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise CIError("process_tree_assignment_failed")

    def close(self) -> None:
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise CIError("process_tree_cleanup_failed")
            self.handle = None


@dataclass(frozen=True)
class Gate:
    name: str
    arguments: list[str]
    cwd: Path
    timeout: float


def execute(gate: Gate, log: Path, environment: dict[str, str], *, limit: int) -> dict:
    """Drain bounded output privately; no child output or exception repr escapes."""
    started = time.monotonic()
    result = {"name": gate.name, "status": "failed", "exit_code": None, "timed_out": False, "log_truncated": False, "owned_process_tree_closed": False, "log_complete": False}
    process = None
    output = None
    tree = ProcessTree()
    overflow = threading.Event()
    read_failed = threading.Event()
    reader = None
    tree_closed = False
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        output = log.open("xb")
        process = subprocess.Popen(gate.arguments, cwd=gate.cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed gate plan, never shell input
        try:
            tree.assign(process)
        except CIError:
            process.kill()
            process.wait(timeout=10)
            raise

        def drain() -> None:
            retained = 0
            try:
                while block := process.stdout.read1(65536):
                    keep = min(len(block), max(0, limit - retained))
                    output.write(block[:keep])
                    output.flush()
                    retained += keep
                    if keep < len(block):
                        overflow.set()
            except (OSError, ValueError):
                read_failed.set()
            finally:
                # Only this thread closes streams after it starts. The parent
                # must not close a file while a late pipe read can still write.
                for stream in (output, process.stdout):
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        read_failed.set()

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        while process.poll() is None:
            if overflow.is_set() or read_failed.is_set() or time.monotonic() - started > gate.timeout:
                result["timed_out"] = not overflow.is_set() and not read_failed.is_set()
                result["log_truncated"] = overflow.is_set()
                tree.close()
                tree_closed = True
                process.wait(timeout=10)
                break
            time.sleep(0.05)
        tree.close()
        tree_closed = True
        reader.join(timeout=10)
        if reader.is_alive() or read_failed.is_set():
            raise CIError("private_log_drain_failed")
        result["exit_code"] = process.returncode
        result["log_truncated"] = overflow.is_set()
        if process.returncode == 0 and not result["timed_out"] and not overflow.is_set():
            result["status"] = "passed"
    except (OSError, CIError, subprocess.TimeoutExpired, RuntimeError):
        result["failure_code"] = "gate_process_failed"
    finally:
        try:
            tree.close()
            tree_closed = True
        except CIError:
            result["status"] = "failed"
            result["failure_code"] = "gate_process_failed"
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if reader is not None and reader.ident is not None:
            reader.join(timeout=10)
        else:
            for stream in (output, process.stdout if process is not None else None):
                if stream is not None:
                    stream.close()
        reader_closed = reader is None or reader.ident is None or not reader.is_alive()
        result["log_complete"] = reader_closed and not read_failed.is_set() and log.is_file()
        result["owned_process_tree_closed"] = tree_closed and reader_closed and (process is None or process.poll() is not None)
        if not result["log_complete"] or not result["owned_process_tree_closed"]:
            result["status"] = "failed"
        result["seconds"] = round(time.monotonic() - started, 3)
        result["log_bytes"] = log.stat().st_size if result["log_complete"] else None
        result["log_sha256"] = sha(log) if result["log_complete"] else None
    return result


def snapshot() -> dict[str, str]:
    paths = git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    return {name: sha(ROOT / name) for name in sorted(set(paths)) if name.startswith(("app/", ".github/")) and (ROOT / name).is_file()}


def git(arguments: list[str]) -> bytes:
    executable = shutil.which("git.exe")
    if not executable:
        raise CIError("native_git_required")
    return subprocess.check_output([executable, *arguments], cwd=ROOT, timeout=30)  # noqa: S603 -- fixed internal Git queries, never shell input


def plan(private: Path, policy: dict) -> list[Gate]:
    python = str(APP / ".venv/Scripts/python.exe")
    node_dir = APP / ".local/toolchains/node-24.22.1/node-v24.22.1-win-x64"
    node = str(node_dir / "node.exe")
    frontend = APP / "apps/admin_web"
    npm = str(node_dir / "node_modules/npm/bin/npm-cli.js")
    uv = str(APP / ".venv/Scripts/uv.exe")
    commands = [
        ("docs", [python, "-X", "utf8", "../docs/tools/verify_docs.py"], APP),
        ("lock", [uv, "lock", "--check"], APP),
        ("ruff", [str(APP / ".venv/Scripts/ruff.exe"), "check", "packages", "apps", "scripts", "tests", "migrations"], APP),
        ("mypy", [str(APP / ".venv/Scripts/mypy.exe"), "packages", "apps"], APP),
        ("contracts", [python, "scripts/sync-contracts.py", "--check"], APP),
        ("openapi", [python, "scripts/export-openapi.py", "--check"], APP),
        ("types_generate", [node, "node_modules/openapi-typescript/bin/cli.js", "../../config/openapi.json", "-o", str(private / "api.d.ts")], frontend),
        ("types_compare", [python, str(APP / "scripts/native_ci.py"), "--check-types", str(private / "api.d.ts")], APP),
        ("frontend_units", [node, "--test", "tests/workflowJson.test.ts", "tests/gatewayCatalog.test.ts"], frontend),
        ("frontend_build", [node, npm, "run", "build"], frontend),
        ("database_migrate", [python, "scripts/native_ci.py", "--migrate-test"], APP),
        ("python", [python, "-m", "pytest", "-q", "tests", "--tb=short", "-o", "faulthandler_timeout=120", "--basetemp=" + str(private / "pytest"), "--junitxml=" + str(private / "python.xml")], APP),
        ("wheels_build", [uv, "build", "--all-packages", "--wheel", "--no-build-isolation", "--offline", "--out-dir", str(private / "wheels")], APP),
        ("wheels_verify", [python, str(APP / "scripts/native_ci.py"), "--check-wheels", str(private / "wheels")], APP),
        ("readme", [python, "scripts/check-readme.py"], APP),
        ("publication", [python, "scripts/audit-repository.py"], APP),
    ]
    if set(policy["timeouts_seconds"]) != {name for name, _, _ in commands} or any(type(v) is not int or not 1 <= v <= 1200 for v in policy["timeouts_seconds"].values()) or policy["log_limit_bytes"] != 64 * 1024**2:
        raise CIError("invalid_ci_policy")
    return [Gate(name, command, cwd, policy["timeouts_seconds"][name]) for name, command, cwd in commands]


def workflow_policy(policy: dict) -> None:
    import yaml

    value = yaml.safe_load((ROOT / ".github/workflows/native-ci.yml").read_bytes())
    events = value.get("on", value.get(True))
    job = value.get("jobs", {}).get("verify", {})
    steps = job.get("steps", [])
    expected_if = "github.repository == 'copypasteltd/comfyUI-aggregator-proxy' && github.ref == 'refs/heads/main' && vars.NATIVE_CI_ENABLED == 'true'"
    if (set(events) != {"push", "workflow_dispatch"} or events["push"] != {"branches": ["main"]}
            or value.get("permissions") != {"contents": "read"}
            or value.get("concurrency") != {"group": "render-control-native-ci", "cancel-in-progress": False}
            or set(value["jobs"]) != {"verify"} or job.get("if") != expected_if
            or job.get("runs-on") != ["self-hosted", "Windows", "X64", "render-control-native"]
            or "services" in job or "container" in job or "permissions" in job or "secrets" in job
            or job.get("timeout-minutes") != policy["job_timeout_minutes"] or len(steps) != 4):
        raise CIError("unsafe_workflow_policy")
    if (set(job) != {"if", "runs-on", "timeout-minutes", "env", "defaults", "steps"}
            or job["env"] != {"CI_RUN_ID": "gha-${{ github.run_id }}-${{ github.run_attempt }}", "CI_MEDIA_SOURCE": "${{ vars.NATIVE_CI_MEDIA_SOURCE }}"}
            or job["defaults"] != {"run": {"shell": "pwsh", "working-directory": "app"}}
            or any(set(step) != names for step, names in zip(steps, [{"name", "uses", "with"}, {"name", "run"}, {"name", "run"}, {"name", "uses", "if", "with"}], strict=True))):
        raise CIError("unsafe_workflow_policy")
    if (steps[0].get("uses") != "actions/checkout@" + policy["actions"]["checkout"]
            or steps[0].get("with") != {"clean": False, "persist-credentials": False, "fetch-depth": 1}
            or steps[1].get("run") != "./scripts/ci.ps1 -Action Bootstrap -RunId $env:CI_RUN_ID -MediaSource $env:CI_MEDIA_SOURCE"
            or steps[2].get("run") != "./scripts/ci.ps1 -Action Verify -RunId $env:CI_RUN_ID"
            or steps[3].get("uses") != "actions/upload-artifact@" + policy["actions"]["upload_artifact"]
            or steps[3].get("if") != "always()"
            or steps[3].get("with") != {"name": "native-ci-${{ github.run_id }}-${{ github.run_attempt }}", "path": "docs/evidence/native-ci/${{ env.CI_RUN_ID }}/*.json", "if-no-files-found": "error", "retention-days": 14, "include-hidden-files": False}):
        raise CIError("unsafe_workflow_policy")


def bootstrap(identifier: str, source: Path | None) -> int:
    private = inside(APP / ".local/native-ci-bootstrap" / identifier, APP / ".local")
    public = inside(ROOT / "docs/evidence/native-ci" / identifier / "bootstrap.json", ROOT / "docs/evidence")
    if private.exists() or public.exists():
        raise CIError("ci_run_already_exists")
    private.mkdir(parents=True)
    report = {"format_version": 1, "run_id": identifier, "passed": False, "gates": [], "raw_logs_uploaded": False, "database_initialized_or_stopped": False, "virtualization_used": False}
    atomic_json(public, report)
    try:
        with file_lock(APP / ".local/native-ci.lock"):
            shell = shutil.which("pwsh.exe")
            if not shell:
                raise CIError("native_powershell_required")
            environment = {**os.environ, "PYTHONUTF8": "1"}
            gate = Gate("bootstrap", [shell, "-NoProfile", "-File", str(APP / "scripts/bootstrap.ps1"), "-WithPostgresToolchain", "-WithBuildTools"], APP, 900)
            result = execute(gate, private / "bootstrap.log", environment, limit=64 * 1024**2)
            report["gates"].append(result)
            atomic_json(public, report)
            if result["status"] != "passed":
                raise CIError("locked_bootstrap_failed")
            gate = Gate("media_prepare", [str(APP / ".venv/Scripts/python.exe"), "-X", "utf8", str(APP / "scripts/native_ci.py"), "--prepare-media", str(source or APP / ".local/toolchains/ffmpeg-8.1")], APP, 180)
            result = execute(gate, private / "media.log", environment, limit=64 * 1024**2)
            report["gates"].append(result)
            if result["status"] != "passed":
                raise CIError("pinned_media_failed")
            report["passed"] = True
    except Exception as exc:
        report["failure_code"] = str(exc) if isinstance(exc, CIError) else "bootstrap_infrastructure_failed"
    finally:
        atomic_json(public, report)
    print(json.dumps({"phase": "bootstrap", "passed": report["passed"], "run_id": identifier}))
    return 0 if report["passed"] else 1


def junit(path: Path) -> dict[str, int]:
    from xml.etree import ElementTree

    if not path.is_file() or path.stat().st_size > 32 * 1024**2:
        raise CIError("missing_or_oversized_junit")
    try:
        raw = path.read_bytes().decode("utf-8-sig")
        if "\x00" in raw or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
            raise CIError("unsafe_junit_xml")
        suites = list(ElementTree.fromstring(raw).iter("testsuite"))  # noqa: S314 -- owned bounded UTF-8 report; DTD/entities rejected above
        counts = {key: sum(int(suite.get(key, "0")) for suite in suites) for key in ("tests", "errors", "failures", "skipped")}
    except (ElementTree.ParseError, ValueError) as exc:
        raise CIError("invalid_junit") from exc
    if not suites or counts["tests"] < 1 or any(counts[k] for k in ("errors", "failures", "skipped")):
        raise CIError("junit_gate_failed")
    return counts


def wheels(path: Path) -> int:
    found = set()
    for item in path.glob("*.whl"):
        with zipfile.ZipFile(item) as archive:
            names = [name for name in archive.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 1048576:
                raise CIError("invalid_application_wheel")
            value = BytesParser().parsebytes(archive.read(names[0]))
            name = re.sub(r"[-_.]+", "-", value.get("Name", "")).lower()
            if name in found or name not in PROJECTS or value.get("Version") != "0.1.0":
                raise CIError("unexpected_application_wheel")
            found.add(name)
    if found != PROJECTS:
        raise CIError("application_wheels_missing")
    return len(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true")
    group.add_argument("--bootstrap", action="store_true")
    group.add_argument("--migrate-test", action="store_true")
    group.add_argument("--check-types", type=Path)
    group.add_argument("--check-wheels", type=Path)
    group.add_argument("--prepare-media", type=Path)
    parser.add_argument("--run-id", default=uuid4().hex[:12], type=run_id)
    parser.add_argument("--media-source", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 13) or sys.maxsize <= 2**32:
        raise CIError("native_windows_python_required")
    if args.bootstrap:
        return bootstrap(args.run_id, args.media_source)
    if args.media_source:
        raise CIError("media_source_requires_bootstrap")
    if args.migrate_test:
        migrate_test()
        return 0
    if args.check_types:
        actual = inside(args.check_types, APP / ".local/native-ci")
        if actual.read_bytes() != (APP / "apps/admin_web/src/generated/api.d.ts").read_bytes():
            raise CIError("frontend_type_drift")
        print('{"passed":true,"generated_types_unchanged":true}')
        return 0
    if args.check_wheels:
        count = wheels(inside(args.check_wheels, APP / ".local/native-ci"))
        print(json.dumps({"passed": True, "application_wheels": count}))
        return 0
    if args.prepare_media:
        print(json.dumps({"passed": True, "media_files_verified": media(args.prepare_media)}))
        return 0
    private = inside(APP / ".local/native-ci" / args.run_id, APP / ".local")
    policy = json.loads((APP / "config/native-ci.json").read_bytes())
    gates = plan(private, policy)
    if args.plan:
        print(json.dumps({"gates": [{"name": g.name, "timeout_seconds": g.timeout} for g in gates], "virtualization_used": False}))
        return 0
    report_path = inside(ROOT / "docs/evidence/native-ci" / args.run_id / "summary.json", ROOT / "docs/evidence")
    if private.exists() or report_path.exists():
        raise CIError("ci_run_already_exists")
    private.mkdir(parents=True)
    report = {"format_version": 1, "at": datetime.now(UTC).isoformat(), "run_id": args.run_id, "passed": False, "state": "preflight", "gates": [{"name": gate.name, "status": "not_run"} for gate in gates], "raw_logs_uploaded": False, "private_logs_retained_locally": True, "virtualization_used": False, "real_resource_acceptance": False}
    atomic_json(report_path, report)
    try:
        report["preflight_phase"] = "workflow_policy"
        workflow_policy(policy)
        report["workflow_policy_verified"] = True
        with ExitStack() as resources:
            for phase, resource in (("execution_lock", lambda: file_lock(APP / ".local/native-ci.lock")), ("database_lock", database_lock)):
                report["preflight_phase"] = phase
                atomic_json(report_path, report)
                resources.enter_context(resource())
            report["preflight_phase"] = "git_revision"
            atomic_json(report_path, report)
            report["git_revision"] = git(["rev-parse", "HEAD"]).decode().strip()
            report["preflight_phase"] = "source_snapshot"
            atomic_json(report_path, report)
            report["source_sha256"] = snapshot()
            report["preflight_phase"] = "git_status"
            atomic_json(report_path, report)
            report["source_tree_dirty"] = bool(git(["status", "--porcelain", "--", "app", ".github"]))
            if os.environ.get("GITHUB_ACTIONS") == "true" and report["source_tree_dirty"]:
                raise CIError("github_checkout_source_dirty")
            report["preflight_phase"] = "media"
            atomic_json(report_path, report)
            report["media_files_verified"] = media()
            report["preflight_phase"] = "complete"
            environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "UV_CACHE_DIR": str(APP / ".local/uv-cache")}
            environment.pop("PYTHONPATH", None)
            environment["PATH"] = os.pathsep.join([str(APP / ".local/toolchains/ffmpeg-8.1/bin"), str(APP / ".local/toolchains/node-24.22.1/node-v24.22.1-win-x64"), environment.get("PATH", "")])
            for index, gate in enumerate(gates):
                report["state"] = "running"
                report["gates"][index] = {"name": gate.name, "status": "running"}
                atomic_json(report_path, report)
                print(json.dumps({"phase": gate.name, "state": "running"}), flush=True)
                result = execute(gate, private / (gate.name + ".log"), environment, limit=policy["log_limit_bytes"])
                report["gates"][index] = result
                atomic_json(report_path, report)
                print(json.dumps({"phase": gate.name, "state": result["status"], "seconds": result["seconds"]}), flush=True)
                if result["status"] != "passed":
                    raise CIError("required_gate_failed")
                if gate.name == "python":
                    report["python"] = junit(private / "python.xml")
            report["sources_unchanged"] = snapshot() == report["source_sha256"]
            if not report["sources_unchanged"]:
                raise CIError("sources_changed_during_ci")
            report["passed"] = True
            report["state"] = "passed"
    except Exception as exc:
        report["state"] = "failed"
        report["failure_code"] = str(exc) if isinstance(exc, CIError) else "ci_command_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "ci_io_failed" if isinstance(exc, OSError) else "ci_encoding_failed" if isinstance(exc, UnicodeError) else "ci_infrastructure_failed"
    finally:
        atomic_json(report_path, report)
    print(json.dumps({"passed": report["passed"], "run_id": args.run_id, "state": report["state"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CIError, OSError, ValueError):
        print('{"passed":false,"failure_code":"ci_preflight_failed"}')
        raise SystemExit(1) from None
