"""Native Edge UI validation; synthetic catalog fixtures, real HTTPS session."""
from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Error, Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence/2026-10-23"
ORIGIN = "https://127.0.0.1:8443"


def main() -> None:
    password = (ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip()
    tenant, project = "ten_" + "1" * 32, "prj_" + "2" * 32
    channel_id, other_id = "ch_" + "3" * 32, "ch_" + "4" * 32
    source_digest = "a" * 64
    channel = {"id": channel_id, "tenant_id": tenant, "project_id": project, "name": "目录测试渠道", "node_id": "node_" + "5" * 32, "gpu_id": "gpu_" + "6" * 32, "base_url": "http://127.0.0.1:8188", "has_authorization": False, "enabled": True, "draining": False, "revision": 3, "desired_revision": 2, "applied_revision": 2, "verified_revision": 2, "deleted_at": None, "observed_at": None}
    other = {**channel, "id": other_id, "name": "未配置目录渠道"}
    operations: dict[str, dict[str, Any]] = {}
    checks: list[str] = []
    errors: list[str] = []
    captured: dict[str, Any] = {}
    held: list[Route] = []
    mode, status_mode, polls = "success", "success", 0
    started = time.monotonic()

    def file(name: str, directory: str = "") -> dict[str, Any]:
        return {"name": name, "relative_path": (directory + "/" if directory else "") + name, "kind": "file", "bytes": 512, "modified_at": 1792700000, "status": "ready", "source_digest": source_digest, "export_format": "comfy-api", "node_count": 7, "task_type": None, "error_code": None}

    def source(identifier: str) -> dict[str, Any]:
        return {"channel_id": identifier, "channel_revision": 3, "status": "not_configured" if identifier == other_id else "ready", "observation_status": "fresh", "observed_at": int(time.time()), "catalog_supported": True}

    def route_api(route: Route) -> None:
        nonlocal polls
        request = route.request
        path = urlparse(request.url).path
        payload: Any
        code = 200
        if path == "/admin/api/v1/workflows" and request.method == "GET":
            payload = {"data": [], "page": 1, "has_more": False}
        elif path == "/admin/api/v1/channels" and request.method == "GET":
            payload = {"data": [channel, other], "page": 1, "has_more": False}
        elif path.endswith("/workflow-exports") and request.method == "GET":
            identifier = path.split("/")[-2]
            if identifier == channel_id and status_mode == "hold":
                held.append(route)
                return
            payload = source(identifier)
        elif path == "/admin/api/v1/workflow-catalogs" and request.method == "POST":
            assert request.headers.get("idempotency-key") and request.headers.get("x-csrf-token")
            identifier = "op_" + f"{len(operations) + 1:032x}"
            operations[identifier] = request.post_data_json
            payload, code = {"operation_id": identifier, "status": "pending"}, 202
        elif path.startswith("/admin/api/v1/workflow-catalogs/") and request.method == "GET":
            polls += 1
            identifier = path.rsplit("/", 1)[-1]
            data = operations[identifier]
            directory = data["relative_directory"]
            page: dict[str, Any] | None = None
            if mode == "success":
                if directory:
                    entries = [file("synthetic.json", directory)]
                    more = False
                elif data.get("cursor"):
                    entries, more = [file(f"{i:02}.json") for i in range(20, 23)], False
                else:
                    entries = [{"name": "00-images", "relative_path": "00-images", "kind": "directory", "bytes": None, "modified_at": 1792700000, "status": "directory"}, {**file("01-ui.json"), "status": "invalid", "source_digest": None, "export_format": None, "node_count": None, "error_code": "ui_workflow_not_supported"}, *[file(f"{i:02}.json") for i in range(2, 20)]]
                    more = True
                cursor = base64.urlsafe_b64encode(json.dumps({"directory": "", "digest": "b" * 64, "after": "19.json"}, separators=(",", ":")).encode()).rstrip(b"=").decode()
                page = {"protocol": "local-directory-v1", "relative_directory": directory, "directory_digest": "b" * 64, "entries": entries, "skipped_count": 2, "has_more": more, "next_cursor": cursor if more else None}
            payload = {"id": identifier, "channel_id": data["channel_id"], "relative_directory": directory, "status": "running" if mode == "pending" else "failed" if mode == "changed" else "succeeded", "catalog": page, "error_code": "workflow_catalog_changed" if mode == "changed" else None}
        elif path == "/admin/api/v1/workflow-pulls" and request.method == "POST":
            captured["pull"] = request.post_data_json
            payload, code = {"operation_id": "op_" + "f" * 32, "status": "pending"}, 202
        elif path == "/admin/api/v1/operations/op_" + "f" * 32 and request.method == "GET":
            payload = {"id": "op_" + "f" * 32, "command": "pull_workflow", "status": "succeeded", "has_more": False, "targets": [{"id": "cmd_" + "e" * 32, "channel_id": channel_id, "status": "succeeded", "receipt": {"success": True, "applied_revision": 2, "source_digest": source_digest, "source_bytes": 512, "export_format": "comfy-api"}}]}
        else:
            route.continue_()
            return
        route.fulfill(status=code, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1150}, locale="zh-CN")
            context.route("**/admin/api/v1/**", route_api)
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.goto(ORIGIN + "/admin/", wait_until="domcontentloaded")
            page.get_by_label("用户名", exact=True).fill("admin")
            page.get_by_label("密码", exact=True).fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
            page.get_by_role("link", name="Workflow 管理", exact=True).click()
            page.get_by_role("button", name="从渠道导入", exact=True).click()
            dialog = page.get_by_role("dialog", name="从渠道导入 Workflow", exact=True)

            def choose(name: str) -> None:
                dialog.get_by_label("来源渠道", exact=True).click()
                page.get_by_role("option", name=name, exact=True).click()

            choose("目录测试渠道")
            expect(dialog.get_by_text("导出目录可用", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="浏览导出目录", exact=True).click()
            expect(dialog.get_by_text("第 1 页 · 20 项 · 跳过 2 项", exact=True)).to_be_visible()
            expect(dialog.get_by_role("button", name="选择 01-ui.json", exact=True)).to_be_disabled()
            expect(dialog.get_by_text("请导出 API 格式", exact=True)).to_be_visible()
            checks.append("source_state_precheck_and_invalid_file_disable")
            page.screenshot(path=str(EVIDENCE / "admin-workflow-catalog.png"), full_page=True)

            dialog.get_by_role("button", name="下一页", exact=True).click()
            expect(dialog.get_by_text("第 2 页 · 3 项 · 跳过 2 项", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="上一页", exact=True).click()
            expect(dialog.get_by_text("第 1 页 · 20 项 · 跳过 2 项", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="进入 00-images", exact=True).click()
            expect(dialog.get_by_role("button", name="选择 synthetic.json", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="选择 synthetic.json", exact=True).click()
            expect(dialog.get_by_label("导出文件相对路径", exact=True)).to_have_value("00-images/synthetic.json")
            dialog.get_by_role("button", name="读取来源", exact=True).click()
            expect(dialog.get_by_text("来源快照已保存，可以预览导入差异", exact=True)).to_be_visible()
            assert captured["pull"] == {"channel_id": channel_id, "expected_revision": 3, "relative_path": "00-images/synthetic.json", "expected_source_digest": source_digest}
            checks.append("directory_navigation_cursor_previous_next_and_selected_digest_pull")

            choose("未配置目录渠道")
            expect(dialog.get_by_text("未配置导出目录", exact=True)).to_be_visible()
            expect(dialog.get_by_role("button", name="浏览导出目录", exact=True)).to_be_disabled()
            status_mode = "hold"
            choose("目录测试渠道")
            expect(dialog.get_by_text("正在查询来源状态", exact=True)).to_be_visible()
            choose("未配置目录渠道")
            expect(dialog.get_by_text("未配置导出目录", exact=True)).to_be_visible()
            for route in held:
                try:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(source(channel_id)))
                except Error:
                    pass  # Aborted old request may already be gone in Edge.
            page.wait_for_timeout(150)
            expect(dialog.get_by_text("未配置导出目录", exact=True)).to_be_visible()
            status_mode = "success"
            choose("目录测试渠道")
            expect(dialog.get_by_text("导出目录可用", exact=True)).to_be_visible()
            checks.append("channel_change_clears_catalog_and_late_source_response_is_ignored")

            mode = "changed"
            dialog.get_by_role("button", name="浏览导出目录", exact=True).click()
            expect(dialog.get_by_text("目录发生变化，请重新加载首页", exact=True)).to_be_visible()
            mode = "success"
            dialog.get_by_role("button", name="导出根目录", exact=True).click()
            expect(dialog.get_by_role("button", name="进入 00-images", exact=True)).to_be_visible()
            checks.append("changed_catalog_rejects_page_and_reload_recovers")

            mode = "pending"
            dialog.get_by_role("button", name="浏览导出目录", exact=True).click()
            expect(dialog.get_by_role("button", name="暂停等待目录", exact=True)).to_be_visible()
            page.wait_for_timeout(200)
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))")
            expect(dialog.get_by_text("页面已隐藏，目录读取已暂停，可重新检查操作", exact=True)).to_be_visible()
            paused = polls
            page.wait_for_timeout(1800)
            assert polls == paused
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
            dialog.get_by_role("button", name="检查目录操作", exact=True).click()
            expect(dialog.get_by_role("button", name="暂停等待目录", exact=True)).to_be_visible()
            choose("未配置目录渠道")
            expect(dialog.get_by_text("未配置导出目录", exact=True)).to_be_visible()
            paused = polls
            page.wait_for_timeout(1800)
            assert polls == paused
            checks.append("hidden_page_and_channel_change_abort_catalog_polling")

            choose("目录测试渠道")
            expect(dialog.get_by_text("导出目录可用", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="浏览导出目录", exact=True).click()
            expect(dialog.get_by_role("button", name="暂停等待目录", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="关闭", exact=True).click()
            paused = polls
            page.wait_for_timeout(1800)
            assert polls == paused
            assert not errors
            checks.append("dialog_close_aborts_requests_without_orphan_timers")
        finally:
            if context is not None:
                context.close()
            browser.close()
    report = {"at": datetime.now(UTC).isoformat(), "duration_seconds": round(time.monotonic() - started, 2), "checks": checks, "runtime_errors": errors, "browser": "native installed Edge, headless", "backend_mode": "real HTTPS login and production static build; synthetic catalog/source routes", "workflow_database_or_worker_mutations": False, "real_admin_session_created": True, "real_comfy_or_s3_tested": False, "browser_context_closed": True}
    (EVIDENCE / "admin-workflow-catalog-browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "runtime_errors": len(errors), "browser_context_closed": True}))


if __name__ == "__main__":
    main()
