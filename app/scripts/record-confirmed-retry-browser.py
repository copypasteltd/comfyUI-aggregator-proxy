"""Capture actual Admin OOM retry rows; authenticate through HTTP outside the UI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    fixture = json.loads(parser.parse_args().fixture.read_text(encoding="utf-8"))
    origin, media, private = fixture["origin"], Path(fixture["media"]), Path(fixture["private"])
    report = {"passed": False, "synthetic_data": True, "real_gpu_generation": False,
              "mocked_api_responses": False, "checks": [], "errors": [], "screenshots": [],
              "recordings": [], "closed_contexts": 0, "browser_closed": False, "authentication_recorded": False}
    contexts, responses = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        report["browser_version"] = browser.version
        try:
            for name, width, height in (("desktop", 1360, 1000), ("mobile", 375, 844)):
                options = {"ignore_https_errors": True, "viewport": {"width": width, "height": height},
                           "locale": "zh-CN", "timezone_id": "Asia/Taipei", "reduced_motion": "reduce"}
                if name == "desktop":
                    options.update(record_video_dir=str(private / "recordings"), record_video_size={"width": width, "height": height})
                context = browser.new_context(**options)
                contexts.append(context)
                login = context.request.post(origin + "/admin/api/v1/session", data={"username": "retry-browser", "password": fixture["password"]}, headers={"Origin": origin})
                assert login.status == 200
                page = context.new_page()
                page.set_default_timeout(12000)
                page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
                page.goto(origin + "/admin/", wait_until="domcontentloaded")
                metric = page.locator("section.metric").filter(has=page.get_by_text("等待内部重试", exact=True))
                expect(metric.locator("strong")).to_have_text("2")
                snapshot = context.request.get(origin + "/admin/api/v1/queues/snapshot").json()
                assert snapshot["states"]["retry_wait"] == 2 and snapshot["global_unassigned_queued"] == 0 and snapshot["occupied_physical_gpus"] == 0
                report["checks"].append(name + "_retry_has_unfinished_quota_without_ready_candidate_or_gpu_slot")
                identifier = fixture["jobs"][0 if name == "desktop" else 1]
                page.goto(origin + "/admin/jobs?job=" + identifier, wait_until="domcontentloaded")
                drawer = page.get_by_role("dialog", name="任务详情", exact=True)
                expect(drawer.get_by_text("显存不足，重试需使用其他 GPU", exact=True)).to_be_visible()
                expect(drawer.get_by_text("提交前连接暂时失败", exact=True)).to_have_count(0)
                expect(drawer.get_by_text("1 / 2", exact=True)).to_be_visible()
                expect(drawer.get_by_text("已释放 / 已关闭", exact=True)).to_be_visible()
                report["checks"].append(name + "_oom_reason_budget_and_closed_source_are_visible")
                before = context.request.get(origin + "/admin/api/v1/jobs/" + identifier).json()
                assert before["retry"]["last_error"] == "comfy_oom"
                responses.append({"operation": name + "_get_retry", "http_status": 200,
                                  "response": {"id": before["id"], "status": before["status"], "retry": before["retry"]}})
                page.wait_for_timeout(750)
                if name == "desktop":
                    drawer.get_by_label("优先级", exact=True).fill("31")
                    drawer.get_by_role("button", name="保存优先级", exact=True).click()
                    expect(page.get_by_text("优先级已保存", exact=True)).to_be_visible()
                    after = context.request.get(origin + "/admin/api/v1/jobs/" + identifier).json()
                    assert after["priority"] == 31 and after["status"] == "retry_wait" and after["retry"] == before["retry"]
                    report["checks"].append("desktop_priority_change_preserves_oom_backoff_and_identity")
                    page.wait_for_timeout(750)
                else:
                    assert drawer.locator('.el-descriptions__body').evaluate("element => element.scrollWidth <= element.clientWidth + 1"), "mobile_description_table_clipped"
                    report["checks"].append("mobile_long_identifiers_wrap_inside_description_table")
                    page.screenshot(path=str(media / "retry-mobile.png"))
                    report["screenshots"].append("retry-mobile.png")
                    drawer.get_by_role("button", name="请求取消", exact=True).click()
                    page.get_by_role("button", name="确认", exact=True).click()
                    expect(page.get_by_text("取消请求已记录", exact=True)).to_be_visible()
                    after = context.request.get(origin + "/admin/api/v1/jobs/" + identifier).json()
                    assert after["status"] == "cancelled" and after["retry"]["count"] == 1 and after["retry"]["next_at"] is None
                    expect(drawer.get_by_role("button", name="保存优先级", exact=True)).to_have_count(0)
                    report["checks"].append("mobile_cancel_settles_without_another_gpu_execution")
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                    report["checks"].append("mobile_375px_no_document_overflow")
                    responses.append({"operation": "mobile_cancel_result", "http_status": 200,
                                      "response": {"id": after["id"], "status": after["status"], "retry": after["retry"]}})
                screenshot = "retry-" + name + ("-cancelled" if name == "mobile" else "") + ".png"
                page.screenshot(path=str(media / screenshot))
                report["screenshots"].append(screenshot)
                page.wait_for_timeout(500)
                context.close()
                if page.video:
                    report["recordings"].append({"name": name, "path": str(page.video.path())})
            assert not report["errors"]
            report["passed"] = True
        finally:
            for context in reversed(contexts):
                context.close()
            report["closed_contexts"] = len(contexts)
            browser.close()
            report["browser_closed"] = True
            (media / "capture.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (media / "api-responses.json").write_text(json.dumps({"synthetic_data": True, "authentication_headers_and_cookies_omitted": True,
                                                               "responses": responses}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert report["passed"]


if __name__ == "__main__":
    main()
