"""Owned Edge checks against the freshly installed candidate, never the dev API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    report = {"passed": False, "checks": [], "errors": [], "closed_contexts": 0, "browser_closed": False}
    contexts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            for name, width, height in (("desktop", 1360, 1000), ("mobile", 390, 844)):
                context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
                contexts.append(context)
                page = context.new_page()
                page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
                page.goto(fixture["origin"] + "/admin/", wait_until="domcontentloaded")
                page.get_by_label("用户名", exact=True).fill("release-test")
                page.get_by_label("密码", exact=True).fill(fixture["password"])
                page.get_by_role("button", name="登录", exact=True).click()
                expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
                assert context.request.get(fixture["origin"] + "/admin/api/v1/session").status == 200
                report["checks"].append(name + "_installed_admin_login_and_live_queue")
                page.evaluate("document.fonts.ready")
                assert page.evaluate("document.documentElement.dataset.c === 'lime'")
                assert page.locator(".li-tk .li-d").count() == 4
                assert page.evaluate('document.fonts.check(\'54px "Anton"\') && document.fonts.check(\'14px "Manrope"\')')
                licenses = context.request.get(fixture["origin"] + "/admin/licenses/fonts.html")
                assert licenses.status == 200 and all(word in licenses.text() for word in ("Anton", "Manrope", "SIL OPEN FONT LICENSE"))
                report["checks"].append(name + "_installed_lime_source_fonts_and_offline_licenses")
                expect(page.locator(".statistics-panel .history-metrics")).to_be_visible()
                statistics = context.request.get(fixture["origin"] + "/admin/api/v1/queues/statistics?window=1h&task_type=text_to_audio")
                assert statistics.status == 200
                value = statistics.json()
                assert value["task_type"] == "text_to_audio" and len(value["buckets"]) == 12
                assert value["totals"]["completed"] == 0 and value["success_rate_bps"] is None and value["successful_duration"]["p95_ms"] is None
                assert page.locator(".statistics-panel").get_by_text("未知", exact=True).count() >= 1
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                report["checks"].append(name + "_installed_statistics_audio_empty_unknown_and_responsive")
                page.goto(fixture["origin"] + "/admin/content", wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="内容管理", exact=True)).to_be_visible()
                assert context.request.get(fixture["origin"] + "/admin/api/v1/content").status == 200
                if name == "mobile":
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                report["checks"].append(name + "_installed_content_route_and_authorization")
                page.goto(fixture["origin"] + "/admin/audit", wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="审计事件", exact=True)).to_be_visible()
                audit = context.request.get(fixture["origin"] + "/admin/api/v1/audit-events?action=admin.login")
                assert audit.status == 200 and audit.json()["total"] >= 1
                expect(page.get_by_label("动作编码", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                report["checks"].append(name + "_installed_audit_query_and_responsive_filters")
                page.screenshot(path=fixture["screenshot_" + name], full_page=True)
            assert not report["errors"]
            report["passed"] = True
        finally:
            for context in reversed(contexts):
                context.close()
                report["closed_contexts"] += 1
            browser.close()
            report["browser_closed"] = True
            Path(fixture["report"]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
