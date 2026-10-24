"""Bounded native Windows build of the pinned, unmodified Sub2API backend."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=int, default=300)
    arguments = parser.parse_args()
    if not 30 <= arguments.timeout_seconds <= 900:
        parser.error("timeout must be between 30 and 900 seconds")
    lock_path = ROOT / "integrations/sub2api/upstream-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    source = ROOT / (".local/upstream/sub2api-" + lock["commit"][:12])
    archive = ROOT / (".local/research/sub2api-" + lock["commit"] + ".zip")
    verification_started = time.monotonic()
    print(json.dumps({"phase": "source_verification", "timeout_seconds": 180}), flush=True)
    if sha256(archive) != lock["archive_sha256"]:
        raise RuntimeError("Upstream archive digest differs from the lock")
    source_files = 0
    archived_paths: set[str] = set()
    expected_directories: set[PurePosixPath] = {PurePosixPath(".")}
    resolved_source = source.resolve()
    resolved_parents: dict[PurePosixPath, Path] = {PurePosixPath("."): resolved_source}
    with zipfile.ZipFile(archive) as snapshot:
        prefix = "sub2api-" + lock["commit"] + "/"
        for item in snapshot.infolist():
            if item.is_dir():
                continue
            if not item.filename.startswith(prefix):
                raise RuntimeError("Unexpected upstream archive root")
            relative = PurePosixPath(item.filename[len(prefix):])
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("Upstream source escaped the fixed checkout")
            if time.monotonic() - verification_started > 180:
                raise TimeoutError("Source verification exceeded 180 seconds")
            if relative.parent not in resolved_parents:
                resolved_parent = (resolved_source / relative.parent).resolve()
                if not resolved_parent.is_relative_to(resolved_source):
                    raise RuntimeError("Source parent escaped the fixed checkout")
                resolved_parents[relative.parent] = resolved_parent
            target = resolved_parents[relative.parent] / relative.name
            with snapshot.open(item) as archived:
                expected = hashlib.file_digest(archived, "sha256").hexdigest()
            if not stat.S_ISREG(target.lstat().st_mode) or sha256(target) != expected:
                raise RuntimeError("Extracted source differs from the pinned archive: " + str(relative))
            archived_paths.add(relative.as_posix())
            expected_directories.update(relative.parents)
            source_files += 1
            if source_files % 512 == 0:
                print(json.dumps({"phase": "source_verification", "files_checked": source_files}), flush=True)
    actual_paths: set[str] = set()
    for directory, folders, files in os.walk(resolved_source, followlinks=False):
        if time.monotonic() - verification_started > 180:
            raise TimeoutError("Source verification exceeded 180 seconds")
        parent = Path(directory).relative_to(resolved_source)
        if any(PurePosixPath((parent / name).as_posix()) not in expected_directories for name in folders):
            raise RuntimeError("Extracted source contains an unexpected directory")
        actual_paths.update((parent / name).as_posix() for name in files)
    if actual_paths != archived_paths:
        raise RuntimeError("Extracted source contains extra or missing files")
    for relative, expected in lock["source_sha256"].items():
        if sha256(source / relative) != expected:
            raise RuntimeError("Pinned source file changed: " + relative)
    go = ROOT / ".local/toolchains/go-1.27.3/go/bin/go.exe"
    toolchain = json.loads((ROOT / "config/native-toolchains.lock.json").read_text(encoding="utf-8"))["sub2api_go"]
    if sha256(ROOT / ".local/toolchains/go1.27.3.windows-amd64.zip") != toolchain["sha256"]:
        raise RuntimeError("Toolchain archive differs from official SHA-256")
    environment = os.environ.copy()
    environment.update({"GOENV": "off", "GOTOOLCHAIN": "local", "GOTELEMETRY": "off", "GOCACHE": str(ROOT / ".local/go/build-cache"), "GOPATH": str(ROOT / ".local/go/path"), "GOMODCACHE": str(ROOT / ".local/go/module-cache"), "GOPROXY": "https://mirrors.aliyun.com/goproxy", "GOSUMDB": "sum.golang.org https://mirrors.aliyun.com/goproxy/sumdb/sum.golang.org", "GODEBUG": (environment.get("GODEBUG", "") + ",http2client=0").lstrip(","), "GOPRIVATE": "", "GONOPROXY": "", "GONOSUMDB": "", "CGO_ENABLED": "0", "GOOS": "windows", "GOARCH": "amd64"})
    environment.update({"GOWORK": "off", "GOFLAGS": "", "GOAUTH": "off", "GOINSECURE": "", "GOVCS": "*:off", "GOTMPDIR": str(ROOT / ".local/go/tmp")})
    local_proxy = ROOT / ".local/research/go-proxy"
    if local_proxy.exists():
        environment["GOPROXY"] = local_proxy.as_uri() + ",https://goproxy.cn,https://mirrors.aliyun.com/goproxy"
    for directory in ("GOCACHE", "GOPATH", "GOMODCACHE", "GOTMPDIR"):
        Path(environment[directory]).mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(go), "version"], env=environment, capture_output=True, check=True, text=True, timeout=45).stdout.strip()  # noqa: S603 -- fixed, officially hash-verified native Go executable
    if version != "go version go1.27.3 windows/amd64":
        raise RuntimeError("Unexpected native compiler version")
    backend = source / "backend"
    fingerprints = {name: sha256(backend / name) for name in ("go.mod", "go.sum")}
    output = ROOT / ".local/bin/sub2api-stock.exe"
    output.parent.mkdir(exist_ok=True, parents=True)
    log = ROOT / ".local/sub2api-native-build.log"
    command = [str(go), "build", "-mod=readonly", "-trimpath", "-p=2", "-o", str(output), "./cmd/server"]
    started = time.monotonic()
    timed_out = False
    print(json.dumps({"phase": "native_build", "compiler": version, "timeout_seconds": arguments.timeout_seconds}), flush=True)
    with log.open("wb") as messages, subprocess.Popen(command, cwd=backend, env=environment, stdin=subprocess.DEVNULL, stdout=messages, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW) as process:  # noqa: S603 -- pinned native Go build, constant package and project-local output
        try:
            result = process.wait(timeout=arguments.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            # This live Popen owns the root process; stop its compiler descendants too.
            if process.poll() is None:
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=15)  # noqa: S603 -- fixed OS executable, live Popen-owned process tree only
            if process.poll() is None:
                process.kill()
            result = process.wait(timeout=15)
    unchanged = all(sha256(backend / name) == digest for name, digest in fingerprints.items())
    passed = result == 0 and not timed_out and unchanged and output.exists()
    evidence = ROOT.parent / "docs/evidence" / datetime.now().strftime("%Y-%m-%d")
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"at": datetime.now(UTC).isoformat(), "source_commit": lock["commit"], "source_archive_sha256": lock["archive_sha256"], "compiler": version, "command": "go build -mod=readonly -trimpath -p=2 -o <app/.local binary> ./cmd/server", "frontend_embedded": False, "module_proxy": "project-local file cache, goproxy.cn, mirrors.aliyun.com" if local_proxy.exists() else "https://mirrors.aliyun.com/goproxy", "http2_client_disabled": True, "native_backend_compile_verified": passed, "exit_code": result, "timed_out": timed_out, "module_files_unchanged": unchanged, "duration_seconds": round(time.monotonic() - started, 2), "provider_implemented": False, "runtime_integration_verified": False, "virtualization_used": False}
    report.update({"source_archive_tree_verified": True, "source_files_verified": source_files})
    if passed:
        report["binary_sha256"] = sha256(output)
        report["binary_bytes"] = output.stat().st_size
    (evidence / "sub2api-native-build.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lock["native_build_verified"] = passed
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    toolchains_path = ROOT / "config/native-toolchains.lock.json"
    toolchains = json.loads(toolchains_path.read_text(encoding="utf-8"))
    toolchains["sub2api_go"]["native_build_verified"] = passed
    toolchains_path.write_text(json.dumps(toolchains, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    if not passed:
        # Dependencies are public module names; no deployment configuration is loaded.
        print(log.read_bytes()[-12000:].decode("utf-8", errors="replace"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
