"""Exercise access-history races with bounded, explicitly synthetic route fixtures.

Login/static assets use the real loopback HTTPS app. No fixture Key or access
event is written to PostgreSQL. The real HTTP/DB browser checks live separately
in browser-smoke.py.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://127.0.0.1:8443"
EVIDENCE = ROOT.parent / "docs/evidence" / datetime.now().strftime("%Y-%m-%d")
KEY = "key_" + "1" * 32


def event(marker: int, result: str = "success") -> dict[str, Any]:
    return {"id": "ace_" + f"{marker:032x}", "request_id": f"00000000-0000-0000-0000-{marker:012x}", "started_at": 1792683000, "recorded_at": 1792683001, "entry": "direct", "method": "GET", "route": "/v1/access-policy", "peer_ip": "127.0.0.1", "client_ip": "127.0.0.1", "gateway_id": None, "status_code": 403 if result == "denied" else 200, "error_code": "synthetic_filter_denied" if result == "denied" else None, "result": result, "completion": "completed", "duration_milliseconds": 1, "request_bytes": 0, "response_bytes": 2}


def payload(marker: int, result: str = "success", more: bool = False) -> dict[str, Any]:
    return {"key_id": KEY, "key_name": "合成访问记录界面验证", "enabled": True, "deleted_at": None, "snapshot_at": 1792683100, "retention_days": 30, "data": [event(marker, result)], "first_cursor": "synthetic-first", "next_cursor": "synthetic-next" if more else None, "capture_health": {"started_at": 1792682000, "written": 0, "failed": 0, "overloaded": 0, "anonymous_skipped": 0}}


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    password = (ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip()
    checks: list[str] = []
    errors: list[str] = []
    seen: list[dict[str, str]] = []
    held: list[Route] = []
    fail_next, hold_next = True, False
    started = time.monotonic()

    def route_api(route: Route) -> None:
        nonlocal fail_next, hold_next
        query = parse_qs(urlparse(route.request.url).query)
        result, before = query.get("result", ["all"])[0], query.get("before", [""])[0]
        seen.append({"result": result, "before": before})
        if hold_next:
            hold_next = False
            held.append(route)
            return
        if before == "synthetic-next" and fail_next:
            fail_next = False
            route.fulfill(status=503, json={"error": {"message": "合成测试：读取暂不可用"}})
        else:
            route.fulfill(json=payload(2 if before == "synthetic-next" else 3 if result == "denied" else 1, "denied" if result == "denied" else "success", more=result == "all" and before != "synthetic-next"))

    def release_stale() -> None:
        for route in held:
            try:
                route.fulfill(json=payload(99))
            except PlaywrightError as exc:
                # Aborted browser requests may already have been discarded by CDP.
                if not any(value in str(exc).lower() for value in ("invalid interception", "already handled", "target closed", "aborted")):
                    raise
        held.clear()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            context.route("**/admin/api/v1/api-keys/*/access-events*", route_api)
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            target = ORIGIN + "/admin/keys/" + KEY + "/access"
            page.goto(target + "?private=synthetic-query#synthetic-fragment", wait_until="domcontentloaded")
            page.get_by_label("用户名", exact=True).fill("admin")
            page.get_by_label("密码", exact=True).fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="Key 访问记录", exact=True)).to_be_visible()
            assert page.url == target
            checks.append("login_keeps_resolved_history_path_without_query_or_hash")
            expect(page.get_by_text(event(1)["request_id"], exact=True)).to_be_visible()
            next_button = page.get_by_role("button", name="下一页", exact=True)
            next_button.click()
            expect(page.get_by_text("合成测试：读取暂不可用", exact=True)).to_be_visible()
            expect(page.get_by_text("第 1 页", exact=True)).to_be_visible()
            expect(page.get_by_text(event(1)["request_id"], exact=True)).to_be_visible()
            next_button.click()
            expect(page.get_by_text("第 2 页", exact=True)).to_be_visible()
            expect(page.get_by_text(event(2)["request_id"], exact=True)).to_be_visible()
            page.get_by_role("button", name="上一页", exact=True).click()
            expect(page.get_by_text("第 1 页", exact=True)).to_be_visible()
            expect(page.get_by_text(event(1)["request_id"], exact=True)).to_be_visible()
            assert seen[-1]["before"] == "synthetic-first"
            checks.append("page_failure_keeps_current_rows_retry_and_previous_reuses_upper_bound")

            hold_next = True
            with page.expect_request("**/access-events*"):
                page.get_by_role("button", name="刷新记录", exact=True).click()
            page.get_by_role("combobox", name="调用结果", exact=True).focus()
            page.get_by_role("combobox", name="调用结果", exact=True).press("ArrowDown")
            page.get_by_role("option", name="拒绝", exact=True).click()
            expect(page.get_by_text("synthetic_filter_denied", exact=True)).to_be_visible()
            release_stale()
            expect(page.get_by_text(event(99)["request_id"], exact=True)).not_to_be_visible()
            expect(page.get_by_text("synthetic_filter_denied", exact=True)).to_be_visible()
            checks.append("late_cancelled_filter_response_cannot_replace_current_filter")

            hold_next = True
            with page.expect_request("**/access-events*"):
                page.get_by_role("button", name="刷新记录", exact=True).click()
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
            release_stale()
            hidden_count = len(seen)
            page.wait_for_timeout(250)
            assert len(seen) == hidden_count
            with page.expect_request("**/access-events*"):
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.get_by_text("synthetic_filter_denied", exact=True)).to_be_visible()
            assert len(seen) == hidden_count + 1
            stable_count = len(seen)
            page.wait_for_timeout(1000)
            assert len(seen) == stable_count
            checks.append("simulated_visibility_cancels_then_resumes_one_read_without_idle_polling")

            hold_next = True
            with page.expect_request("**/access-events*"):
                page.get_by_role("button", name="刷新记录", exact=True).click()
            page.get_by_role("button", name="返回 API 密钥", exact=True).click()
            expect(page.get_by_role("heading", name="API 密钥", exact=True)).to_be_visible()
            release_stale()
            expect(page.get_by_text(event(99)["request_id"], exact=True)).not_to_be_visible()
            count = len(seen)
            page.wait_for_timeout(250)
            assert len(seen) == count
            checks.append("leaving_history_cancels_pending_read_and_drops_late_response")
            assert not errors, "Access-history browser runtime errors"
        finally:
            if context is not None:
                context.close()
            browser.close()
    report = {"at": datetime.now(UTC).isoformat(), "browser": "installed Microsoft Edge, headless", "origin": ORIGIN, "backend_mode": "real local HTTPS login/build; bounded synthetic access-history route fixtures", "visibility_mode": "document.hidden and visibilitychange simulated in isolated page", "business_data_mutated": False, "real_comfy_or_s3_tested": False, "duration_seconds": round(time.monotonic() - started, 2), "checks": checks, "runtime_errors": errors, "browser_context_closed": True}
    (EVIDENCE / "admin-key-access-ui.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "runtime_errors": len(errors), "browser_context_closed": True, "synthetic_access_history_responses": True}))


if __name__ == "__main__":
    main()
