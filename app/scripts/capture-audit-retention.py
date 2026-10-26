"""Capture the built Lime Admin with explicit audit UI fixtures and owned Edge."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def worker(fixture: Path) -> None:
    from audit_retention_browser import retention_geometry
    from playwright.sync_api import expect, sync_playwright

    values = json.loads(fixture.read_text(encoding="utf-8"))
    public, private = Path(values["public"]), Path(values["private"])
    dist = ROOT / "apps/admin_web/dist"
    now = int(time.time())
    policy = {"enabled": False, "retention_days": None, "batch_limit": 500, "snapshot_at": now, "last_batch": None}
    report = {"passed": False, "checks": [], "errors": [], "screenshots": [], "observations": [], "closed_contexts": 0, "browser_closed": False}
    contexts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            # A context closes before the next viewport; only one state is active.
            state = {"value": "disabled"}
            for name, width, height in (("desktop", 1360, 940), ("mobile", 390, 844), ("narrow", 320, 844)):
                context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce" if name == "narrow" else "no-preference", **({"record_video_dir": str(private / "video"), "record_video_size": {"width": width, "height": height}} if name == "mobile" else {}))
                contexts.append(context)
                state["value"] = "disabled"

                def serve(route):
                    url = urlsplit(route.request.url)
                    path = unquote(url.path)
                    if url.netloc != "render-ui.invalid":
                        route.abort()
                        report["errors"].append("unexpected_network_origin")
                    elif path == "/admin/api/v1/session":
                        route.fulfill(json={"user": {"id": "usr_synthetic", "username": "合成界面演示", "role": "observer", "revision": 1, "is_active": True}, "csrf_token": "synthetic-fixture-only", "permissions": ["audit:read"], "scopes": [], "lifetime": None})
                    elif path == "/admin/api/v1/audit-retention":
                        if state["value"] in ("failed", "stale"):
                            route.fulfill(status=503, json={"error": {"message": "合成读取失败，请刷新重试", "type": "server_error", "code": "dependency_unavailable"}})
                        else:
                            route.fulfill(json={**policy, **({"enabled": True, "retention_days": 3650, "last_batch": {"completed_at": now - 60, "cutoff": now - 3650 * 86400, "deleted": 500, "retention_days": 3650}} if state["value"] == "enabled" else {})})
                    elif path == "/admin/api/v1/audit-events":
                        route.fulfill(json={"data": [], "page": 1, "has_more": False, "total": 0, "snapshot_at": now})
                    elif path.startswith("/admin/api/"):
                        route.fulfill(status=404, json={"error": {"message": "unsupported UI fixture"}})
                    else:
                        file = (dist / path.removeprefix("/admin/")).resolve()
                        if not file.is_relative_to(dist.resolve()):
                            route.abort()
                            report["errors"].append("unsafe_static_path")
                            return
                        if not file.is_file():
                            file = dist / "index.html"
                        route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")

                context.route("**/*", serve)
                page = context.new_page()
                page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
                page.goto("https://render-ui.invalid/admin/audit", wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="审计事件", exact=True)).to_be_visible()
                page.evaluate("document.fonts.ready")
                assert page.evaluate("document.documentElement.dataset.c === 'lime' && document.fonts.check('54px Anton') && document.fonts.check('14px Manrope')")
                report["checks"].append(name + "_original_lime_fonts_and_theme")
                for variant in ("disabled", "enabled", "stale", "failed"):
                    state["value"] = variant
                    if variant == "failed":
                        page.reload(wait_until="domcontentloaded")
                        expect(page.get_by_text("尚未取得保留状态。", exact=True)).to_be_visible()
                    elif variant != "disabled":
                        page.get_by_role("button", name="刷新记录", exact=True).click()
                    if variant in ("stale", "failed"):
                        expect(page.get_by_text("保留状态读取失败", exact=True)).to_be_visible()
                    if variant != "failed":
                        expected = "自动清理未启用" if variant == "disabled" else "已配置保留 3650 天"
                        expect(page.locator(".audit-retention .el-tag")).to_have_text(expected)
                        metrics = retention_geometry(page)
                        report["observations"].append({"viewport": width, "state": variant, **metrics})
                        if variant == "stale":
                            expect(page.get_by_text("上次成功状态", exact=False)).to_be_visible()
                    else:
                        assert not page.locator(".audit-retention .el-tag").count()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
                    report["checks"].append(name + "_" + variant + "_complete_policy_text")
                    if name == "mobile":
                        page.wait_for_timeout(900)
                    if (name, variant) in (("desktop", "enabled"), ("mobile", "disabled"), ("narrow", "enabled"), ("narrow", "failed")):
                        image = public / (name + "-" + variant + ".png")
                        page.screenshot(path=str(image), full_page=True, animations="disabled")
                        report["screenshots"].append({"path": image.name, "sha256": hashlib.sha256(image.read_bytes()).hexdigest()})
                field = page.get_by_label("操作主体 ID", exact=True)
                field.focus()
                assert field.evaluate("el => el === document.activeElement")
                assert field.evaluate("el => getComputedStyle(el.closest('.el-input__wrapper')).boxShadow !== 'none'")
                report["checks"].append(name + "_keyboard_focus_visible")
                if name == "narrow":
                    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
                    report["checks"].append("narrow_reduced_motion")
                context.close()
                contexts.pop()
                report["closed_contexts"] += 1
            assert not report["errors"]
            report["passed"] = True
        finally:
            for context in reversed(contexts):
                context.close()
                report["closed_contexts"] += 1
            browser.close()
            report["browser_closed"] = True
            (public / "browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    import native_ci as ci

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-id")
    mode.add_argument("--fixture", type=Path)
    parser.add_argument("--browser-python", type=Path, default=Path(sys.base_prefix) / "python.exe")
    args = parser.parse_args()
    if args.fixture:
        worker(args.fixture)
        return
    identifier = ci.run_id(args.run_id)
    private = ROOT / ".local/audit-retention-browser" / identifier
    public = ROOT.parent / "docs/evidence/2026-10-26" / identifier
    private.mkdir(parents=True, exist_ok=False)
    public.mkdir(parents=True, exist_ok=False)
    sources = ci.snapshot()
    fixture = private / "fixture.json"
    fixture.write_text(json.dumps({"public": str(public), "private": str(private)}), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith(("GEN_", "PYTHON"))}
    report = {"format_version": 1, "at": datetime.now(UTC).isoformat(), "scope": "Built Admin with explicit UI API fixtures; no production API, database, ComfyUI or external network", "source_sha256": sources, "real_resource_acceptance": False, "full_acceptance": "pending", "virtualization_used": False}
    gate = ci.Gate("audit_retention_browser", [str(args.browser_python.resolve(strict=True)), "-X", "utf8", str(Path(__file__).resolve()), "--fixture", str(fixture)], ROOT, 120)
    report["browser_process"] = ci.execute(gate, private / "browser.log", environment, limit=2 * 1024**2)
    browser_report = public / "browser.json"
    if browser_report.is_file():
        report.update(json.loads(browser_report.read_text(encoding="utf-8")))
    report["sources_unchanged"] = ci.snapshot() == sources
    fixture.unlink()
    report["cleanup"] = {"owned_process_tree": report["browser_process"]["owned_process_tree_closed"], "fixture": not fixture.exists()}
    report["passed"] = bool(report.get("passed") and report["browser_process"]["status"] == "passed" and report["sources_unchanged"] and all(report["cleanup"].values()))
    (public / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report.get("checks", [])), "closed_contexts": report.get("closed_contexts", 0)}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
