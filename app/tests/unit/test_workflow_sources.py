from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from control_api.limits import body_limit
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflow_imports import (
    MAX_WORKFLOW_BYTES,
    bounded_document,
    checked_source_receipt,
    export_path,
    parse_source,
)
from windows_worker.commands import CommandExecutor
from windows_worker.configuration import WorkerSettings
from windows_worker.journal import Journal
from windows_worker.workflow_exports import configured_export_root, read_export

GRAPH = {"1": {"class_type": "SyntheticOutput", "inputs": {"text": "合成测试"}, "_meta": {"title": "preserve me"}}}


@pytest.mark.parametrize("path", ["../x.json", "/x.json", "C:/x.json", "a\\x.json", "a/../x.json", "a//x.json", "a/./x.json", "a:stream.json", "nul.json", "CON.txt/x.json", "com1/x.json", "lpt¹.json", "a./x.json", "a /x.json", "x\x00.json", "a*.json", "x.txt", "", "a" * 513 + ".json"])
def test_export_paths_reject_traversal_devices_and_ambiguous_names(path: str) -> None:
    with pytest.raises(DomainError):
        export_path(path)


@pytest.mark.parametrize("raw", [b'{"1":{},"1":{}}', b'{"1":{"class_type":"X","inputs":{"x":NaN}}}', b'{"1":{"class_type":"X","inputs":{"x":1e9999}}}', b'{"1":{"class_type":"X","inputs":{"x":1.1234567890123456789}}}', b'[]', b'{"nodes":[],"links":[]}', b'{}', b'\xff', b'{"1":{"class_type":"X"}}'])
def test_unsafe_or_lossy_json_is_rejected(raw: bytes) -> None:
    with pytest.raises(DomainError):
        parse_source(raw)


def test_json_complexity_and_canonical_expansion_are_bounded() -> None:
    value: dict[str, Any] = {}
    for _ in range(66):
        value = {"next": value}
    for document in (value, {"values": [0] * 100001}, {"values": [1e308] * 4000}, {"large": "x" * MAX_WORKFLOW_BYTES}):
        with pytest.raises(DomainError):
            bounded_document(document)
    with pytest.raises(DomainError):
        parse_source(b" " * (MAX_WORKFLOW_BYTES + 1))


def test_worker_exports_preserve_graph_and_journal_snapshot_across_restart(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    source = exports / "sample.json"
    source.write_text(json.dumps(GRAPH, ensure_ascii=False), encoding="utf-8")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=tmp_path / "credential", state_root=tmp_path / "state", gpus=[{"hardware_id": "synthetic", "name": "Synthetic"}], channels=[{"base_url": "http://127.0.0.1:8188", "hardware_id": "synthetic", "comfy_root": tmp_path, "workflow_export_root": exports}])
    payload = {"base_url": "http://127.0.0.1:8188", "revision": 1, "relative_path": "sample.json"}
    command = {"type": "pull_workflow", "payload": payload, "command_digest": fingerprint({"type": "pull_workflow", "payload": payload})}
    executor = CommandExecutor(tmp_path / "state", settings)
    journal = Journal(tmp_path / "journal")
    try:
        assert journal.command_intent("synthetic-pull", command["command_digest"]) is None
        result = executor.execute(command)
        assert result["source_document"] == GRAPH and result["source_digest"] == fingerprint(GRAPH)
        journal.command_result("synthetic-pull", result)
    finally:
        journal.close()
    source.write_text('{"2":{"class_type":"Changed","inputs":{}}}', encoding="utf-8")
    journal = Journal(tmp_path / "journal")
    try:
        assert journal.command_intent("synthetic-pull", command["command_digest"]) == result
    finally:
        journal.close()


def test_export_missing_root_oversize_and_open_handle_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(DomainError, match="尚未配置"):
        read_export(None, "sample.json")
    root = tmp_path / "exports"
    root.mkdir()
    source = root / "sample.json"
    source.write_bytes(b" " * (MAX_WORKFLOW_BYTES + 1))
    with pytest.raises(DomainError) as large:
        read_export(root, "sample.json")
    assert large.value.code == "workflow_source_too_large"
    source.write_text(json.dumps(GRAPH), encoding="utf-8")
    monkeypatch.setattr("windows_worker.workflow_exports._opened_path", lambda *_: tmp_path / "outside.json")
    with pytest.raises(DomainError) as escaped:
        read_export(root, "sample.json")
    assert escaped.value.code == "workflow_export_not_allowed"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction boundary")
def test_windows_junction_cannot_redirect_export_reads(tmp_path: Path) -> None:
    root, outside = tmp_path / "exports", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    junction = root / "redirect"
    importlib.import_module("_winapi").CreateJunction(str(outside), str(junction))
    try:
        assert junction.is_junction()
        with pytest.raises(DomainError):
            read_export(root, "redirect/secret.json")
        with pytest.raises(DomainError):
            configured_export_root(junction)
    finally:
        junction.rmdir()
    assert (outside / "secret.json").is_file()


def test_source_receipt_digest_and_schema_cannot_be_forged(tmp_path: Path) -> None:
    (tmp_path / "sample.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    result = {"success": True, "applied_revision": 1, **read_export(tmp_path, "sample.json")}
    assert checked_source_receipt(result) == GRAPH
    for changed in ({"source_digest": "0" * 64}, {"export_format": "manifest-v1"}, {"source_bytes": True}, {"applied_revision": True}, {"authorization": "forbidden"}):
        with pytest.raises(DomainError):
            checked_source_receipt({**result, **changed})


def test_hardlinks_are_rejected_and_composite_node_ids_are_preserved(tmp_path: Path) -> None:
    outside = tmp_path / "source.json"
    outside.write_text(json.dumps({"12:9": GRAPH["1"]}), encoding="utf-8")
    assert read_export(tmp_path, "source.json")["source_document"] == {"12:9": GRAPH["1"]}
    link = tmp_path / "link.json"
    link.hardlink_to(outside)
    with pytest.raises(DomainError):
        read_export(tmp_path, "link.json")


def test_receipt_envelope_allows_unicode_escapes_without_raising_other_limits() -> None:
    assert body_limit("/internal/v1/commands/cmd_synthetic/receipts") == 8 * MAX_WORKFLOW_BYTES
    assert body_limit("/admin/api/v1/workflow-pulls") == MAX_WORKFLOW_BYTES
