from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from ctypes import wintypes
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from generation_domain.errors import DomainError
from windows_worker.configuration import (
    WorkerSettings,
    canonical_origin,
    load_settings,
    read_credential,
)
from windows_worker.journal import Journal
from windows_worker.process_lock import NodeProcessLock
from windows_worker.runtime import Worker

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://control.invalid"


def credential(root: Path, *, node: str | None = None, token: str | None = None) -> Path:
    path = root / (uuid4().hex + ".json")
    path.write_text(json.dumps({"node_id": node or "node_" + uuid4().hex, "token": token or "worker_" + uuid4().hex + "." + "A" * 43}), encoding="utf-8")
    return path


def settings(root: Path, identity: Path, *, origin: str = ORIGIN) -> WorkerSettings:
    return WorkerSettings(control_origin=origin, credential_file=identity, state_root=root, gpus=[], channels=[])


def pending(journal: Journal) -> None:
    journal.accept_claim({"attempt_id": "att_synthetic", "execution_digest": "a" * 64})
    association = journal.intent("att_synthetic", "b" * 64)
    journal.begin_post("att_synthetic", association)


@pytest.mark.parametrize("origin,expected", [
    ("https://CONTROL.invalid:443/", ORIGIN),
    ("https://[0:0:0:0:0:0:0:1]:443/", "https://[::1]"),
    ("https://例子.test", "https://xn--fsqu00a.test"),
    ("https://control.invalid:8443", "https://control.invalid:8443"),
])
def test_control_origin_is_canonical_for_node_identity(origin: str, expected: str) -> None:
    assert canonical_origin(origin) == expected


@pytest.mark.parametrize("origin,expected", [
    ("http://CONTROL.invalid:80/", "http://control.invalid"),
    ("http://[0:0:0:0:0:0:0:1]:39444/", "http://[::1]:39444"),
    ("https://CONTROL.invalid:443/", ORIGIN),
])
def test_http_control_origin_requires_explicit_opt_in(origin: str, expected: str) -> None:
    if origin.startswith("http:"):
        with pytest.raises(ValueError):
            canonical_origin(origin)
    assert canonical_origin(origin, allow_http=True) == expected


@pytest.mark.parametrize("origin", ["http://user:secret@control.invalid", "http://control.invalid/path", "http://control.invalid?token=secret", "http://control.invalid#fragment", "ftp://control.invalid", "http://control.invalid:0", "http://control.invalid:65536", "http://control.invalid\n"])
def test_http_opt_in_preserves_origin_validation(origin: str) -> None:
    with pytest.raises(ValueError):
        canonical_origin(origin, allow_http=True)


@pytest.mark.asyncio
async def test_http_worker_preserves_node_lock_and_journal_identity(tmp_path: Path) -> None:
    identity = credential(tmp_path)
    values = dict(credential_file=identity, gpus=[], channels=[], allow_http=True)
    first = Worker(WorkerSettings(control_origin="http://CONTROL.invalid:80/", state_root=tmp_path / "first", **values))
    try:
        assert str(first.http.base_url) == "http://control.invalid"
        with pytest.raises(DomainError) as conflict:
            Worker(WorkerSettings(control_origin="http://control.invalid", state_root=tmp_path / "other", **values))
        assert conflict.value.code == "worker_node_in_use"
        assert not (tmp_path / "other").exists()
    finally:
        await first.close()
    with pytest.raises(DomainError) as mismatch:
        Worker(WorkerSettings(control_origin=ORIGIN, state_root=tmp_path / "first", **values))
    assert mismatch.value.code == "journal_identity_mismatch"
    with pytest.raises(ValueError):
        WorkerSettings(control_origin="http://control.invalid", state_root=tmp_path, credential_file=identity, gpus=[], channels=[])
    with pytest.raises(ValueError):
        WorkerSettings(control_origin=ORIGIN, state_root=tmp_path, credential_file=identity, gpus=[], channels=[], allow_http="true")


@pytest.mark.parametrize("variant", ["duplicate", "unknown", "wrong_realm", "node", "short", "newline", "bom", "oversize", "array", "missing", "constant", "file_directory"])
def test_bounded_credential_reader_never_exposes_secret(tmp_path: Path, variant: str) -> None:
    path = credential(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if variant == "bom":
        path.write_text(json.dumps(value), encoding="utf-8-sig")
        assert read_credential(path).node_id == value["node_id"]
        assert value["token"] not in repr(read_credential(path))
        return
    if variant == "duplicate":
        data = json.dumps(value)[:-1] + ',"token":"sensitive-duplicate"}'
    elif variant == "unknown":
        data = json.dumps({**value, "secret": "sensitive-unknown"})
    elif variant == "wrong_realm":
        data = json.dumps({**value, "token": value["token"].replace("worker_", "gateway_")})
    elif variant == "node":
        data = json.dumps({**value, "node_id": "node_wrong"})
    elif variant == "short":
        data = json.dumps({**value, "token": "worker_synthetic"})
    elif variant == "newline":
        data = json.dumps({**value, "token": value["token"] + "\n"})
    elif variant == "oversize":
        data = " " * 4097 + json.dumps(value)
    elif variant == "array":
        data = "[]"
    elif variant == "constant":
        data = '{"node_id":NaN,"token":"sensitive-constant"}'
    elif variant == "file_directory":
        path.unlink()
        path.mkdir()
        data = None
    else:
        path.unlink()
        data = None
    if data is not None:
        path.write_text(data, encoding="utf-8")
    with pytest.raises(DomainError) as failure:
        read_credential(path)
    assert failure.value.code == "invalid_worker_credential"
    assert "sensitive" not in str(failure.value) and value["token"] not in str(failure.value) and str(path) not in str(failure.value)


@pytest.mark.parametrize("content", ['{"control_origin":NaN}', '{"control_origin":"https://secret.invalid","control_origin":"duplicate"}', "[]", " " * (1024 * 1024 + 1)], ids=["nonfinite", "duplicate", "array", "oversize"])
def test_configuration_reader_is_bounded_and_redacted(tmp_path: Path, content: str) -> None:
    path = tmp_path / "worker.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(DomainError) as failure:
        load_settings(path)
    assert failure.value.code == "invalid_worker_configuration" and "secret.invalid" not in str(failure.value)


def test_preflight_does_not_create_state_or_contact_control(tmp_path: Path) -> None:
    value = settings(tmp_path / "state", credential(tmp_path))
    config = tmp_path / "worker.json"
    config.write_text(value.model_dump_json(), encoding="utf-8-sig")
    result = subprocess.run([sys.executable, "-I", "-m", "windows_worker.main", "--config", str(config), "--validate-config"], capture_output=True, timeout=15)  # noqa: S603 -- fixed interpreter/module and owned config, no shell
    assert result.returncode == 0 and not value.state_root.exists()
    assert json.loads(result.stdout) == {"configuration_valid": True, "processes_started": False, "server_identity_verified": False}
    value.credential_file.write_text('{"token":"sensitive-invalid-token"}', encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-m", "windows_worker.main", "--config", str(config), "--validate-config"], capture_output=True, timeout=15)  # noqa: S603 -- fixed interpreter/module and owned config, no shell
    assert result.returncode == 1 and json.loads(result.stderr) == {"error": "invalid_worker_credential"}
    assert b"sensitive" not in result.stderr and not value.state_root.exists()


@pytest.mark.asyncio
async def test_same_node_cannot_start_in_another_directory_or_with_rotated_secret(tmp_path: Path) -> None:
    identity = credential(tmp_path)
    other = credential(tmp_path, node=read_credential(identity).node_id, token="worker_" + uuid4().hex + "." + "B" * 43)
    first = Worker(settings(tmp_path / "first", identity))
    try:
        with pytest.raises(DomainError) as failure:
            Worker(settings(tmp_path / "second", other, origin="https://CONTROL.invalid:443/"))
        assert failure.value.code == "worker_node_in_use" and not (tmp_path / "second").exists()
        independent_identity = credential(tmp_path)
        with pytest.raises(DomainError) as directory_busy:
            Worker(settings(tmp_path / "first", independent_identity))
        assert directory_busy.value.code == "journal_in_use"
        independent = Worker(settings(tmp_path / "other", independent_identity))
        await independent.close()
    finally:
        await first.close()
    first = Worker(settings(tmp_path / "first", other))
    await first.close()
    await first.close()


def native_kernel() -> Any:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def test_native_namespace_conflict_fails_closed_and_does_not_poison_future_start(tmp_path: Path) -> None:
    value = settings(tmp_path / "state", credential(tmp_path))
    original = NodeProcessLock(value.control_origin, read_credential(value.credential_file).node_id)
    name = original.name
    original.close()
    kernel = native_kernel()
    handle = kernel.CreateEventW(None, False, False, name)
    assert handle
    try:
        with pytest.raises(DomainError) as rejected:
            Worker(value)
        assert rejected.value.code == "worker_node_lock_unavailable" and not value.state_root.exists()
    finally:
        kernel.CloseHandle(handle)
    worker = Worker(value)
    asyncio.run(worker.close())


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["node", "origin"])
async def test_journal_binding_cannot_be_overwritten_even_by_adoption(tmp_path: Path, change: str) -> None:
    identity = credential(tmp_path)
    first = Worker(settings(tmp_path / "state", identity))
    pending(first.journal)
    original = first.journal.recovery()
    await first.close()
    changed = settings(tmp_path / "state", credential(tmp_path) if change == "node" else identity, origin="https://another.invalid" if change == "origin" else ORIGIN)
    with pytest.raises(DomainError) as failure:
        Worker(changed, adopt_legacy_journal=True)
    assert failure.value.code == "journal_identity_mismatch"
    replacement = Worker(settings(tmp_path / "state", identity))
    try:
        assert replacement.quarantined and replacement.journal.recovery() == original
        assert not (tmp_path / "state/identity-backups").exists()
    finally:
        await replacement.close()


@pytest.mark.asyncio
async def test_legacy_adoption_requires_explicit_flag_and_verified_snapshot(tmp_path: Path) -> None:
    value = settings(tmp_path / "state", credential(tmp_path))
    journal = Journal(value.state_root)
    pending(journal)
    journal.command_intent("cmd_synthetic", "c" * 64)
    journal.claim_request("synthetic-old-boot")
    previous = journal.recovery()
    journal.close()
    with pytest.raises(DomainError) as failure:
        Worker(value)
    assert failure.value.code == "journal_identity_unbound"
    worker = Worker(value, adopt_legacy_journal=True)
    try:
        assert worker.quarantined and worker.journal.recovery() == previous
        backup = worker.journal.identity_backup
        assert backup is not None
        with sqlite3.connect(backup) as snapshot:
            assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert snapshot.execute("SELECT stage,cloud_ack FROM attempts").fetchone() == ("posting", 0)
            assert snapshot.execute("SELECT COUNT(*) FROM worker_identity").fetchone()[0] == 0
            assert snapshot.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == snapshot.execute("SELECT COUNT(*) FROM claim_requests").fetchone()[0] == 1
        with pytest.raises(DomainError) as rejected:
            worker.journal.intent("att_synthetic", "b" * 64)
        assert rejected.value.code == "resubmit_forbidden"
    finally:
        await worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["size", "time", "directory"])
async def test_failed_legacy_backup_never_binds_or_loses_unknown_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: str) -> None:
    from windows_worker import journal as journal_module
    value = settings(tmp_path / "state", credential(tmp_path))
    journal = Journal(value.state_root)
    pending(journal)
    original = journal.recovery()
    journal.close()
    if budget == "size":
        monkeypatch.setattr(journal_module, "IDENTITY_BACKUP_MAX_BYTES", 1)
    elif budget == "time":
        monkeypatch.setattr(journal_module, "IDENTITY_BACKUP_SECONDS", -1)
    else:
        (value.state_root / "identity-backups").write_bytes(b"synthetic-not-directory")
    with pytest.raises(DomainError) as failure:
        Worker(value, adopt_legacy_journal=True)
    assert failure.value.code == "journal_identity_backup_failed"
    journal = Journal(value.state_root)
    try:
        assert journal.recovery() == original and journal.db.execute("SELECT COUNT(*) FROM worker_identity").fetchone()[0] == 0
    finally:
        journal.close()
    lock = NodeProcessLock(value.control_origin, read_credential(value.credential_file).node_id)
    lock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ca", "database", "commands", "registration"])
async def test_startup_and_registration_failure_release_all_owned_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    value = settings(tmp_path / "state", credential(tmp_path))
    if failure == "ca":
        bad = tmp_path / "bad-ca.pem"
        bad.write_text("sensitive-private-path", encoding="utf-8")
        with pytest.raises(DomainError) as rejected:
            Worker(value.model_copy(update={"ca_file": bad}))
        assert rejected.value.code == "invalid_worker_trust" and not value.state_root.exists()
    elif failure == "database":
        value.state_root.mkdir()
        (value.state_root / "journal.sqlite3").write_bytes(b"synthetic-corrupt-database")
        with pytest.raises(sqlite3.DatabaseError):
            Worker(value)
        (value.state_root / "journal.sqlite3").unlink()
    elif failure == "commands":
        from windows_worker import runtime
        original = runtime.CommandExecutor
        def broken(*args: Any) -> Any:
            raise OSError("sensitive-constructor-error")
        monkeypatch.setattr(runtime, "CommandExecutor", broken)
        with pytest.raises(OSError):
            Worker(value)
        monkeypatch.setattr(runtime, "CommandExecutor", original)
    else:
        worker = Worker(value)
        await worker.http.aclose()
        worker.http = httpx.AsyncClient(base_url=ORIGIN, transport=httpx.MockTransport(lambda request: httpx.Response(401)))
        with pytest.raises(httpx.HTTPStatusError):
            await worker.run()
        assert worker.http.is_closed
    worker = Worker(value)
    await worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["node", "boot", "missing_node", "missing_boot"])
async def test_registration_identity_reply_must_match_before_background_or_claims(tmp_path: Path, invalid: str) -> None:
    value = settings(tmp_path / "state", credential(tmp_path))
    worker = Worker(value)
    assert worker.http.headers["X-Worker-Node-Id"] == worker.node_id
    await worker.http.aclose()
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        result = {"node_id": worker.node_id, "boot_id": worker.boot, "reconciliation_required": []}
        if invalid.startswith("missing_"):
            result.pop(invalid.removeprefix("missing_") + "_id")
        else:
            result[invalid + "_id"] = "node_" + uuid4().hex if invalid == "node" else str(uuid4())
        return httpx.Response(200, json=result)

    worker.http = httpx.AsyncClient(base_url=ORIGIN, transport=httpx.MockTransport(response))
    with pytest.raises(DomainError) as rejected:
        await worker.run()
    assert rejected.value.code == "worker_registration_identity_mismatch"
    assert len(requests) == 1 and requests[0].url.path == "/internal/v1/register" and worker.http.is_closed
    assert worker.journal._closed
    replacement = Worker(value)
    await replacement.close()


@pytest.mark.asyncio
async def test_cancelling_an_operation_or_shutdown_retains_locks_until_thread_really_stops(tmp_path: Path) -> None:
    import threading
    value = settings(tmp_path / "state", credential(tmp_path))
    entered, release = threading.Event(), threading.Event()
    worker = Worker(value)

    def operation() -> None:
        entered.set()
        assert release.wait(10)
        (value.state_root / "synthetic-completed.txt").write_text("done", encoding="utf-8")

    call = asyncio.create_task(worker.blocking(operation))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        closing = asyncio.create_task(worker.close())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(DomainError) as blocked:
            Worker(settings(tmp_path / "other-state", value.credential_file))
        assert blocked.value.code == "worker_node_in_use"
        with pytest.raises(DomainError):
            Journal(value.state_root)
        assert not (tmp_path / "other-state").exists()
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        else:
            await worker.close()
    assert (value.state_root / "synthetic-completed.txt").read_text(encoding="utf-8") == "done"
    replacement = Worker(value)
    await replacement.close()


HELPER = '''
import asyncio,json,sys,time
from pathlib import Path
from generation_domain.errors import DomainError
from windows_worker.configuration import load_settings
from windows_worker.runtime import Worker
try:
    worker=Worker(load_settings(Path(sys.argv[1])))
except DomainError as error:
    print(json.dumps({"error":error.code}),flush=True)
    raise SystemExit(3)
print(json.dumps({"ready":True,"quarantined":worker.quarantined,"abandoned_lock":worker.process_lock.abandoned}),flush=True)
try:
    sys.stdin.readline()
finally:
    asyncio.run(worker.close())
'''


@pytest.fixture
def process_factory(tmp_path: Path) -> Iterator[Any]:
    helper = tmp_path / "native-worker-holder.py"
    helper.write_text(HELPER, encoding="utf-8")
    paths = [ROOT / "apps/windows_worker/src", ROOT / "packages/domain/src", ROOT / "packages/contracts/src", ROOT / ".venv/Lib/site-packages"]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(str(p) for p in paths), "PYTHONNOUSERSITE": "1"}
    processes: list[subprocess.Popen[bytes]] = []

    def create(value: WorkerSettings) -> subprocess.Popen[bytes]:
        config = tmp_path / (uuid4().hex + "-worker.json")
        config.write_text(value.model_dump_json(), encoding="utf-8")
        # Use the actual base interpreter, avoiding the Windows venv redirector
        # so terminate/wait refer to the exact process owning the native mutex.
        process = subprocess.Popen([sys._base_executable, "-u", str(helper), str(config)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- actual native interpreter and owned test helper/config, no shell
        processes.append(process)
        return process

    yield create
    for process in processes:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def message(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    # Bounded read through a thread because Windows select() cannot wait on pipes.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        assert process.stdout is not None
        future = pool.submit(process.stdout.readline, 4096)
        try:
            line = future.result(timeout=15)
        except TimeoutError:
            process.terminate()
            process.wait(timeout=5)
            raise
    return json.loads(line)


def test_native_processes_cross_directory_exclusion_crash_and_unknown_journal(tmp_path: Path, process_factory: Any) -> None:
    identity = credential(tmp_path)
    original = settings(tmp_path / "state", identity)
    worker = Worker(original)
    pending(worker.journal)
    before = worker.journal.recovery()
    asyncio.run(worker.close())
    first = process_factory(original)
    assert message(first) == {"ready": True, "quarantined": True, "abandoned_lock": False}
    second_state = tmp_path / "other-state"
    second = process_factory(settings(second_state, identity, origin="https://CONTROL.invalid:443/"))
    assert message(second) == {"error": "worker_node_in_use"}
    assert second.wait(timeout=10) == 3 and not second_state.exists()
    # Retain a non-owning handle so the abandoned OS object survives the owner
    # crash; the replacement must observe WAIT_ABANDONED and still reconcile.
    kernel = native_kernel()
    observer = kernel.CreateMutexW(None, False, worker.process_lock.name)
    assert observer
    try:
        first.terminate()
        first.wait(timeout=10)
        replacement = process_factory(original)
        assert message(replacement) == {"ready": True, "quarantined": True, "abandoned_lock": True}
        assert replacement.stdin is not None
        replacement.stdin.write(b"stop\n")
        replacement.stdin.flush()
        assert replacement.wait(timeout=10) == 0
    finally:
        kernel.CloseHandle(observer)
    journal = Journal(original.state_root)
    try:
        assert journal.recovery() == before
        with pytest.raises(DomainError):
            journal.intent("att_synthetic", "b" * 64)
    finally:
        journal.close()


def test_native_simultaneous_start_has_one_owner_and_other_nodes_are_independent(tmp_path: Path, process_factory: Any) -> None:
    identity = credential(tmp_path)
    processes = [process_factory(settings(tmp_path / f"state-{index}", identity)) for index in range(6)]
    results = [message(process) for process in processes]
    assert sum(result.get("ready", False) for result in results) == 1
    assert sum(result.get("error") == "worker_node_in_use" for result in results) == 5
    independent = process_factory(settings(tmp_path / "independent", credential(tmp_path)))
    assert message(independent)["ready"]
    for process, result in zip(processes, results, strict=True):
        if result.get("error"):
            assert process.wait(timeout=10) == 3
    assert sum((tmp_path / f"state-{index}").exists() for index in range(6)) == 1
