"""Inspect two local source trees for integration planning; never call an API."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
CANVAS = PROJECT / "infinite-canvas"
AGGREGATOR = PROJECT.parent / "生图生视API封装"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def receipt(root: Path, name: str, needle: str | None = None) -> dict:
    data = (root / name).read_bytes()
    result = {
        "path": name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if needle is not None:
        matches = [
            number
            for number, line in enumerate(data.decode("utf-8").splitlines(), 1)
            if needle in line
        ]
        if not matches:
            raise ValueError(f"Source changed; review missing anchor: {name}: {needle}")
        result.update(anchor=needle, lines=matches)
    return result


def main() -> None:
    canvas_sources = {
        "AGENTS.md": None,
        "LICENSE": None,
        "VERSION": None,
        "web/package.json": None,
        "web/package-lock.json": None,
        "web/vite.config.ts": 'base: process.env.VITE_BASE || "/"',
        "web/src/router.tsx": "createBrowserRouter([",
        "web/src/stores/use-config-store.ts": 'export type ApiCallFormat = "openai" | "gemini"',
        "web/src/services/api/image.ts": "output_format: IMAGE_OUTPUT_FORMAT",
        "web/src/services/api/video.ts": "const pluginVideoResults = new Map",
        "web/src/services/api/audio.ts": 'aiApiUrl(requestConfig, "/audio/speech")',
        "web/src/services/api/model-plugin.ts": "options?.timeoutMs ?? 300000",
        "web/src/types/canvas.ts": "videoTaskId?: string",
        "web/src/lib/canvas/canvas-generation-helpers.ts": "hasResumableVideoTask",
        "web/src/services/file-storage.ts": 'storeName: "media_files"',
        "web/src/services/image-storage.ts": 'storeName: "image_files"',
        "web/src/services/config-file.ts": "config, webdav, promptSources",
        "web/src/components/canvas/canvas-toolbar.tsx": "left: 300",
        "web/src/components/canvas/canvas-side-panel.tsx": None,
        "web/src/lib/app-theme.ts": "getAntThemeConfig",
        "web/src/lib/canvas-theme.ts": "export const canvasThemes",
    }
    aggregator_sources = {
        "AGENTS.md": None,
        "app/config/openapi.json": None,
        "app/apps/control_api/src/control_api/main.py": '@app.post("/v1/jobs", status_code=202)',
        "app/apps/control_api/src/control_api/compatibility.py": "deadline = time.monotonic() + 60",
        "app/apps/control_api/src/control_api/compatibility_inputs.py": 'name != "input_reference"',
        "app/apps/control_api/src/control_api/monitoring.py": 'allow_origin_regex=".*"',
        "app/packages/contracts/src/generation_contracts/validation.py": 'extra="forbid"',
        "app/packages/contracts/src/generation_contracts/compatibility.py": "n: Literal[1] = 1",
        "app/packages/contracts/src/generation_contracts/model_usage.py": "output_retention_seconds: Literal[28800]",
        "app/packages/contracts/src/generation_contracts/discovery.py": "class PublishedCapability",
        "app/packages/domain/src/generation_domain/job-state-machine.json": '"terminal_states"',
        "app/packages/infrastructure/src/generation_infrastructure/jobs.py": 'result["artifacts"]',
        "app/deploy/native-linux/nginx.conf.example": "listen 39443",
        "app/config/lime-reference.lock.json": None,
        "app/apps/admin_web/src/theme/reference/lime.css": None,
        "app/apps/admin_web/src/theme/tokens.css": None,
    }
    spec = json.loads((AGGREGATOR / "app/config/openapi.json").read_text(encoding="utf-8"))
    required = {
        "/v1/access-policy": ["get"],
        "/v1/models": ["get"],
        "/v1/models/{model}": ["get"],
        "/v1/capabilities": ["get"],
        "/v1/assets": ["post"],
        "/v1/jobs": ["post", "get"],
        "/v1/jobs/{identifier}": ["get"],
        "/v1/jobs/{identifier}/cancel": ["post"],
        "/v1/content/{identifier}": ["get"],
        "/v1/content/{identifier}/download": ["get", "head"],
        "/v1/images/generations": ["post"],
        "/v1/images/generations/async": ["post"],
        "/v1/images/edits": ["post"],
        "/v1/images/edits/async": ["post"],
        "/v1/images/tasks/{identifier}": ["get"],
        "/v1/videos": ["post"],
        "/v1/videos/{identifier}": ["get"],
        "/v1/videos/{identifier}/content": ["get", "head"],
    }
    for path, methods in required.items():
        for method in methods:
            if method not in spec["paths"].get(path, {}):
                raise ValueError(f"Contract changed: missing {method.upper()} {path}")

    machine = json.loads(
        (AGGREGATOR / "app/packages/domain/src/generation_domain/job-state-machine.json").read_text(encoding="utf-8")
    )
    tracked = git(CANVAS, "ls-files", "-z").split("\0")
    tracked = [name for name in tracked if name]
    # Fail rather than silently publishing an incomplete download as ready.
    for name in tracked:
        if not (CANVAS / name).is_file():
            raise ValueError(f"Missing tracked source: {name}")
    git(CANVAS, "fsck", "--no-reflogs", "--no-progress")
    now = datetime.now(timezone(timedelta(hours=8)))
    report = {
        "checked_at": now.isoformat(),
        "evidence_level": "local_source_and_frozen_openapi_only",
        "purpose": "source download and integration preparation; no adapter implementation",
        "canvas": {
            "repository": git(CANVAS, "remote", "get-url", "origin"),
            "path": str(CANVAS),
            "commit": git(CANVAS, "rev-parse", "HEAD"),
            "version": (CANVAS / "VERSION").read_text(encoding="utf-8").strip(),
            "shallow": git(CANVAS, "rev-parse", "--is-shallow-repository") == "true",
            "working_tree_clean": not git(CANVAS, "status", "--porcelain"),
            "tracked_file_count": len(tracked),
            "tracked_file_bytes": sum((CANVAS / name).stat().st_size for name in tracked),
            "git_fsck_passed": True,
            "license": "MIT",
            "sources": [receipt(CANVAS, name, needle) for name, needle in canvas_sources.items()],
        },
        "aggregator": {
            "repository": git(AGGREGATOR, "remote", "get-url", "origin"),
            "path": str(AGGREGATOR),
            "commit": git(AGGREGATOR, "rev-parse", "HEAD"),
            "working_tree_clean": not git(AGGREGATOR, "status", "--porcelain"),
            "sources": [receipt(AGGREGATOR, name, needle) for name, needle in aggregator_sources.items()],
        },
        "required_endpoints_present": required,
        "audio_speech_endpoint_present": "/v1/audio/speech" in spec["paths"],
        "chat_completions_endpoint_present": "/v1/chat/completions" in spec["paths"],
        "responses_endpoint_present": "/v1/responses" in spec["paths"],
        "task_types": spec["components"]["schemas"]["ModelUsage"]["properties"]["task_type"]["enum"],
        "job_states": machine["states"],
        "terminal_states": machine["terminal_states"],
        "integration_code_implemented": False,
        "script_actions": {
            "scope": "this static inspection invocation only; native probes are recorded separately",
            "node_dependencies_installed": False,
            "application_built_or_started": False,
            "browser_used": False,
            "live_api_called": False,
            "production_modified": False,
            "virtualization_used": False,
        },
    }
    target = PROJECT / "docs/research" / f"aggregator-integration-audit-{now.date().isoformat()}.json"
    if target.exists():
        target = target.with_name(f"{target.stem}-{now.strftime('%H%M%S%f')}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "tracked_files": len(tracked), "endpoints": len(required), "scope": report["evidence_level"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
