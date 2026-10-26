"""Exercise actual queue-estimate requests in owned Edge contexts; record synthetic demo pages only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    origin, public, private = fixture["origin"], Path(fixture["public"]), Path(fixture["private"])
    report: dict[str, Any] = {"passed": False, "checks": [], "screenshots": [], "responses": [], "closed_contexts": 0, "browser_closed": False, "visual_theme": "04-lime-source", "synthetic_data": True}
    contexts = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        report["browser_version"] = browser.version
        try:
            for width in (1440, 768, 390):
                context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 960 if width > 390 else 844}, locale="zh-CN", timezone_id="Asia/Taipei", reduced_motion="reduce",
                    **({"record_video_dir": str(private / "video"), "record_video_size": {"width": 1440, "height": 960}} if width == 1440 else {}))
                contexts.append(context)
                assert context.request.post(origin + "/admin/api/v1/session", data={"username": "demo-observer", "password": fixture["password"]}, headers={"Origin": origin}).status == 200
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                page.goto(origin + "/admin/jobs", wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="任务管理", exact=True)).to_be_visible(timeout=20000)
                page.get_by_role("button", name="合成演示 · 已有运行样本", exact=True).click()
                panel = page.get_by_role("region", name="预计可分配等待", exact=True)
                expect(panel).to_be_visible()
                assert panel.get_by_text("暂无法估算", exact=True).count() == 0
                with page.expect_response(lambda response: response.url.endswith("/wait-estimate")) as response:
                    panel.get_by_role("button", name="估算等待", exact=True).click()
                value = response.value.json()
                assert response.value.status == 200 and value["status"] == "estimated" and value["minimum_route_samples"] >= 10
                expect(panel.locator(".wait-interval")).to_be_visible()
                assert page.evaluate("document.documentElement.dataset.c === 'lime'")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.evaluate("document.fonts.ready")
                panel.scroll_into_view_if_needed()
                path = public / f"wait-estimate-{width}.png"
                page.screenshot(path=str(path), full_page=False)
                report["screenshots"].append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                report["checks"].append(f"actual_https_estimate_and_layout_{width}")
                report["responses"].append(value)
                if width == 1440:
                    button = panel.get_by_role("button", name="重新估算", exact=True)
                    button.focus()
                    assert button.evaluate("element => element === document.activeElement")
                    report["checks"].append("keyboard_focus")
                    expect(panel.get_by_text("估算已过期", exact=True)).to_be_visible(timeout=17000)
                    expect(panel.locator(".wait-interval")).to_have_count(0)
                    report["checks"].append("expired_estimate_hides_numeric_interval")
                page.locator(".el-drawer__close-btn:visible").click()
                page.get_by_role("button", name="合成演示 · 样本尚不足", exact=True).click()
                panel = page.get_by_role("region", name="预计可分配等待", exact=True)
                with page.expect_response(lambda response: response.url.endswith("/wait-estimate")) as response:
                    panel.get_by_role("button", name="估算等待", exact=True).click()
                assert response.value.json()["status"] == "unknown" and response.value.json()["reason"] == "insufficient_samples"
                expect(panel.get_by_text("暂无法估算", exact=True)).to_be_visible()
                expect(panel.locator(".wait-interval")).to_have_count(0)
                panel.scroll_into_view_if_needed()
                path = public / f"wait-unknown-{width}.png"
                page.screenshot(path=str(path), full_page=False)
                report["screenshots"].append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                report["checks"].append(f"actual_https_insufficient_samples_{width}")
                if width == 1440:
                    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
                    page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
                    expect(panel.get_by_text("暂无法估算", exact=True)).to_have_count(0)
                    expect(panel.get_by_role("button", name="估算等待", exact=True)).to_be_visible()
                    page.evaluate("delete document.hidden")
                    report["checks"].append("visibility_event_clears_snapshot_and_reduced_motion")
                    held = []
                    pattern = "**/wait-estimate"
                    page.route(pattern, lambda route, _request, held=held: held.append(route))
                    panel.get_by_role("button", name="估算等待", exact=True).click()
                    expect(panel.locator("button.is-loading")).to_be_visible()
                    page.locator(".el-drawer__close-btn:visible").click()
                    page.wait_for_timeout(50)
                    assert len(held) == 1
                    held[0].continue_()
                    page.unroute(pattern)
                    page.get_by_role("button", name="合成演示 · 已有运行样本", exact=True).click()
                    panel = page.get_by_role("region", name="预计可分配等待", exact=True)
                    expect(panel.locator(".wait-interval")).to_have_count(0)
                    expect(panel.locator("button.is-loading")).to_have_count(0)
                    report["checks"].append("closed_drawer_aborts_request_and_new_job_has_no_stale_result")
                page.locator(".el-drawer__close-btn:visible").click()
                page.get_by_role("button", name="退出登录", exact=True).click()
                expect(page.get_by_role("heading", name="登录管理后台", exact=True)).to_be_visible()
                page.close()
                context.close()
                contexts.remove(context)
                report["closed_contexts"] += 1
            assert not errors
            report["passed"] = True
        finally:
            for context in contexts:
                context.close()
                report["closed_contexts"] += 1
            browser.close()
            report["browser_closed"] = True
            report["errors"] = errors
            (public / "browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
