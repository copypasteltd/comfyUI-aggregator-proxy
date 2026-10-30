"""Capture an existing read-only delivery gate in a new, immutable report."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = ROOT / "aggregator-service"
CHECKS = {
    "docs": (REPOSITORY / "docs/tools/verify_docs.py", REPOSITORY),
    "readme": (REPOSITORY / "app/scripts/check-readme.py", REPOSITORY),
    "delivery": (ROOT / "docs/tools/check_studio_delivery.py", ROOT),
    "public": (ROOT / "docs/tools/check_studio_public.py", ROOT),
}

if __name__ == "__main__":
    name = sys.argv[1]
    script, cwd = CHECKS[name]
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    private = ROOT / ".local/studio-final-gates" / label
    private.mkdir(parents=True)
    completed = subprocess.run([sys.executable, "-B", str(script)], cwd=cwd, env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    (private / "stdout.log").write_bytes(completed.stdout)
    (private / "stderr.log").write_bytes(completed.stderr)
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, ValueError):
        result = {"passed": False, "failure": "Gate did not return a JSON report; inspect the retained private log"}
    result.update({"recorded_at": datetime.now(UTC).isoformat(), "gate": name, "exit_code": completed.returncode, "private_logs": private.relative_to(ROOT).as_posix(), "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(), "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest()})
    result["passed"] = result.get("passed", False) and completed.returncode == 0
    target = REPOSITORY / "docs/evidence/studio-release-136" / (name + "-final-" + label + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "gate": name, "report": target.relative_to(REPOSITORY).as_posix(), "errors": result.get("errors", []), "private_logs": private.relative_to(ROOT).as_posix()}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
