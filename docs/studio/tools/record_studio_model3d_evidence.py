"""Bind the GLB increment to its exact public package and unchanged backend runtime."""
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


def latest(prefix):
    passing = [file for file in sorted(EVIDENCE.glob(prefix + "-20*.json")) if read(file).get("passed")]
    assert passing, prefix
    return passing[-1].name


if __name__ == "__main__":
    target = EVIDENCE / "model3d-validation-notes-1341.json"
    runtime_target = EVIDENCE / "backend-runtime-unchanged-1341.json"
    assert not target.exists() and not runtime_target.exists()
    receipts = {name: latest(prefix) for name, prefix in {"units": "unit", "types": "typecheck", "build": "build", "browser": "browser", "storage": "storage", "task_backup": "task-backup", "submission_lookup": "submission-lookup", "batch": "batch", "model3d": "model3d", "public_model3d": "public-model3d", "public_current_restart": "public-storage-after"}.items()}
    receipts.update({"static_build": "static-build-studio-20261030.134.1.json", "health": "live-final-health-1341.json", "static_promotion": "static-upgrade-1341.json"})
    promotions = list((ROOT / ".local/studio-ops").glob("ship-static-upgrade-1341-*.json")); assert len(promotions) == 1
    item = read(promotions[0]); assert item["exit_code"] == 0
    promotion = json.loads(item["stdout"]); assert promotion["passed"] and promotion["steps"] == ["studio-20261030.134.1"] and promotion["studio_blob_connect_allowed"]
    current = source_snapshot(REPO / "app")
    deployed = read(ROOT / ".local/studio-cloud-build/linux-20261030.132.2-build.json")
    assert len(current) == deployed["source_files"] == 555
    commit = deployed["application_source_revision"]; assert commit == "97459bc3ce83551bfec8c0ee65506a7eb02e60b9"
    process = subprocess.run(["git", "cat-file", "--batch"], cwd=REPO, input="".join(f"{commit}:app/{name}\n" for name in current).encode(), capture_output=True, check=True)
    stream = io.BytesIO(process.stdout)
    for name, digest in current.items():
        header = stream.readline().decode().split(); assert len(header) == 3 and header[1] == "blob", name
        assert hashlib.sha256(stream.read(int(header[2]))).hexdigest() == digest and stream.read(1) == b"\n", name
    assert not stream.read()
    source = read(EVIDENCE / receipts["build"])["source_after"]
    for name in ("units", "types", "build"):
        assert read(EVIDENCE / receipts[name])["source_after"] == source
    ui = read(EVIDENCE / receipts["browser"])
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15), ("batch", 15), ("model3d", 17)):
        result = read(EVIDENCE / receipts[name]); assert len(result["checks"]) == count and all(result["cleanup"].values()) and result["static_artifact_sha256"] == ui["static_artifact_sha256"]
    assert read(EVIDENCE / receipts["static_build"])["source_sha256"] == promotion["source_sha256"] == source
    assert read(EVIDENCE / receipts["public_model3d"])["static_release"] == "studio-20261030.134.1"
    public_promotion = EVIDENCE / receipts["static_promotion"]; assert not public_promotion.exists()
    public_promotion.write_text(json.dumps(promotion, indent=2) + "\n", encoding="utf-8")
    runtime = {"contract_version": "0.4.0", "passed": True, "at": datetime.now(UTC).isoformat(), "scope": "All 555 backend runtime source files equal deployed 132.2 inputs and immutable runtime Git; backend tests reused for static-only GLB release, dedicated edge CSP reloaded separately", "backend_release": "linux-20261030.132.2", "runtime_commit": commit, "deployed_build_sha256": sha(ROOT / ".local/studio-cloud-build/linux-20261030.132.2-build.json"), "source_files": current}
    runtime_target.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    notes = {"contract_version": "0.4.0", "passed": True, "at": datetime.now(UTC).isoformat(), "scope": "159 units/type/build; shipped UI with 112 native-browser checks including 17 GLB cases, real saved ComfyUI GLB plus synthetic errors; public HTTP/CSP/WebGL and data preservation; no new GPU generation or physical-phone/fleet acceptance", "source_sha256": source, "frontend_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT / "infinite-canvas").decode().strip(), "backend_release": "linux-20261030.132.2", "static_release": "studio-20261030.134.1", "runtime_commit": commit, "backend_runtime_receipt": runtime_target.name, "backend_runtime_receipt_sha256": sha(runtime_target), "passing_package_evidence": receipts, "report_sha256": {name: sha(EVIDENCE / file) for name, file in receipts.items()}, "backend_tests_reused_from": "submission-lookup-validation-notes-1321.json", "backend_focused": "docs/evidence/studio-release-132/studio-fcd6464d8b7548fabc7126b95e3d5b78.json", "backend_compatibility": "docs/evidence/studio-release-132/studio-8eb8130972b846d6bf39443b4a876a71.json", "failed_reports_preserved": [file.name for file in sorted(EVIDENCE.glob("model3d-20*.json")) if not read(file)["passed"]] + ["typecheck-2026-10-30T02-03-25-567Z.json"], "failure_explanations": ["An initial nullable state guard failed type checking and was corrected before the passing frozen build.", "The first synthetic PNG had invalid bytes; replaced the fixture with a native Pillow-generated PNG. The product correctly refused the broken texture and released its URL.", "A fixture counted library cards before the asynchronous directory read completed; now waits for the original content heading. No product timeout was increased.", "Screenshots and pixel comparisons fast-forward finite UI transitions, so neither faded overlays nor ongoing modal animation can substitute for rendered model changes. Long content IDs now wrap inside library cards."], "full_acceptance": "HOLD", "no_new_gpu_submission": True, "no_disabled_key_reenabled": True, "no_local_virtualization": True}
    notes["failed_reports_preserved"].append("batch-2026-10-30T02-20-21-814Z.json")
    notes["failure_explanations"].append("A batch fixture set a hidden file input during connection hydration, bypassing the disabled user control. It now opens the actual enabled import button and uses its native file chooser; no product timeout was increased.")
    assert len(notes["failed_reports_preserved"]) == 4
    target.write_text(json.dumps(notes, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "notes": target.relative_to(ROOT).as_posix(), "runtime_sources": len(current), "receipts": len(receipts)}))
