"""Check selected delivery files against actual private session credentials.

Only counts and paths are reported. Private values never leave this process.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "aggregator-service"


def secrets(value):
    found = set()
    if isinstance(value, dict):
        for name, item in value.items():
            if any(word in name.lower() for word in ("password", "secret", "bearer", "authorization", "token", "api_key")) and isinstance(item, str) and len(item) >= 8:
                found.add(item.encode())
            elif isinstance(item, (dict, list)):
                found.update(secrets(item))
    elif isinstance(value, list):
        for item in value:
            found.update(secrets(item))
    return found


if __name__ == "__main__":
    values = set()
    inputs = [ROOT / ".local/studio-ops" / name for name in ("connection.json", "admin-access.json", "validation-key.json", "restricted-validation-key.json", "lookup-validation-key-1321.json")]
    inputs.append(Path("C:/Users/carzy/.codex/skills/remote-3080/device.json"))
    for source in inputs:
        if source.exists():
            values.update(secrets(json.loads(source.read_text(encoding="utf-8-sig"))))
    assert len(values) >= 3, "Actual session secrets must be available for this check"
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO).decode().split("\0")
    problems = []
    count = 0
    for name in sorted(set(names) - {""}):
        path = REPO / name
        if not path.is_file():
            continue
        count += 1
        if any(part in {".local", ".venv", "node_modules", ".git"} for part in Path(name).parts):
            problems.append({"file": name, "reason": "private directory selected"})
        if path.stat().st_size > 40 * 1024 * 1024:
            problems.append({"file": name, "reason": "review large delivery file"})
        data = path.read_bytes()
        if any(value in data for value in values):
            problems.append({"file": name, "reason": "actual session credential found"})
    report = {"passed": not problems, "selected_file_count": count, "private_credentials_checked": len(values), "problems": problems, "scope": "actual selected delivery bytes and current session secrets; not a claim to detect every possible unknown credential"}
    print(json.dumps(report, ensure_ascii=False))
    if problems:
        raise SystemExit(1)
