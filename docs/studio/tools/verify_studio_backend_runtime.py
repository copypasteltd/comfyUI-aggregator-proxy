"""Recheck all deployed backend source inputs against local files and frozen Git."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = ROOT / "aggregator-service"
EVIDENCE = ROOT / "docs/research/studio-implementation"


def sha(data):
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    historical = EVIDENCE / "backend-runtime-unchanged-1351.json"
    original = json.loads(historical.read_text(encoding="utf-8"))
    assert original["passed"] and len(original["source_files"]) == 555
    frozen = subprocess.check_output(["git", "archive", original["runtime_commit"], "app"], cwd=REPOSITORY)
    with tarfile.open(fileobj=io.BytesIO(frozen)) as archive:
        for name, digest in original["source_files"].items():
            assert sha((REPOSITORY / "app" / name).read_bytes()) == digest
            member = archive.extractfile("app/" + name)
            assert member and sha(member.read()) == digest
    report = {**original, "at": datetime.now(UTC).isoformat(), "scope": "All 555 backend runtime inputs equal current delivery files and frozen deployed Git; prior 112 focused / 1486 compatibility tests are reused, not rerun. Static Key-rotation UI only; no backend code migration.", "provenance": {"file": historical.relative_to(ROOT).as_posix(), "sha256": sha(historical.read_bytes())}, "git_inputs_rechecked": True}
    target = EVIDENCE / "backend-runtime-unchanged-1361.json"
    with target.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": True, "files": len(report["source_files"]), "runtime_commit": report["runtime_commit"], "report": target.relative_to(ROOT).as_posix()}))
