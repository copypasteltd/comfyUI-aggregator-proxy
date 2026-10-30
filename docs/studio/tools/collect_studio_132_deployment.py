"""Publish non-sensitive fixed deployment receipts without altering their sources."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".local/studio-ops"
TARGET = ROOT / "docs/research/studio-implementation"


def source(action):
    files = sorted(PRIVATE.glob("ship-" + action + "-*.json"))
    receipt = json.loads(files[-1].read_text(encoding="utf-8"))
    assert receipt["action"] == action and receipt["exit_code"] == 0 and not receipt["stderr"]
    return files[-1], receipt


def save(name, value):
    with (TARGET / name).open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    file, receipt = source("backend-status-1322")
    text = receipt["stdout"]
    assert text.startswith("prepared\n")
    prepared, end = json.JSONDecoder().raw_decode(text[len("prepared\n"):])
    remainder = text[len("prepared\n") + end:].strip()
    assert remainder.startswith("passed\n")
    promoted = json.loads(remainder[len("passed\n"):])
    assert prepared["passed"] and prepared["release_id"] == "linux-20261030.132.2"
    assert promoted["passed"] and not promoted["worker_restarted"] and not promoted["database_migration_executed"]
    assert promoted["steps"][0]["before"] == promoted["steps"][0]["after"]
    save("production-deployment-1322.json", {"passed": True, "scope": "Actual native offline preparation, verified archive backup, backend promotion and unchanged database counts; no full disaster restore", "prepared": prepared, "promotion": promoted, "private_source_receipt": file.relative_to(ROOT).as_posix(), "source_receipt_sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    for number in ("1321", "1322"):
        file, receipt = source("static-upgrade-" + number)
        value = json.loads(receipt["stdout"])
        assert value["passed"] and not value["backend_restarted"] and not value["old_ui_roundtrip_performed"]
        save("static-promotion-" + number + ".json", {**value, "at": receipt["at"], "private_source_receipt": file.relative_to(ROOT).as_posix(), "source_receipt_sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    old = json.loads((ROOT / ".local/studio-cloud-build/studio-20261030.132.1.json").read_text())
    new = json.loads((ROOT / ".local/studio-cloud-build/studio-20261030.132.2.json").read_text())
    assert old["files"] == new["files"] and old["source_sha256"] == new["source_sha256"]
    assert old["browser_storage_version"] == 2 and new["browser_storage_version"] == 3
    failed = TARGET / "public-storage-after-2026-10-30T01-00-19-355Z.json"
    assert not json.loads(failed.read_text())["passed"]
    save("static-manifest-correction-1322.json", {"passed": True, "scope": "Forward correction of an inaccurate package database-version field; identical 96 production UI files and source, no data downgrade or application-source change", "previous_static": old["release_id"], "current_static": new["release_id"], "previous_declared_database_version": 2, "current_declared_database_version": 3, "actual_ui_files_identical": True, "source_sha256": new["source_sha256"], "preserved_failed_report": failed.relative_to(ROOT).as_posix(), "cause": "A storage report lacking a version field silently defaulted to 2; the packager now requires an explicitly observed version and uses the passing v3 journal report."})
    print(json.dumps({"passed": True, "public_receipts_created": 4, "historical_receipts_preserved": True}))
