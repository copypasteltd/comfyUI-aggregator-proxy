"""Real HTTPS Admin session and production UI; synthetic capacity snapshots only."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence/2026-10-23"
ORIGIN = "https://127.0.0.1:8443"


def main() -> None:
    password = (ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip()
    checks: list[str] = []
    errors: list[str] = []
    mode = "success"
    calls = 0
    kinds = {"worker_storage_pressure": "Worker 磁盘或暂存空间不足", "worker_memory_pressure": "Worker 可用内存不足", "worker_upload_backlog": "Worker 上传积压", "worker_capacity_unavailable": "Worker 容量观测不可用"}
    sample = {"protocol": "native-capacity-v1", "sequence": 7, "observed_ago_ms": 0, "status": "blocked", "reasons": ["disk_space", "memory", "upload_backlog"], "free_disk_bytes": 1024**3, "available_memory_bytes": 512 * 1024**2, "spool_bytes": 6 * 1024**3, "reserved_bytes": 2 * 1024**3, "pending_uploads": 2, "minimum_free_disk_bytes": 5 * 1024**3, "minimum_available_memory_bytes": 1024**3, "maximum_spool_bytes": 20 * 1024**3, "generation_reserve_bytes": 2 * 1024**3, "maximum_uploads": 2}

    def snapshot(route: Route) -> None:
        nonlocal calls
        if urlparse(route.request.url).path != "/admin/api/v1/alerts":
            route.continue_()
            return
        calls += 1
        if mode == "error":
            route.fulfill(status=503, content_type="application/json", body='{"error":{"message":"合成容量服务暂不可用"}}')
            return
        selected = parse_qs(urlparse(route.request.url).query).get("kind", [None])[0]
        now = int(time.time())
        data: list[dict[str, Any]] = [{"id": "alert:" + kind + ":node_" + "1" * 32, "kind": kind, "severity": "warning", "resource_id": "node_" + "1" * 32, "recorded_at": now, "capacity_observed_at": None if kind.endswith("unavailable") else now, "capacity": None if kind.endswith("unavailable") else sample} for kind in kinds if not selected or kind == selected]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"snapshot_at": now, "data": data, "total": len(data), "page": 1, "has_more": False}))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1740, "height": 1100}, locale="zh-CN")
            context.route("**/admin/api/v1/alerts*", snapshot)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.goto(ORIGIN + "/admin/", wait_until="domcontentloaded")
            page.get_by_label("用户名", exact=True).fill("admin")
            page.get_by_label("密码", exact=True).fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
            page.get_by_role("link", name="运行告警", exact=True).click()
            expect(page.get_by_role("heading", name="运行告警", exact=True)).to_be_visible()
            table = page.locator(".el-table__body-wrapper")
            for label in kinds.values():
                expect(table.get_by_text(label, exact=False)).to_be_visible()
            expect(table.get_by_text("磁盘可用 1.00 GiB · 最低 5.00 GiB", exact=True).first).to_be_visible()
            expect(table.get_by_text("待回传 2 / 2 · 下次预留 2.00 GiB", exact=True).first).to_be_visible()
            expect(table.get_by_text("等待有效容量观测，暂停接收新生成", exact=True)).to_be_visible()
            checks.append("four_capacity_alerts_and_numeric_budgets_displayed")
            page.screenshot(path=str(EVIDENCE / "admin-worker-capacity.png"), full_page=True)
            for label in kinds.values():
                page.locator(".toolbar .el-select__wrapper").click()
                page.get_by_role("option", name=label, exact=True).click()
                expect(table.locator(".el-table__row")).to_have_count(1)
                expect(table.get_by_text(label, exact=False)).to_be_visible()
            checks.append("all_four_capacity_filters_send_and_show_selected_kind")
            mode = "error"
            page.get_by_role("button", name="刷新告警", exact=True).click()
            expect(page.get_by_text("合成容量服务暂不可用 · 当前展示最后一次成功快照", exact=True)).to_be_visible()
            expect(table.get_by_text(kinds["worker_capacity_unavailable"], exact=False)).to_be_visible()
            checks.append("failed_refresh_keeps_explicit_last_successful_snapshot")
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))")
            paused = calls
            page.wait_for_timeout(5500)
            assert calls == paused
            checks.append("hidden_page_stops_capacity_polling")
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
            mode = "success"
            page.get_by_role("link", name="运行总览", exact=True).click()
            expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
            page.wait_for_timeout(300)
            paused = calls
            page.wait_for_timeout(5500)
            assert calls == paused and not errors
            checks.append("route_leave_stops_polling_and_has_no_runtime_errors")
        finally:
            if context is not None:
                context.close()
            browser.close()
    report = {"recorded_at": datetime.now(UTC).isoformat(), "passed": True, "checks": checks, "runtime_errors": errors, "browser_context_closed": True, "real_https_admin_session": True, "snapshots_synthetic": True, "worker_or_catalog_mutations": False}
    (EVIDENCE / "admin-worker-capacity-browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "runtime_errors": len(errors), "browser_context_closed": True}))


if __name__ == "__main__":
    main()
