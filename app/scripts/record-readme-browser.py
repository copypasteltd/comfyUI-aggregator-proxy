"""Capture owned Edge contexts only; credentials never enter a recorded page."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.fixture.read_text(encoding="utf-8"))
    origin, media, private = value["origin"], Path(value["media"]), Path(value["private"])
    report: dict[str, Any] = {"passed": False, "synthetic_data": True, "real_comfyui": False, "screenshots": [], "recordings": [], "errors": [], "closed_contexts": 0, "browser_closed": False, "visual_theme": "04-lime-source", "route_checks": [], "layout_checks": []}
    responses: list[dict[str, Any]] = []
    contexts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        report["browser_version"] = browser.version
        try:
            def context(*, mobile: bool = False, clip: str | None = None, login_shot: bool = False) -> Any:
                options: dict[str, Any] = {"ignore_https_errors": True, "viewport": {"width": 390 if mobile else 1440, "height": 844 if mobile else 960}, "timezone_id": "Asia/Taipei", "reduced_motion": "reduce"}
                if clip:
                    options.update(record_video_dir=str(private / clip), record_video_size={"width": 1440, "height": 960})
                result = browser.new_context(**options)
                contexts.append(result)
                if login_shot:
                    anonymous = result.new_page()
                    try:
                        anonymous.goto(origin + "/admin/", wait_until="domcontentloaded")
                        expect(anonymous.get_by_role("heading", name="登录管理后台", exact=True)).to_be_visible()
                        anonymous.evaluate("document.fonts.ready")
                        anonymous.screenshot(path=str(media / "login.png"))
                        report["screenshots"].append("login.png")
                    finally:
                        anonymous.close()
                login = result.request.post(origin + "/admin/api/v1/session", data={"username": "demo-admin", "password": value["password"]}, headers={"Origin": origin})
                assert login.status == 200, "demo_login_failed"
                return result

            def visit(page: Any, route: str, title: str, screenshot: str | None = None) -> None:
                page.goto(origin + "/admin/" + route, wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible(timeout=20000)
                page.wait_for_timeout(1100)
                page.evaluate("document.fonts.ready")
                assert page.evaluate("document.documentElement.dataset.c === 'lime'")
                expect(page.get_by_text("服务处理失败", exact=True)).to_have_count(0)
                if screenshot:
                    page.screenshot(path=str(media / (screenshot + ".png")), full_page=False)
                    report["screenshots"].append(screenshot + ".png")

            def layout(page: Any, route: str) -> None:
                measured = page.evaluate("({ viewport: innerWidth, document: document.documentElement.scrollWidth })")
                report["layout_checks"].append({"route": route, **measured})
                assert measured["document"] <= measured["viewport"], "page_overflow:" + route

            tour = context(clip="tour", login_shot=True)
            page = tour.new_page()
            page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
            external_requests: set[str] = set()
            page.on("request", lambda request: external_requests.add("external_request") if request.url.startswith(("http:", "https:")) and not request.url.startswith(origin + "/") else None)
            access_route = ""
            for route, title, shot in [("", "运行总览", "overview"), ("channels", "Windows 渠道", "channels"), ("worker-nodes", "Windows 节点", "nodes"), ("jobs", "任务管理", "queue"), ("workflows", "Workflow 管理", "workflows"), ("models", "模型与路由", "models"), ("keys", "API 密钥", "api-keys"), ("content", "内容管理", "content")]:
                visit(page, route, title, shot)
                layout(page, route)
                report["route_checks"].append(route or "/")
                if route == "":
                    assert page.locator(".li-tk .li-d").count() == 4
                    assert page.evaluate('document.fonts.check(\'54px "Anton"\') && document.fonts.check(\'14px "Manrope"\')')
                    report["source_fonts_loaded"] = True
                if route == "keys":
                    href = page.locator('a[href$="/access"]').first.get_attribute("href")
                    assert href and href.startswith("/admin/keys/")
                    access_route = href.removeprefix("/admin/")
            remaining = [("spec-isolations", "规格隔离"), ("model-artifacts", "模型文件目录"), ("scopes", "租户与项目"), ("gateways", "网关接入"), ("operations", "操作中心"), ("alerts", "运行告警"), ("audit", "审计事件"), ("users", "管理账号"), (access_route, "Key 访问记录")]
            for route, title in remaining:
                visit(page, route, title)
                layout(page, "keys/:identifier/access" if route == access_route else route)
                report["route_checks"].append("keys/:identifier/access" if route == access_route else route)
            assert not external_requests, "admin_made_external_request"
            report["runtime_external_requests"] = 0
            page.set_viewport_size({"width": 1280, "height": 600})
            last_link = page.get_by_role("navigation", name="主导航").get_by_role("link").last
            last_link.focus()
            page.keyboard.press("Tab")
            last_link.focus()
            assert last_link.evaluate("el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && getComputedStyle(el).outlineStyle !== 'none' }")
            report["short_screen_keyboard_navigation"] = True
            tour.close()
            report["closed_contexts"] += 1
            page.video.save_as(str(private / "admin-tour.webm"))
            report["recordings"].append("admin-tour.webm")

            lifecycle = context(clip="lifecycle")
            page = lifecycle.new_page()
            page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
            visit(page, "workflows", "Workflow 管理")
            page.get_by_role("textbox", name="Workflow 名称搜索", exact=True).fill("音频实验 · DEMO")
            page.get_by_role("button", name="应用筛选", exact=True).click()
            row = page.locator(".el-table__body-wrapper .el-table__row").filter(has_text="音频实验 · DEMO")
            expect(row).to_have_count(1)
            for archived in (True, False):
                row.get_by_role("button", name="归档" if archived else "解除归档", exact=True).click()
                dialog = page.get_by_role("dialog", name="Workflow 状态变更", exact=True)
                expect(dialog).to_be_visible()
                page.wait_for_timeout(1300)
                if archived:
                    page.screenshot(path=str(media / "workflow-confirmation.png"))
                    report["screenshots"].append("workflow-confirmation.png")
                with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/lifecycle")) as pending:
                    dialog.get_by_role("button", name="确认归档" if archived else "确认解除归档", exact=True).click()
                response = pending.value
                assert response.status == 200
                responses.append({"operation": "archive" if archived else "unarchive", "http_status": response.status, "request": response.request.post_data_json, "response": response.json()})
                expect(row.get_by_text("已归档" if archived else "未归档", exact=True)).to_be_visible()
                page.wait_for_timeout(1300)
            row.get_by_role("button", name="发布历史", exact=True).click()
            history = page.get_by_role("dialog", name="Workflow 发布历史", exact=True)
            expect(history).to_be_visible()
            page.wait_for_timeout(1400)
            page.screenshot(path=str(media / "workflow-history.png"))
            report["screenshots"].append("workflow-history.png")
            history.get_by_role("button", name="关闭历史", exact=True).click()
            lifecycle.close()
            report["closed_contexts"] += 1
            page.video.save_as(str(private / "workflow-lifecycle.webm"))
            report["recordings"].append("workflow-lifecycle.webm")

            mobile = context(mobile=True)
            page = mobile.new_page()
            visit(page, "", "运行总览", "mobile-overview")
            layout(page, "/")
            page.locator(".metric-grid").scroll_into_view_if_needed()
            page.screenshot(path=str(media / "mobile-queue.png"))
            report["screenshots"].append("mobile-queue.png")
            mobile_routes = [("", "运行总览"), ("channels", "Windows 渠道"), ("worker-nodes", "Windows 节点"), ("jobs", "任务管理"), ("workflows", "Workflow 管理"), ("models", "模型与路由"), ("keys", "API 密钥"), ("content", "内容管理"), *remaining]
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                for route, title in mobile_routes:
                    visit(page, route, title)
                    layout(page, "keys/:identifier/access" if route == access_route else route or "/")
            report["reduced_motion"] = page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
            mobile.close()
            report["closed_contexts"] += 1
            report["passed"] = not report["errors"]
        finally:
            closed = 0
            for item in contexts:
                item.close()
                closed += 1
            report["closed_contexts"] = closed
            browser.close()
            report["browser_closed"] = True
            (media / "capture.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (media / "api-responses.json").write_text(json.dumps({"synthetic_data": True, "authentication_headers_and_cookies_omitted": True, "responses": responses}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert report["passed"]


if __name__ == "__main__":
    main()
