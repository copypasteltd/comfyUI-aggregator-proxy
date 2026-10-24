"""Build the reviewed Provider composition using the pinned native Go runtime."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    spec = importlib.util.spec_from_file_location("sub2api_ledger_harness", ROOT / "scripts/test-sub2api-ledger.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Native source verification entry is unavailable")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    sha256 = harness.sha256
    script_hash = sha256(Path(__file__))
    harness_hash = sha256(ROOT / "scripts/test-sub2api-ledger.py")
    lock = json.loads((ROOT / "integrations/sub2api/upstream-lock.json").read_text(encoding="utf-8"))
    backend, sources, reused = harness.materialize(lock)
    go, environment = harness.native_environment()
    formatted = subprocess.run([str(go.with_name("gofmt.exe")), "-l", *(str(backend / name) for name in sources if name.endswith(".go"))], capture_output=True, check=True, timeout=30)  # noqa: S603 -- fixed native formatter and verified local sources
    if formatted.stdout:
        raise RuntimeError("Reviewed Provider sources require gofmt")
    marker = ROOT / ".local/sub2api-overlay-workspace.json"
    marker.write_text(json.dumps({"backend": str(backend), "passed": False, "overlay_files_sha256": sources}, indent=2) + "\n", encoding="utf-8")
    binary = ROOT / ".local/bin/sub2api-provider.exe"
    binary.parent.mkdir(parents=True, exist_ok=True)
    log = ROOT / ".local/sub2api-provider-build.log"
    started = time.monotonic()
    print(json.dumps({"phase": "compile_reviewed_stock_server_composition", "timeout_seconds": 900}), flush=True)
    result, timed_out = harness.run_owned([str(go), "build", "-trimpath", "-mod=readonly", "-p=2", "-o", str(binary), "./cmd/server"], backend, environment, log, 900)
    maintained = {path.relative_to(ROOT / "integrations/sub2api/overlay/backend").as_posix(): sha256(path) for path in (ROOT / "integrations/sub2api/overlay/backend").rglob("*") if path.is_file()}
    maintained.update({"internal/rendercontrol/controlclient/" + path.name: sha256(path) for path in (ROOT / "integrations/sub2api/controlclient").glob("*.go") if not path.name.endswith("_test.go")})
    patches = json.loads((ROOT / "integrations/sub2api/overlay/backend/render_control_patches.json").read_text(encoding="utf-8"))["patches"]
    maintained.update({item["path"]: item["patched_sha256"] for item in patches})
    unchanged = sources == maintained and all(sha256(backend / name) == expected for name, expected in sources.items()) and all(sha256(backend / name) == lock["source_sha256"]["backend/" + name] for name in ("go.mod", "go.sum")) and sha256(Path(__file__)) == script_hash and sha256(ROOT / "scripts/test-sub2api-ledger.py") == harness_hash
    passed = result == 0 and not timed_out and unchanged and binary.is_file()
    report = {
        "at": datetime.now(UTC).isoformat(), "passed": passed, "source_commit": lock["commit"],
        "source_archive_sha256": lock["archive_sha256"], "build_script_sha256": script_hash,
        "source_harness_sha256": harness_hash, "compiler": "go version go1.27.3 windows/amd64",
        "command": "go build -trimpath -mod=readonly -p=2 -o <app-local binary> ./cmd/server",
        "duration_seconds": round(time.monotonic() - started, 2), "exit_code": result, "timed_out": timed_out,
        "overlay_workspace_reused": reused, "overlay_files_sha256": sources,
        "maintained_and_generated_sources_match": unchanged, "module_files_unchanged": unchanged,
        "module_downloads_enabled": False, "frontend_embedded": False, "upstream_server_started": False,
        "provider_implemented": True, "runtime_integration_verified": False, "virtualization_used": False,
    }
    if passed:
        report.update({"binary_sha256": sha256(binary), "binary_bytes": binary.stat().st_size})
    evidence = ROOT.parent / "docs/evidence" / datetime.now().strftime("%Y-%m-%d")
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "sub2api-provider-native-build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    marker.write_text(json.dumps({"backend": str(backend), "passed": passed, "overlay_files_sha256": sources}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "duration_seconds", "exit_code", "timed_out", "maintained_and_generated_sources_match")}), flush=True)
    if not passed:
        print(log.read_text(encoding="utf-8", errors="replace")[-12000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
