"""Index assessment evidence and numbered gaps without executing product code."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
CANVAS = ROOT / "infinite-canvas"
SERVICE = ROOT.parent / "生图生视API封装"
OUT = ROOT / "docs/research/fusion-assessment-2026-10-30"


def git(directory: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, encoding="utf-8").stdout.strip()


def receipt(directory: Path, name: str, needle: str | None = None) -> dict:
    source = directory / name
    data = source.read_bytes()
    result = {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if needle:
        lines = [n for n, value in enumerate(data.decode("utf-8").splitlines(), 1) if needle in value]
        if not lines:
            raise ValueError(f"Missing source anchor: {name}: {needle}")
        result.update(anchor=needle, lines=lines)
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    assessment = ROOT / "docs/06-融合可行性评估与上线约束.md"
    verification = ROOT / "docs/08-可用性验证与发布验收.md"
    rows = []
    for line in assessment.read_text(encoding="utf-8").splitlines():
        if re.match(r"\| F-\d{2} \|", line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) != 5:
                raise ValueError(f"Malformed gap row: {line}")
            rows.append(dict(zip(["id", "scope", "current_evidence_or_gap", "completion_condition", "priority"], cells)))
    expected = [f"F-{i:02d}" for i in range(1, 47)]
    if [row["id"] for row in rows] != expected:
        raise ValueError("Gap numbering changed or incomplete.")
    gate_ids = re.findall(r"^\| (G-\d{2}) \|", verification.read_text(encoding="utf-8"), flags=re.M)
    if gate_ids != [f"G-{i:02d}" for i in range(1, 28)]:
        raise ValueError("Release gate numbering changed or incomplete.")

    documents = ["README.md", "docs/README.md", "docs/04-本地源码与中转服务融合方案.md", "docs/05-中转服务接口映射与开发验收清单.md", "docs/06-融合可行性评估与上线约束.md", "docs/07-融合实现契约与故障恢复.md", "docs/08-可用性验证与发布验收.md", "docs/09-模型适配样本与用户流程.md"]
    checked_links = 0
    for name in documents:
        file = ROOT / name
        for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", file.read_text(encoding="utf-8")):
            target = target.strip("<>")
            if re.match(r"(?:[a-z]+:|#)", target, flags=re.I):
                continue
            path = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if path:
                if not (file.parent / path).exists():
                    raise ValueError(f"Broken local file link: {name} -> {target}")
                checked_links += 1

    with (OUT / "development-gaps.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    canvas_sources = {
        "web/package.json": None,
        "web/package-lock.json": None,
        "web/src/components/layout/app-providers.tsx": "ProConfigProvider",
        "web/src/components/layout/model-script-editor.tsx": 'content: { height: "100dvh",',
        "web/src/stores/canvas/use-canvas-store.ts": "void localForageStorage.setItem",
        "web/src/lib/localforage-storage.ts": "window.localStorage.setItem",
        "web/src/lib/canvas/plugin-loader.ts": "async function loadLocalPlugins()",
        "web/src/services/api/model-plugin.ts": "new Function(",
        "web/src/components/layout/client-root-init.tsx": "apiKey",
        "web/src/services/config-file.ts": "config, webdav, promptSources",
        "web/src/components/canvas/canvas-toolbar.tsx": "left: 300",
        "web/src/lib/utils.ts": "nanoid",
        "web/vite.config.ts": 'base: process.env.VITE_BASE || "/"',
        "web/src/router.tsx": "createBrowserRouter([",
    }
    service_sources = {
        "app/packages/infrastructure/src/generation_infrastructure/auth.py": "consume_business_rates(session, tenant, project, key)",
        "app/packages/infrastructure/src/generation_infrastructure/jobs.py": "digest = request_fingerprint(request, asset_hashes)",
        "app/apps/control_api/src/control_api/uploads.py": "Idempotency-Key",
        "app/apps/control_api/src/control_api/main.py": 'path="/admin"',
        "app/packages/contracts/src/generation_contracts/validation.py": "class JobCreate",
        "app/apps/control_api/src/control_api/monitoring.py": 'allow_origin_regex=".*"',
        "app/config/openapi.json": None,
        "app/config/lime-reference.lock.json": None,
        "docs/121-ComfyUI原始产物移交即清理设计.md": "当前两项自动化均尚未实现",
        "docs/123-空闲资源释放与多设备模型池调度设计.md": "balanced_ready",
    }
    reports = []
    for file in sorted(OUT.glob("*.json")):
        if file.name == "assessment-summary.json":
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        entry = receipt(ROOT, file.relative_to(ROOT).as_posix())
        for key in ["scope", "stage", "passed", "exit_code", "case_count", "passed_count", "baseline_routes_and_reload_passed", "all_viewport_controls_passed", "creation_entry", "interrupted_by_assessment", "adopted", "verification_note", "cleanup"]:
            if key in data:
                entry[key] = data[key]
        reports.append(entry)

    browser_records = [json.loads(file.read_text(encoding="utf-8")) for file in OUT.glob("native-browser*.json")]
    screenshot_count = 0
    for record in browser_records:
        if not all(record.get("cleanup", {}).get(key) for key in ["contexts_closed", "browser_closed", "server_closed"]):
            raise ValueError("A browser run has unfinished cleanup.")
        for page in record.get("pages", []):
            if page.get("screenshot"):
                digest = hashlib.sha256((ROOT / page["screenshot"]).read_bytes()).hexdigest()
                if digest != page["screenshot_sha256"]:
                    raise ValueError(f"Screenshot changed: {page['screenshot']}")
                screenshot_count += 1
    primary_browser = json.loads((OUT / "native-browser-normal-diagnostic-closed-idb.json").read_text(encoding="utf-8"))
    offline = json.loads((OUT / "offline-contracts.json").read_text(encoding="utf-8"))
    documented = json.loads((OUT / "documented-requests.json").read_text(encoding="utf-8"))

    summary = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "assessment_complete": True,
        "integration_implemented": False,
        "release_ready": False,
        "release_decision": "HOLD: adapter implementation and all applicable live release gates remain unsigned",
        "scope": "local assessment evidence index; native baseline and offline contracts are not integrated acceptance",
        "gap_count": len(rows),
        "gap_priority_counts": {kind: sum(row["priority"] == kind for row in rows) for kind in ["P0", "P1"]},
        "release_gate_count": len(gate_ids),
        "signed_off_release_gate_count": 0,
        "verification": {
            "unmodified_upstream_ci_passed": False,
            "patched_native_install_passed": all(json.loads((OUT / name).read_text(encoding="utf-8"))["passed"] for name in ["native-install-005103.json", "native-native-bindings-014101.json"]),
            "patched_native_typecheck_passed": json.loads((OUT / "native-typecheck-021113.json").read_text(encoding="utf-8"))["passed"],
            "patched_native_build_passed": json.loads((OUT / "native-build-014155.json").read_text(encoding="utf-8"))["passed"],
            "formal_clean_ci_executed": False,
            "normal_http_project_routes_and_reload_passed": primary_browser["baseline_routes_and_reload_passed"],
            "mobile_controls_passed": primary_browser["all_viewport_controls_passed"],
            "offline_contracts_passed": offline["passed"],
            "offline_contract_count": offline["case_count"],
            "documented_request_examples_passed": sum(check.get("passed", False) for check in documented["checks"]),
            "browser_runs_closed": len(browser_records),
            "screenshot_hashes_verified": screenshot_count,
            "live_generation_executed": False,
            "real_phones_tested": False,
        },
        "development_gaps": rows,
        "release_gate_ids": gate_ids,
        "checked_local_file_links": checked_links,
        "canvas": {"commit": git(CANVAS, "rev-parse", "HEAD"), "working_tree_clean": not git(CANVAS, "status", "--porcelain"), "sources": [receipt(CANVAS, name, needle) for name, needle in canvas_sources.items()]},
        "aggregator": {"commit": git(SERVICE, "rev-parse", "HEAD"), "working_tree_clean": not git(SERVICE, "status", "--porcelain"), "sources": [receipt(SERVICE, name, needle) for name, needle in service_sources.items()]},
        "documents": [receipt(ROOT, name) for name in documents],
        "reports": reports,
        "production_modified": False,
        "live_generation_called": False,
        "local_virtualization_used": False,
    }
    target = OUT / "assessment-summary.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(target), "gaps": len(rows), "priorities": summary["gap_priority_counts"], "release_gates": len(gate_ids), "local_links": checked_links, "canvas_clean": summary["canvas"]["working_tree_clean"], "aggregator_clean": summary["aggregator"]["working_tree_clean"], "reports": len(reports), "release_ready": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
