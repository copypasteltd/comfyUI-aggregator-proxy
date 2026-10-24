from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from conftest import capacity_sample
from generation_contracts.capacity import WorkerCapacity
from generation_domain.errors import DomainError
from windows_worker.capacity import available_memory, observe_capacity, spool_size
from windows_worker.configuration import WorkerSettings
from windows_worker.journal import Journal


def settings(root: Path, **values: Any) -> WorkerSettings:
    return WorkerSettings(control_origin="https://127.0.0.1", credential_file=root / "unused.json", state_root=root, gpus=[], channels=[], **values)


@pytest.mark.parametrize(("changes", "reason"), [({"free_disk_bytes": 0}, "disk_space"), ({"available_memory_bytes": 0}, "memory"), ({"spool_bytes": 20 * 1024**3}, "spool_limit"), ({"pending_uploads": 2}, "upload_backlog"), ({"spool_bytes": None}, "observation_unavailable")])
def test_capacity_stops_at_actual_pressure(changes: dict[str, Any], reason: str) -> None:
    value = WorkerCapacity.model_validate(capacity_sample(**changes))
    assert reason in value.reasons and value.status != "ready"


def test_exact_budget_reservations_and_recovery() -> None:
    value = WorkerCapacity.model_validate(capacity_sample(free_disk_bytes=13 * 1024**3, reserved_bytes=2 * 1024**3))
    assert value.status == "ready"  # 5 GiB + two copies of (2 + 2 GiB).
    assert value.derived_reasons(3 * 1024**3) == ["disk_space"]
    value = WorkerCapacity.model_validate(capacity_sample(free_disk_bytes=9 * 1024**3, reserved_bytes=2 * 1024**3))
    assert value.status == "blocked" and value.derived_reasons(0) == []
    assert WorkerCapacity.model_validate(capacity_sample()).status == "ready"


@pytest.mark.parametrize("changes", [{"status": "ready", "free_disk_bytes": 0}, {"reasons": ["memory"]}, {"sequence": True}, {"reserved_bytes": -1}, {"spool_bytes": 1.2}, {"protocol": "unknown"}])
def test_capacity_rejects_forged_status_and_non_strict_metrics(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        WorkerCapacity.model_validate({**capacity_sample(), **changes})


def test_native_windows_probes_and_partial_files_count(tmp_path: Path) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/partial").write_bytes(b"x" * 5)
    (tmp_path / "outputs/unknown").mkdir(parents=True)
    (tmp_path / "outputs/unknown/unregistered.bin").write_bytes(b"x" * 7)
    assert available_memory() > 0
    assert spool_size(tmp_path) == 12
    value = observe_capacity(settings(tmp_path), 1, 0, 0)
    assert value.free_disk_bytes and value.available_memory_bytes and value.spool_bytes == 12


def test_scan_limits_fail_closed_and_do_not_delete(tmp_path: Path) -> None:
    (tmp_path / "outputs").mkdir()
    file = tmp_path / "outputs/unknown"
    file.write_bytes(b"keep")
    for options in ({"maximum_entries": 0}, {"maximum_depth": -1}, {"seconds": -1}):
        with pytest.raises(OSError):
            spool_size(tmp_path, **options)
    assert file.read_bytes() == b"keep"


def test_capacity_probes_comfy_data_volumes_and_hides_os_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from windows_worker import capacity
    for name in ("input", "output", "temp"):
        (tmp_path / name).mkdir()
    values = settings(tmp_path).model_copy(update={"channels": [SimpleNamespace(comfy_root=tmp_path)]})
    visited: set[Path] = set()
    def usage(path: Path) -> Any:
        visited.add(path)
        return SimpleNamespace(free=0 if path.name == "output" else 100 * 1024**3)
    monkeypatch.setattr(capacity.shutil, "disk_usage", usage)
    sample = observe_capacity(values, 1, 0, 0)
    assert visited == {tmp_path, tmp_path / "input", tmp_path / "output", tmp_path / "temp"}
    assert sample.status == "blocked" and "disk_space" in sample.reasons
    def failure() -> int:
        raise OSError("C:/private/do-not-expose")
    monkeypatch.setattr(capacity, "available_memory", failure)
    sample = observe_capacity(values, 2, 0, 0)
    assert sample.status == "unavailable" and "private" not in sample.model_dump_json()


def package(journal: Journal, root: Path) -> tuple[str, Path]:
    aid = "att_" + uuid4().hex
    file = root / "outputs" / aid / "0.png"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"synthetic-output")
    journal.accept_claim({"attempt_id": aid, "execution_digest": "a" * 64, "workflow": {"output": {"max_bytes": 100, "expected_count": 3}}})
    journal.save_outputs(aid, {"files": [file.relative_to(root).as_posix()], "specs": []})
    return aid, file


def test_retention_survives_restart_duplicate_confirm_and_clock_rollback(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    aid, file = package(journal, tmp_path)
    try:
        assert journal.cleanup_outputs(now=999999) == 0 and file.exists()
        journal.finish(aid, retention_seconds=7200, now=10000)
        journal.close()
        journal = Journal(tmp_path)
        journal.finish(aid, now=20000)
        assert journal.outputs(aid)["retain_until"] == 17200
        assert journal.cleanup_outputs(now=9999) == journal.cleanup_outputs(now=17199) == 0
        assert file.exists() and spool_size(tmp_path) == len(b"synthetic-output")
        assert journal.cleanup_outputs(now=17200) == 1 and not file.exists()
        assert journal.cleanup_outputs(now=18000) == 0
        with pytest.raises(DomainError):
            journal.save_outputs(aid, {})
    finally:
        journal.close()


@pytest.mark.parametrize("bad", ["outputs/elsewhere/0.png", "../outside.png", "outputs/{aid}/../0.png", "outputs/{aid}/non-numeric.png"])
def test_unsafe_package_cannot_authorize_any_deletion(tmp_path: Path, bad: str) -> None:
    journal = Journal(tmp_path)
    try:
        aid, file = package(journal, tmp_path)
        journal.save_outputs(aid, {"files": [file.relative_to(tmp_path).as_posix(), bad.format(aid=aid)]})
        journal.finish(aid, now=10000)
        assert journal.cleanup_outputs(now=20000) == 0 and file.exists()
    finally:
        journal.close()


def test_legacy_ack_without_timestamp_is_never_guessed(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    try:
        aid, file = package(journal, tmp_path)
        with journal.db:
            journal.db.execute("UPDATE attempts SET cloud_ack=1,stage='closed' WHERE id=?", (aid,))
        journal.finish(aid, now=10000)
        assert "confirmed_at" not in journal.outputs(aid)
        assert journal.cleanup_outputs(now=999999) == 0 and file.exists()
    finally:
        journal.close()


def test_cleanup_retry_preserves_metadata_until_delete_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = Journal(tmp_path)
    try:
        aid, file = package(journal, tmp_path)
        journal.finish(aid, now=10000)
        original = Path.unlink
        def refuse(path: Path, **kwargs: Any) -> None:
            if path == file:
                raise PermissionError("synthetic sharing violation")
            original(path, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", refuse)
            assert journal.cleanup_outputs(now=20000) == 0 and file.exists()
            assert not journal.outputs(aid).get("retention_cleaned")
        assert journal.cleanup_outputs(now=20000) == 1
    finally:
        journal.close()


def test_bad_package_does_not_starve_later_expired_outputs(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    try:
        entries = sorted([package(journal, tmp_path) for _ in range(3)])
        bad_aid, bad_file = entries[0]
        journal.save_outputs(bad_aid, {"files": ["../outside.png"]})
        for aid, _ in entries:
            journal.finish(aid, now=10000)
        assert journal.cleanup_outputs(now=20000, limit=1) == 0
        assert journal.cleanup_outputs(now=20000, limit=1) == 1
        assert journal.cleanup_outputs(now=20000, limit=1) == 1
        assert bad_file.exists() and all(not file.exists() for _, file in entries[1:])
    finally:
        journal.close()


def test_native_junction_is_rejected_by_scan_and_cleanup(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "state")
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    junction: Path | None = None
    try:
        aid, file = package(journal, state)
        journal.finish(aid, now=10000)
        file.unlink()
        file.parent.rmdir()
        (outside / "0.png").write_bytes(b"never-delete")
        junction = file.parent
        subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)  # noqa: S603,S607
        with pytest.raises(OSError):
            spool_size(state)
        assert journal.cleanup_outputs(now=20000) == 0
        assert (outside / "0.png").read_bytes() == b"never-delete"
    finally:
        if junction is not None and junction.exists():
            junction.rmdir()  # Removes only the verified owned junction, never its target.
        journal.close()


def test_journal_reserves_all_outputs_and_counts_unacknowledged_uploads(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    try:
        journal.accept_claim({"attempt_id": "att_unknown", "execution_digest": "a" * 64})
        journal.accept_claim({"attempt_id": "att_known", "execution_digest": "b" * 64, "workflow": {"output": {"max_bytes": 100, "expected_count": 3}}})
        assert journal.capacity_usage(1000) == (1300, 0)
        aid, _ = package(journal, tmp_path)
        assert journal.capacity_usage(1000) == (1300, 1)
        journal.finish(aid, now=10000)
        assert journal.capacity_usage(1000) == (1300, 0)
        with pytest.raises(ValueError):
            journal.finish(aid, retention_seconds=3599)
        assert json.loads(journal.get("att_known")["claim"])["workflow"]["output"]["expected_count"] == 3
    finally:
        journal.close()
