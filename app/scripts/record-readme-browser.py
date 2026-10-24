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
    report: dict[str, Any] = {"passed": False, "synthetic_data": True, "real_comfyui": False, "screenshots": [], "recordings": [], "errors": [], "closed_contexts": 0, "browser_closed": False}
    responses: list[dict[str, Any]] = []
    contexts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        report["browser_version"] = browser.version
        try:
            def context(*, mobile: bool = False, clip: str | None = None) -> Any:
                options: dict[str, Any] = {"ignore_https_errors": True, "viewport": {"width": 390 if mobile else 1440, "height": 844 if mobile else 960}, "timezone_id": "Asia/Taipei", "reduced_motion": "reduce"}
                if clip:
                    options.update(record_video_dir=str(private / clip), record_video_size={"width": 1440, "height": 960})
                result = browser.new_context(**options)
                contexts.append(result)
                login = result.request.post(origin + "/admin/api/v1/session", data={"username": "demo-admin", "password": value["password"]}, headers={"Origin": origin})
                assert login.status == 200, "demo_login_failed"
                return result

            def visit(page: Any, route: str, title: str, screenshot: str | None = None) -> None:
                page.goto(origin + "/admin/" + route, wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible(timeout=20000)
                page.wait_for_timeout(1100)
                if screenshot:
                    page.screenshot(path=str(media / (screenshot + ".png")), full_page=False)
                    report["screenshots"].append(screenshot + ".png")

            tour = context(clip="tour")
            page = tour.new_page()
            page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
            for route, title, shot in [("", "运行总览", "overview"), ("channels", "Windows 渠道", "channels"), ("worker-nodes", "Windows 节点", "nodes"), ("jobs", "任务管理", "queue"), ("workflows", "Workflow 管理", "workflows"), ("models", "模型与路由", "models"), ("keys", "API 密钥", "api-keys"), ("content", "内容管理", "content")]:
                visit(page, route, title, shot)
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
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            mobile.close()
            report["closed_contexts"] += 1
            report["passed"] = not report["errors"]
        finally:
            for item in contexts:
                item.close()
            browser.close()
            report["browser_closed"] = True
            (media / "capture.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (media / "api-responses.json").write_text(json.dumps({"synthetic_data": True, "authentication_headers_and_cookies_omitted": True, "responses": responses}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert report["passed"]


if __name__ == "__main__":
    main()
