from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflow_catalogs import (
    catalog_cursor,
    export_directory,
    parse_catalog_cursor,
)
from generation_infrastructure.commands import checked_catalog_receipt
from windows_worker.commands import CommandExecutor
from windows_worker.configuration import WorkerSettings
from windows_worker.journal import Journal
from windows_worker.runtime import Worker
from windows_worker.workflow_catalogs import list_exports
from windows_worker.workflow_exports import export_directory_handle, observe_export

GRAPH = {"1": {"class_type": "SyntheticOutput", "inputs": {"text": "synthetic-private-prompt"}}}


@pytest.mark.parametrize("value", ["../x", "a/..", "/absolute", "a//b", "a\\b", "C:/x", "nul", "LPT¹", "a:stream", "a. ", "a/" * 9, "x" * 513])
def test_catalog_directories_reject_unsafe_names(value: str) -> None:
    with pytest.raises(DomainError):
        export_directory(value)


def test_catalog_cursor_is_bounded_canonical_and_bound_to_directory() -> None:
    cursor = catalog_cursor("目录", "a" * 64, "末项.json")
    assert parse_catalog_cursor(cursor, "目录")["after"] == "末项.json"
    for value, directory in [(cursor, "other"), (cursor + "=", "目录"), ("?", ""), ("a" * 4097, "")]:
        with pytest.raises(DomainError) as invalid:
            parse_catalog_cursor(value, directory)
        assert invalid.value.code == "invalid_catalog_cursor"


def test_catalog_pages_are_ordered_complete_bounded_and_hide_source_content(tmp_path: Path) -> None:
    for i in range(23):
        (tmp_path / f"{i:02}.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    (tmp_path / "子目录").mkdir()
    (tmp_path / "ignored.txt").write_text("not exported", encoding="utf-8")
    first = list_exports(tmp_path, "", None)["catalog"]
    assert len(first["entries"]) == 20 and first["has_more"] and first["skipped_count"] == 1
    second = list_exports(tmp_path, "", first["next_cursor"])["catalog"]
    names = [x["name"] for x in first["entries"] + second["entries"]]
    assert len(names) == len(set(names)) == 24 and names == sorted(names) and not second["has_more"]
    assert "synthetic-private-prompt" not in json.dumps(first) and str(tmp_path) not in json.dumps(first)
    assert second["entries"][-1]["kind"] == "directory"
    assert list_exports(tmp_path, "子目录", None)["catalog"]["entries"] == []
    (tmp_path / "new.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    with pytest.raises(DomainError) as changed:
        list_exports(tmp_path, "", first["next_cursor"])
    assert changed.value.code == "workflow_catalog_changed"


def test_bad_ui_oversize_unknown_task_and_hardlink_files_are_visible_but_not_selectable(tmp_path: Path) -> None:
    documents = {"bad.json": b"not-json", "ui.json": b'{"nodes":[],"links":[]}', "large.json": b" " * 1048577, "unknown.json": json.dumps({"format_version": "1.0", "workflow_api": GRAPH, "task_type": "unknown"}).encode()}
    for name, raw in documents.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / "original.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    (tmp_path / "link.json").hardlink_to(tmp_path / "original.json")
    rows = {item["name"]: item for item in list_exports(tmp_path, "", None)["catalog"]["entries"]}
    assert rows["bad.json"]["error_code"] == "invalid_workflow_source"
    assert rows["unknown.json"]["error_code"] == "invalid_workflow_source"
    assert rows["ui.json"]["error_code"] == "ui_workflow_not_supported"
    assert rows["large.json"]["error_code"] == "workflow_source_too_large"
    assert rows["link.json"]["error_code"] == "workflow_export_not_allowed"
    assert all(row["status"] == "invalid" and row["source_digest"] is None for row in rows.values())


def test_directory_count_and_time_budget_fail_without_partial_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(2001):
        (tmp_path / f"{i}.txt").touch()
    with pytest.raises(DomainError) as large:
        list_exports(tmp_path, "", None)
    assert large.value.code == "workflow_catalog_too_large"
    tick = iter([0, 16])
    monkeypatch.setattr("windows_worker.workflow_catalogs.time.monotonic", lambda: next(tick))
    with pytest.raises(DomainError) as timed:
        list_exports(tmp_path, "", None)
    assert timed.value.code == "workflow_catalog_timeout"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows handle and junction checks")
def test_windows_catalog_pins_ancestors_rejects_junctions_and_closes_handles(tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    root, outside = tmp_path / "exports", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    child = root / "child"
    child.mkdir()
    junction = root / "redirect"
    importlib.import_module("_winapi").CreateJunction(str(outside), str(junction))
    try:
        page = list_exports(root, "", None)["catalog"]
        assert page["skipped_count"] == 1 and [x["name"] for x in page["entries"]] == ["child"]
        with pytest.raises(DomainError):
            list_exports(root, "redirect", None)
        with pytest.raises(RuntimeError), export_directory_handle(root, "child"):
            writer = create(str(child), 0x40000000, 7, None, 3, 0x02000000, None)
            if writer not in (None, ctypes.c_void_p(-1).value):
                kernel.CloseHandle(writer)
                pytest.fail("Directory write access must be blocked while enumerating")
            assert ctypes.get_last_error() == 32  # ERROR_SHARING_VIOLATION
            with pytest.raises(OSError):
                root.rename(tmp_path / "moved")
            with pytest.raises(OSError):
                child.rename(root / "renamed")
            raise RuntimeError("synthetic cleanup path")
        writer = create(str(child), 0x40000000, 7, None, 3, 0x02000000, None)
        assert writer not in (None, ctypes.c_void_p(-1).value)
        kernel.CloseHandle(writer)
        child.rename(root / "renamed")
        root.rename(tmp_path / "moved")
        (tmp_path / "moved").rename(root)
    finally:
        junction.rmdir()


def test_catalog_receipt_rejects_tampering_extra_data_order_and_input_cursor(tmp_path: Path) -> None:
    (tmp_path / "sample.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    result = {"success": True, "applied_revision": 1, **list_exports(tmp_path, "", None)}
    payload: dict[str, Any] = {"relative_directory": "", "cursor": None}
    checked_catalog_receipt(result, payload)
    for changed in ({"catalog_digest": "0" * 64}, {"applied_revision": True}, {"success": 1}, {"authorization": "forbidden"}, {"extra": "x" * 65536}):
        with pytest.raises(DomainError) as invalid:
            checked_catalog_receipt({**result, **changed}, payload)
        assert invalid.value.code == "invalid_workflow_catalog_receipt"
    for mutate in (lambda p: p.update(relative_directory="outside"), lambda p: p["entries"].append(p["entries"][0]), lambda p: p["entries"][0].update(relative_path="../sample.json")):
        forged = json.loads(json.dumps(result))
        mutate(forged["catalog"])
        forged["catalog_digest"] = fingerprint(forged["catalog"])
        with pytest.raises(DomainError):
            checked_catalog_receipt(forged, payload)
    with pytest.raises(DomainError):
        checked_catalog_receipt(result, {**payload, "cursor": catalog_cursor("", result["catalog"]["directory_digest"], "sample.json")})


def test_worker_catalog_journal_replays_snapshot_and_selected_digest_is_rechecked(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    file = root / "sample.json"
    file.write_text(json.dumps(GRAPH), encoding="utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=tmp_path / "credential", state_root=tmp_path / "state", gpus=[{"hardware_id": "synthetic", "name": "Synthetic"}], channels=[{"base_url": "http://127.0.0.1:8188", "hardware_id": "synthetic", "comfy_root": tmp_path, "workflow_export_root": root}])
    executor = CommandExecutor(tmp_path / "state", settings)
    payload: dict[str, Any] = {"base_url": "http://127.0.0.1:8188", "revision": 1, "mode": "catalog", "relative_directory": "", "cursor": None}
    command = {"type": "pull_workflow", "payload": payload, "command_digest": fingerprint({"type": "pull_workflow", "payload": payload})}
    journal = Journal(tmp_path / "journal")
    try:
        assert journal.command_intent("catalog", command["command_digest"]) is None
        result = executor.execute(command)
        journal.command_result("catalog", result)
    finally:
        journal.close()
    file.write_text(json.dumps({"2": {"class_type": "Changed", "inputs": {}}}), encoding="utf-8")
    journal = Journal(tmp_path / "journal")
    try:
        assert journal.command_intent("catalog", command["command_digest"]) == result
    finally:
        journal.close()
    pull = {"base_url": payload["base_url"], "revision": 1, "relative_path": "sample.json", "expected_source_digest": fingerprint(GRAPH)}
    with pytest.raises(DomainError) as changed:
        executor.execute({"type": "pull_workflow", "payload": pull, "command_digest": fingerprint({"type": "pull_workflow", "payload": pull})})
    assert changed.value.code == "workflow_export_changed"
    assert observe_export(None)["status"] == "not_configured"
    assert observe_export(root)["status"] == "ready"
    assert observe_export(root / "missing")["status"] == "unavailable"


async def test_worker_observes_export_directory_even_when_comfy_queue_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    credential = tmp_path / "credential.json"
    credential.write_text(json.dumps({"node_id": "node_" + "1" * 32, "token": "worker_" + "1" * 32 + "." + "A" * 43}), encoding="utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=credential, state_root=tmp_path / "state", gpus=[{"hardware_id": "synthetic", "name": "Synthetic"}], channels=[{"base_url": "http://127.0.0.1:8188", "hardware_id": "synthetic", "comfy_root": tmp_path, "workflow_export_root": tmp_path}])
    worker = Worker(settings)
    channel_id = "ch_" + "2" * 32
    worker.observation_channels = [{"id": channel_id, "revision": 1, "base_url": "http://127.0.0.1:8188", "authorization": None}]
    entered = asyncio.Event()

    async def queue_unreachable(client: Any) -> dict[str, Any]:
        entered.set()
        return {"reachable": False, "error_code": "unreachable"}

    monkeypatch.setattr("windows_worker.runtime.observe_queue", queue_unreachable)
    task = asyncio.create_task(worker.observe())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.sleep(0)
        observation = worker.observations[channel_id][1]
        assert observation["workflow_export"] == {"protocol": "local-directory-v1", "status": "ready"}
        assert observation["observation"]["reachable"] is False
        assert str(tmp_path) not in json.dumps(observation)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.close()
