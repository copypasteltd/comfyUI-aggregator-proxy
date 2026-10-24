"""Owned Edge contexts against real retry rows and a native HTTPS API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    fixture = json.loads(parser.parse_args().fixture.read_text(encoding="utf-8"))
    origin = fixture["origin"]
    report = {"passed": False, "checks": [], "errors": [], "closed_contexts": 0, "browser_closed": False, "mocked_api_responses": False}
    contexts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            for name, width, height in (("desktop", 1360, 1000), ("mobile", 390, 844)):
                context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height}, timezone_id="Asia/Taipei")
                contexts.append(context)
                page = context.new_page()
                page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
                page.goto(origin + "/admin/", wait_until="domcontentloaded")
                page.get_by_label("用户名", exact=True).fill("retry-browser")
                page.get_by_label("密码", exact=True).fill(fixture["password"])
                page.get_by_role("button", name="登录", exact=True).click()
                expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
                metric = page.locator("section.metric").filter(has=page.get_by_text("等待内部重试", exact=True))
                expect(metric.locator("strong")).to_have_text("2")
                snapshot = context.request.get(origin + "/admin/api/v1/queues/snapshot").json()
                assert snapshot["states"]["retry_wait"] == 2 and snapshot["global_unassigned_queued"] == 0 and snapshot["occupied_physical_gpus"] == 0
                report["checks"].append(name + "_live_overview_retry_is_unfinished_without_ready_or_gpu_count")
                identifier = fixture["jobs"][0 if name == "desktop" else 1]
                page.goto(origin + "/admin/jobs?job=" + identifier, wait_until="domcontentloaded")
                dialog = page.get_by_role("dialog", name="任务详情", exact=True)
                expect(dialog.get_by_text("提交前连接暂时失败", exact=True)).to_be_visible()
                expect(dialog.get_by_text("1 / 2", exact=True)).to_be_visible()
                expect(dialog.get_by_text("可再次分配时间", exact=True)).to_be_visible()
                expect(dialog.get_by_text("已释放 / 已关闭", exact=True)).to_be_visible()
                report["checks"].append(name + "_live_detail_retry_count_reason_server_time_and_closed_attempt")
                if name == "desktop":
                    before = context.request.get(origin + "/admin/api/v1/jobs/" + identifier).json()
                    dialog.get_by_label("优先级", exact=True).fill("31")
                    dialog.get_by_role("button", name="保存优先级", exact=True).click()
                    expect(page.get_by_text("优先级已保存", exact=True)).to_be_visible()
                    after = context.request.get(origin + "/admin/api/v1/jobs/" + identifier).json()
                    assert after["priority"] == 31 and after["status"] == "retry_wait" and after["retry"] == before["retry"]
                    report["checks"].append("desktop_retry_wait_priority_keeps_job_backoff_and_identity")
                    session = context.request.get(origin + "/admin/api/v1/session").json()
                    path = origin + "/admin/api/v1/jobs/" + identifier + "/priority"
                    request = {"expected_revision": before["updated_revision"], "priority": 12}
                    headers = {"Origin": origin, "If-Match": '"' + str(before["updated_revision"]) + '"', "Idempotency-Key": "synthetic-stale-retry-priority"}
                    assert context.request.patch(path, data=request, headers=headers).status == 403
                    headers["X-CSRF-Token"] = session["csrf_token"]
                    assert context.request.patch(path, data=request, headers=headers).status == 409
                    report["checks"].append("desktop_priority_csrf_and_stale_revision_are_enforced")
                else:
                    dialog.get_by_role("button", name="请求取消", exact=True).click()
                    page.get_by_role("button", name="确认", exact=True).click()
                    expect(page.get_by_text("取消请求已记录", exact=True)).to_be_visible()
                    result = context.request.get(origin + "/admin/api/v1/jobs/" + identifier).json()
                    assert result["status"] == "cancelled" and result["retry"]["count"] == 1 and result["retry"]["next_at"] is None
                    expect(dialog.get_by_role("button", name="保存优先级", exact=True)).to_have_count(0)
                    report["checks"].append("mobile_retry_wait_cancel_is_terminal_and_clears_schedule")
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                    report["checks"].append("mobile_retry_detail_has_no_document_overflow")
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
