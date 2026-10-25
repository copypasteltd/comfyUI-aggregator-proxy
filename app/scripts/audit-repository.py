"""Inspect Git publication candidates without printing credential values."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential": re.compile(rb"(?:admin|worker|key|gateway|mon|sk(?:-gen)?)_[0-9a-f]{32}\.[A-Za-z0-9_-]{43,}"),
    "github_token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "database_password": re.compile(rb"postgres(?:ql)?(?:\+psycopg)?://[^\s:/]+:([^\s@]+)@"),
}
PRIVATE = {".git", ".local", ".venv", "node_modules", "__pycache__"}


def staged_bytes(git: str) -> dict[str, bytes]:
    result = subprocess.run([git, "ls-files", "--stage", "-z"], cwd=ROOT, capture_output=True, check=True)  # noqa: S603 -- fixed Git arguments
    entries = {}
    for entry in result.stdout.decode("utf-8").split("\0"):
        if not entry:
            continue
        metadata, _, name = entry.partition("\t")
        mode, identifier, stage = metadata.split()
        if stage != "0" or mode not in {"100644", "100755"}:
            raise SystemExit("Unmerged or non-regular staged file; inspect Git status")
        entries[name] = identifier
    identifiers = sorted(set(entries.values()))
    result = subprocess.run([git, "cat-file", "--batch"], cwd=ROOT, input=("\n".join(identifiers) + "\n").encode(), capture_output=True, check=True) if identifiers else None  # noqa: S603 -- object IDs obtained from the index
    data, position, objects = result.stdout if result else b"", 0, {}
    for identifier in identifiers:
        end = data.index(b"\n", position)
        actual, kind, length = data[position:end].decode("ascii").split()
        if actual != identifier or kind != "blob":
            raise SystemExit("Unexpected Git object")
        position = end + 1
        objects[identifier] = data[position:position + int(length)]
        position += int(length) + 1
    return {name: objects[identifier] for name, identifier in entries.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    git = shutil.which("git")
    if not git:
        raise SystemExit("Git is required")
    command = [git, "ls-files", "-z", "--cached"]
    if not args.staged:
        command.extend(["--others", "--exclude-standard"])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, check=True)  # noqa: S603 -- fixed Git arguments
    names = sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})
    index = staged_bytes(git) if args.staged else None
    live: list[bytes] = []
    env = ROOT / "app/.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if not separator:
                continue
            value = value.strip().strip("\"'")
            if "DATABASE_URL" in name:
                value = unquote(urlsplit(value.replace("postgresql+psycopg", "postgresql")).password or "")
            elif not any(part in name for part in ("SECRET", "PASSWORD", "PEPPER", "ENCRYPTION_KEY", "TOKEN")):
                continue
            if len(value) >= 24 and not value.startswith("REPLACE"):
                live.append(value.encode())
    findings: list[dict[str, object]] = []
    total = 0
    for name in names:
        path = ROOT / name
        if index is None and not path.is_file():
            continue
        if any(part in PRIVATE for part in Path(name).parts) or path.name == ".env":
            findings.append({"path": name, "kind": "private_path", "count": 1})
            continue
        data = index[name] if index is not None else path.read_bytes()
        total += len(data)
        if len(data) > 50 * 1024 * 1024:
            findings.append({"path": name, "kind": "oversized_file", "count": 1})
        for kind, pattern in PATTERNS.items():
            matches = list(pattern.finditer(data))
            if kind == "database_password":
                matches = [m for m in matches if not m.group(1).startswith((b"REPLACE", b"<", b"${")) and m.group(1) != b"password" and not re.fullmatch(rb"\{[A-Za-z_][A-Za-z0-9_]*\}", m.group(1))]
            if matches:
                findings.append({"path": name, "kind": kind, "count": len(matches)})
        if any(secret in data for secret in live):
            findings.append({"path": name, "kind": "current_local_secret", "count": 1})
    report = {"at": datetime.now(UTC).isoformat(), "passed": not findings, "scope": "staged" if args.staged else "Git publication candidates", "files": len(names), "bytes": total, "current_local_secret_values_checked": len(live), "findings": findings, "credential_values_printed": False}
    if args.report:
        target = args.report.resolve()
        if not target.is_relative_to(ROOT / "docs"):
            raise SystemExit("Reports belong under docs/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "files": len(names), "MiB": round(total / 1024**2, 2), "finding_files": len({f["path"] for f in findings}), "findings": findings[:18]}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
