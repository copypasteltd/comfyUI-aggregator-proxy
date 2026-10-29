"""Run a named native frontend feasibility check and record its exact result."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".local/assessment-native/probe/web"
EVIDENCE = ROOT / "docs/research/fusion-assessment-2026-10-30"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["install", "fresh-lock", "native-bindings", "ci", "ci-check", "build", "typecheck"])
    options = parser.parse_args()
    work = WORK
    if options.stage == "fresh-lock":
        work = ROOT / ".local/assessment-native/lock-resolver"
        work.mkdir(parents=True, exist_ok=True)
        if (work / "package-lock.json").exists():
            raise SystemExit("Fresh lock probe already exists; preserve its evidence.")
        shutil.copyfile(WORK / "package.json", work / "package.json")
    npm = shutil.which("npm.cmd")
    node = shutil.which("node")
    if not npm or not node:
        raise SystemExit("Native Node.js / npm not found.")
    cli = Path(npm).parent / "node_modules/npm/bin/npm-cli.js"
    if not cli.is_file():
        raise SystemExit(f"Native npm CLI not found beside npm.cmd: {cli}")
    arguments = {
        "install": ["install"],
        "fresh-lock": ["install", "--package-lock-only", "--ignore-scripts", "--include=optional"],
        "native-bindings": ["install", "--save-optional", "--save-exact", "--include=optional"],
        "ci": ["ci"],
        "ci-check": ["ci", "--dry-run", "--ignore-scripts"],
        "build": ["run", "build"],
        "typecheck": ["run", "typecheck"],
    }[options.stage]
    if options.stage == "native-bindings":
        packages = {
            "rollup": "@rollup/rollup-win32-x64-msvc",
            "@tailwindcss/oxide": "@tailwindcss/oxide-win32-x64-msvc",
            "lightningcss": "lightningcss-win32-x64-msvc",
            "esbuild": "@esbuild/win32-x64",
        }
        arguments += [
            f"{binding}@{json.loads((WORK / 'node_modules' / parent / 'package.json').read_text(encoding='utf-8'))['version']}"
            for parent, binding in packages.items()
        ]
    if options.stage in {"install", "fresh-lock", "native-bindings", "ci", "ci-check"}:
        arguments += ["--cache", str(ROOT / ".local/npm-cache"), "--no-audit", "--no-fund"]
    command = [node, str(cli), *arguments]
    start = datetime.now(timezone(timedelta(hours=8)))
    before = time.monotonic()
    result = subprocess.run(command, cwd=work, capture_output=True, encoding="utf-8", errors="replace")
    log = result.stdout + result.stderr
    local_log = ROOT / f".local/assessment-native/{options.stage}-{start.strftime('%H%M%S')}.log"
    local_log.write_text(log, encoding="utf-8")
    report = {
        "started_at": start.isoformat(),
        "duration_seconds": round(time.monotonic() - before, 3),
        "scope": "patched isolated frontend, not original upstream or integrated product",
        "stage": options.stage,
        "ci_check_is_clean_install": False if options.stage == "ci-check" else None,
        "working_directory": str(work),
        "command": command,
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
        "local_log": str(local_log),
        "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
        "output_tail": log[-10000:],
        "inputs": {
            name: hashlib.sha256((work / name).read_bytes()).hexdigest()
            for name in ["package.json", "package-lock.json", "src/components/layout/app-providers.tsx", "src/components/layout/model-script-editor.tsx"]
            if (work / name).is_file()
        },
    }
    target = EVIDENCE / f"native-{options.stage}-{start.strftime('%H%M%S')}.json"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "exit_code": result.returncode, "duration_seconds": report["duration_seconds"], "output_tail": log[-3000:]}, ensure_ascii=False))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
