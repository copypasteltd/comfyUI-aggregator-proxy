"""Bounded offline tests for our native Go metadata client, never upstream tests."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "integrations/sub2api/controlclient"


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main() -> None:
    toolchain = json.loads((ROOT / "config/native-toolchains.lock.json").read_text(encoding="utf-8"))["sub2api_go"]
    if sha256(ROOT / ".local/toolchains/go1.27.3.windows-amd64.zip") != toolchain["sha256"]:
        raise RuntimeError("Native Go archive differs from official SHA-256")
    native = ROOT / ".local/toolchains/go-1.27.3/go/bin"
    environment = os.environ.copy()
    environment.update({"GOENV": "off", "GOTOOLCHAIN": "local", "GOTELEMETRY": "off", "GOWORK": "off", "GOFLAGS": "", "CGO_ENABLED": "0", "GOOS": "windows", "GOARCH": "amd64", "GOPROXY": "off", "GOSUMDB": "off", "GOCACHE": str(ROOT / ".local/go/build-cache"), "GOPATH": str(ROOT / ".local/go/path"), "GOMODCACHE": str(ROOT / ".local/go/module-cache"), "GOTMPDIR": str(ROOT / ".local/go/tmp")})
    for name in ("GOCACHE", "GOPATH", "GOMODCACHE", "GOTMPDIR"):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(native / "go.exe"), "version"], cwd=MODULE, env=environment, capture_output=True, check=True, timeout=45).stdout.decode("utf-8").strip()  # noqa: S603 -- fixed native executable, archive checked above
    if version != "go version go1.27.3 windows/amd64":
        raise RuntimeError("Unexpected native compiler version")
    formatted = subprocess.run([str(native / "gofmt.exe"), "-l", *[path.name for path in sorted(MODULE.glob("*.go"))]], cwd=MODULE, env=environment, capture_output=True, check=True, timeout=30)  # noqa: S603 -- fixed formatter and module-local paths
    if formatted.stdout:
        raise RuntimeError("Go client sources require gofmt")
    sources = {path.name: sha256(path) for path in sorted(MODULE.iterdir()) if path.is_file() and (path.suffix == ".go" or path.name in ("go.mod", "go.sum"))}
    command = [str(native / "go.exe"), "test", "-json", "-mod=readonly", "-count=1", "-p=2", "-timeout=90s", "./..."]
    log = ROOT / ".local/sub2api-controlclient-tests.jsonl"
    started = time.monotonic()
    timed_out = False
    print(json.dumps({"phase": "native_client_tests", "compiler": version, "offline": True, "timeout_seconds": 300}), flush=True)
    with log.open("wb") as messages, subprocess.Popen(command, cwd=MODULE, env=environment, stdin=subprocess.DEVNULL, stdout=messages, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW) as process:  # noqa: S603 -- constant package within our standard-library-only module
        try:
            result = process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            timed_out = True
            if process.poll() is None:
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=15)  # noqa: S603 -- only the tree owned by this live Popen
            if process.poll() is None:
                process.kill()
            result = process.wait(timeout=15)
    tests: list[dict[str, object]] = []
    package_passed = False
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("Action") == "pass" and not entry.get("Test"):
            package_passed = True
        if entry.get("Test") and entry.get("Action") in ("pass", "fail", "skip"):
            tests.append({"name": entry["Test"], "status": entry["Action"], "seconds": entry.get("Elapsed", 0)})
    unchanged = all(sha256(MODULE / name) == digest for name, digest in sources.items())
    top_level = [test for test in tests if "/" not in str(test["name"])]
    passed = result == 0 and not timed_out and package_passed and unchanged and bool(top_level) and all(test["status"] == "pass" for test in tests)
    report = {"at": datetime.now(UTC).isoformat(), "compiler": version, "command": "go test -json -mod=readonly -count=1 -p=2 -timeout=90s ./...", "module": "render-control/sub2api-controlclient", "source_sha256": sources, "sources_unchanged": unchanged, "offline_module_downloads_disabled": True, "passed": passed, "exit_code": result, "timed_out": timed_out, "top_level_tests": len(top_level), "subtests": len(tests) - len(top_level), "tests": tests, "duration_seconds": round(time.monotonic() - started, 2), "provider_implemented": False, "upstream_runtime_verified": False, "virtualization_used": False}
    evidence = ROOT.parent / "docs/evidence" / datetime.now().strftime("%Y-%m-%d")
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "sub2api-controlclient-tests.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    if not passed:
        print(log.read_bytes()[-12000:].decode("utf-8", errors="replace"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
