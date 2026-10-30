"""Bind batch UI receipts to unchanged deployed backend sources, without rerunning them."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "aggregator-service"
EVIDENCE = ROOT / "docs/research/studio-implementation"
sys.path.insert(0, str(REPO / "app/scripts"))
from release_source import source_snapshot  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    target = EVIDENCE / "batch-validation-notes-1331.json"
    runtime_target = EVIDENCE / "backend-runtime-unchanged-1331.json"
    assert not target.exists() and not runtime_target.exists()
    receipts = {
        "units": "unit-2026-10-30T01-35-11-303Z.json",
        "types": "typecheck-2026-10-30T01-35-11-308Z.json",
        "build": "build-2026-10-30T01-35-11-358Z.json",
        "browser": "browser-2026-10-30T01-36-18-771Z.json",
        "storage": "storage-2026-10-30T01-37-04-701Z.json",
        "task_backup": "task-backup-2026-10-30T01-37-04-548Z.json",
        "submission_lookup": "submission-lookup-2026-10-30T01-38-49-179Z.json",
        "batch": "batch-2026-10-30T01-37-58-914Z.json",
        "static_build": "static-build-studio-20261030.133.1.json",
        "health": "live-final-health-1331.json",
        "public_current_restart": "public-storage-after-2026-10-30T01-40-27-775Z.json",
    }
    promotions = list((ROOT / ".local/studio-ops").glob("ship-static-upgrade-1331-*.json"))
    assert len(promotions) == 1
    item = read(promotions[0]); assert item["exit_code"] == 0
    promotion = json.loads(item["stdout"])
    assert promotion["passed"] and promotion["steps"] == ["studio-20261030.133.1"]
    public_promotion = EVIDENCE / "static-upgrade-1331.json"
    assert not public_promotion.exists()
    receipts["static_promotion"] = public_promotion.name
    current = source_snapshot(REPO / "app")
    deployed = read(ROOT / ".local/studio-cloud-build/linux-20261030.132.2-build.json")
    assert len(current) == deployed["source_files"] == 555
    commit = deployed["application_source_revision"]
    assert commit == "97459bc3ce83551bfec8c0ee65506a7eb02e60b9"
    batch_input = "".join(f"{commit}:app/{name}\n" for name in current).encode()
    process = subprocess.run(["git", "cat-file", "--batch"], cwd=REPO, input=batch_input, capture_output=True, check=True)
    stream = io.BytesIO(process.stdout)
    for name, digest in current.items():
        header = stream.readline().decode().split()
        assert len(header) == 3 and header[1] == "blob", name
        assert hashlib.sha256(stream.read(int(header[2]))).hexdigest() == digest, name
        assert stream.read(1) == b"\n"
    assert not stream.read()
    public_promotion.write_text(json.dumps(promotion, indent=2) + "\n", encoding="utf-8")
    runtime = {"contract_version": "0.4.0", "passed": True, "at": datetime.now(UTC).isoformat(), "scope": "All 555 runtime source bytes equal the deployed 132.2 build and immutable runtime Git commit; no backend tests rerun for a static-only release", "backend_release": "linux-20261030.132.2", "runtime_commit": commit, "deployed_build_sha256": sha(ROOT / ".local/studio-cloud-build/linux-20261030.132.2-build.json"), "source_files": current}
    runtime_target.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    notes = {"contract_version": "0.4.0", "passed": True, "at": datetime.now(UTC).isoformat(), "scope": "147 frontend units/type/build; current production dist with synthetic HTTP and native IndexedDB, deployed public HTTP and preservation; no new GPU run, real phone or fleet acceptance", "source_sha256": read(EVIDENCE / receipts["build"])["source_after"], "frontend_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT / "infinite-canvas").decode().strip(), "backend_release": "linux-20261030.132.2", "static_release": "studio-20261030.133.1", "runtime_commit": commit, "backend_runtime_receipt": runtime_target.name, "backend_runtime_receipt_sha256": sha(runtime_target), "passing_package_evidence": receipts, "report_sha256": {name: sha(EVIDENCE / file) for name, file in receipts.items()}, "backend_tests_reused_from": "submission-lookup-validation-notes-1321.json", "backend_focused": "docs/evidence/studio-release-132/studio-fcd6464d8b7548fabc7126b95e3d5b78.json", "backend_compatibility": "docs/evidence/studio-release-132/studio-8eb8130972b846d6bf39443b4a876a71.json", "failed_reports_preserved": [file.name for file in sorted(EVIDENCE.glob("batch-20*.json")) if not read(file)["passed"]], "failure_explanations": ["The first fixture closed a reused socket before headers; Chromium retried the same key at transport level. Changed injection to a truncated accepted response body, preserving the original failed report.", "A fixture checked button disappearance before the asynchronous POST completed; replaced it with the existing success completion signal.", "Two runs exposed generic loading/failure placeholders covering managed task cards. Product fix now leaves those cards' query and read-only recovery controls available; the passing test queries the original failed job ID."], "full_acceptance": "HOLD", "no_new_gpu_submission": True, "no_disabled_key_reenabled": True, "no_local_virtualization": True}
    assert len(notes["failed_reports_preserved"]) == 4
    for kind in ("units", "types", "build"):
        result = read(EVIDENCE / receipts[kind]); assert result["passed"] and result["source_unchanged"] and result["source_after"] == notes["source_sha256"]
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15), ("batch", 15), ("public_current_restart", 3)):
        result = read(EVIDENCE / receipts[name]); assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values())
    assert read(EVIDENCE / receipts["health"])["studio"] == notes["static_release"]
    target.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "source_sha256": notes["source_sha256"], "runtime_files_unchanged": len(current), "receipts": len(receipts), "failed_reports_preserved": len(notes["failed_reports_preserved"])}))
