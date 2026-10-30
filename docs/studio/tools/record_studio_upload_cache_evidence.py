"""Bind the storage increment to exact source, clean-install chain, public files and unchanged backend."""
from __future__ import annotations

import hashlib
import io
import json
import re
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


def latest(prefix):
    values = [file for file in sorted(EVIDENCE.glob(prefix + "-20*.json")) if read(file).get("passed")]
    assert values, prefix
    return values[-1].name


if __name__ == "__main__":
    target = EVIDENCE / "storage-validation-notes-1351.json"
    runtime_target = EVIDENCE / "backend-runtime-unchanged-1351.json"
    promotion_target = EVIDENCE / "static-upgrade-1351.json"
    assert not any(file.exists() for file in (target, runtime_target, promotion_target))
    receipts = {name: latest(prefix) for name, prefix in {"units": "unit", "types": "typecheck", "build": "build", "browser": "browser", "storage": "storage", "task_backup": "task-backup", "submission_lookup": "submission-lookup", "batch": "batch", "model3d": "model3d", "upload_cache": "upload-cache", "clean_final": "clean-build", "public_storage_space": "public-storage-space", "public_current_restart": "public-storage-after"}.items()}
    clean = read(EVIDENCE / receipts["clean_final"])
    origin_path = ROOT / clean["clean_install_reused"]["file"]
    assert sha(origin_path) == clean["clean_install_reused"]["sha256"]
    receipts["clean_origin"] = origin_path.name
    receipts.update({"static_build": "static-build-studio-20261030.135.1.json", "health": "live-final-health-1351.json", "static_promotion": promotion_target.name})
    promotions = [read(file) for file in (ROOT / ".local/studio-ops").glob("ship-static-upgrade-1351-*.json")]
    passing = [json.loads(value["stdout"]) for value in promotions if value["exit_code"] == 0]
    assert len(passing) == 1
    promotion = passing[0]
    assert promotion["passed"] and promotion["steps"] == ["studio-20261030.135.1"] and not promotion["backend_restarted"] and not promotion["worker_restarted"] and not promotion["dedicated_edge_reloaded"]
    current = source_snapshot(REPO / "app")
    deployed = read(ROOT / ".local/studio-cloud-build/linux-20261030.132.2-build.json")
    assert len(current) == deployed["source_files"] == 555
    commit = deployed["application_source_revision"]
    assert commit == "97459bc3ce83551bfec8c0ee65506a7eb02e60b9"
    process = subprocess.run(["git", "cat-file", "--batch"], cwd=REPO, input="".join(f"{commit}:app/{name}\n" for name in current).encode(), capture_output=True, check=True)
    stream = io.BytesIO(process.stdout)
    for name, digest in current.items():
        header = stream.readline().decode().split()
        assert len(header) == 3 and header[1] == "blob", name
        assert hashlib.sha256(stream.read(int(header[2]))).hexdigest() == digest and stream.read(1) == b"\n", name
    assert not stream.read()
    source = read(EVIDENCE / receipts["build"])["source_after"]
    for name in ("units", "types", "build"):
        value = read(EVIDENCE / receipts[name])
        assert value["passed"] and value["source_unchanged"] and value["source_after"] == source
    log = (EVIDENCE / read(EVIDENCE / receipts["units"])["log"]).read_text(encoding="utf-8")
    assert re.search(r"^# tests 169$", log, re.M) and re.search(r"^# fail 0$", log, re.M)
    ui = read(EVIDENCE / receipts["browser"])
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15), ("batch", 15), ("model3d", 17), ("upload_cache", 28)):
        value = read(EVIDENCE / receipts[name])
        assert value["passed"] and len(value["checks"]) == count and all(value["cleanup"].values()) and value["static_artifact_sha256"] == ui["static_artifact_sha256"]
    assert clean["source_before"] == clean["source_after"] == clean["copied_source_after"] == source and clean["static_artifact_sha256"] == ui["static_artifact_sha256"] and clean["unit_tests"] == 169
    origin = read(origin_path)
    assert origin["passed"] and any(value["name"] == "clean-install" and value["exit_code"] == 0 for value in origin["stages"])
    assert clean["package_sha256"] == origin["package_sha256"] and clean["lock_sha256"] == origin["lock_sha256"] and not origin["install_scripts_disabled"] and not origin["force_or_peer_bypass"]
    assert read(EVIDENCE / receipts["static_build"])["source_sha256"] == promotion["source_sha256"] == source
    for name, count in (("public_storage_space", 4), ("public_current_restart", 3)):
        value = read(EVIDENCE / receipts[name])
        assert value["passed"] and len(value["checks"]) == count and all(value["cleanup"].values()) and not value["blocked_mutations"] and not value["page_errors"]
    promotion_target.write_text(json.dumps(promotion, indent=2) + "\n", encoding="utf-8")
    runtime = {"contract_version": "0.4.0", "passed": True, "at": datetime.now(UTC).isoformat(), "scope": "All 555 backend runtime sources equal deployed 132.2 inputs and immutable Git; no API/Worker/Edge restart or new backend regression run for the static-only storage increment", "backend_release": "linux-20261030.132.2", "runtime_commit": commit, "deployed_build_sha256": sha(ROOT / ".local/studio-cloud-build/linux-20261030.132.2-build.json"), "source_files": current}
    runtime_target.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    notes = {"contract_version": "0.4.0", "passed": True, "at": datetime.now(UTC).isoformat(), "scope": "169 units/type/build, clean-lock install and isolated final-source build, 140 native-browser checks including 28 cache/quota/identity cases, actual public storage HTTP and native-data preservation; no new GPU, business Key or physical-phone/fleet acceptance", "source_sha256": source, "frontend_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT / "infinite-canvas").decode().strip(), "backend_release": "linux-20261030.132.2", "static_release": "studio-20261030.135.1", "runtime_commit": commit, "backend_runtime_receipt": runtime_target.name, "backend_runtime_receipt_sha256": sha(runtime_target), "passing_package_evidence": receipts, "report_sha256": {name: sha(EVIDENCE / file) for name, file in receipts.items()}, "backend_tests_reused_from": "submission-lookup-validation-notes-1321.json", "backend_focused": "docs/evidence/studio-release-132/studio-fcd6464d8b7548fabc7126b95e3d5b78.json", "backend_compatibility": "docs/evidence/studio-release-132/studio-8eb8130972b846d6bf39443b4a876a71.json", "failed_reports_preserved": ["packaging-1351-attempt-1.json", "clean-build-binding-1351-attempt-1.json"], "observations": ["The initial packaging command selected a storage report without a database version; the unchanged task-backup report exercises DBv3 and was used instead. No archive was created or sent by the rejected attempt.", "The first final isolated build omitted the repository .gitignore, causing Tailwind to scan old dist input and change CSS/chunk hashes. Its successful scoped unit/type/build report remains unchanged, but artifact-equality binding was rejected. Copying .gitignore and rebuilding with the same clean-installed lock produced all 104 canonical asset hashes exactly.", "Screenshot review found low-contrast default-button hover text; shared Lime tokens now keep text/borders dark. Actual final hover contrast is recorded; earlier passing intermediate UI/source reports remain historical.", "Plain HTTP did not provide a browser site estimate; the final page displays unavailable for both site usage and quota, keeping measured record bytes separate.", "The final isolated build reuses the exact native clean-install lock proved by its bound original receipt; no canonical node_modules was borrowed and install scripts were not disabled.", "Static 135.1 did not reapply the already enabled blob connection policy; its actual public CSP retains blob as verified separately."], "full_acceptance": "HOLD", "no_new_gpu_submission": True, "no_new_business_key": True, "no_disabled_key_reenabled": True, "no_local_virtualization": True}
    target.write_text(json.dumps(notes, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "notes": target.relative_to(ROOT).as_posix(), "runtime_sources": len(current), "receipts": len(receipts)}))
