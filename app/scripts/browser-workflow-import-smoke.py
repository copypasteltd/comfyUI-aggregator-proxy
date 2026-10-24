"""Real native Edge/HTTPS UI with bounded synthetic Workflow pull fixtures."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence/2026-10-23"
ORIGIN = "https://127.0.0.1:8443"


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    password = (ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip()
    started = time.monotonic()
    checks: list[str] = []
    errors: list[str] = []
    captured: dict[str, Any] = {}
    tenant, project = "ten_" + "1" * 32, "prj_" + "2" * 32
    channel_id, workflow_id, imported_id = "ch_" + "3" * 32, "wf_" + "4" * 32, "wf_" + "5" * 32
    operation_id, preview_id = "op_" + "6" * 32, "wip_" + "7" * 32
    source_digest, candidate_digest = "a" * 64, "b" * 64
    workflow = {"id": workflow_id, "tenant_id": tenant, "project_id": project, "name": "合成原有 Workflow", "task_type": "text_to_image", "revision": 4, "archived": False, "created_at": 1792700000}
    rows = [workflow]
    channel = {"id": channel_id, "tenant_id": tenant, "project_id": project, "name": "合成导出渠道", "node_id": "node_" + "8" * 32, "gpu_id": "gpu_" + "9" * 32, "base_url": "http://127.0.0.1:8188", "has_authorization": False, "enabled": True, "draining": False, "revision": 3, "desired_revision": 2, "applied_revision": 2, "verified_revision": 2, "deleted_at": None, "observed_at": None}
    mode, polls, imported = "success", 0, False
    preview_target: dict[str, Any] = {}

    def route_api(route: Route) -> None:
        nonlocal polls, imported, preview_target
        request = route.request
        path = urlparse(request.url).path
        payload: Any
        status = 200
        if path == "/admin/api/v1/workflows" and request.method == "GET":
            payload = {"data": rows, "page": 1, "has_more": False}
        elif path == "/admin/api/v1/channels" and request.method == "GET":
            payload = {"data": [channel], "page": 1, "has_more": False}
        elif path == "/admin/api/v1/channels/" + channel_id + "/workflow-exports" and request.method == "GET":
            payload = {"channel_id": channel_id, "channel_revision": 3, "status": "not_configured" if mode == "unconfigured" else "ready", "observation_status": "fresh", "observed_at": int(time.time()), "catalog_supported": True}
        elif path == "/admin/api/v1/workflow-pulls" and request.method == "POST":
            captured["pull"] = request.post_data_json
            assert request.headers.get("idempotency-key") and request.headers.get("x-csrf-token")
            payload, status = {"operation_id": operation_id, "status": "pending"}, 202
        elif path == "/admin/api/v1/operations/" + operation_id and request.method == "GET":
            polls += 1
            pending, failed = mode == "pending", mode == "unconfigured"
            receipt = {"success": False, "error_code": "workflow_export_not_configured"} if failed else None if pending else {"success": True, "applied_revision": 2, "source_digest": source_digest, "source_bytes": 512, "export_format": "comfy-api"}
            state = "failed" if failed else "running" if pending else "succeeded"
            payload = {"id": operation_id, "command": "pull_workflow", "status": state, "has_more": False, "targets": [{"id": "cmd_" + "c" * 32, "channel_id": channel_id, "status": state, "receipt": receipt}]}
        elif path == "/admin/api/v1/workflow-import-previews" and request.method == "POST":
            captured["preview"] = request.post_data_json
            preview_target = captured["preview"]["target"]
            payload, status = {"id": preview_id, "source_operation_id": operation_id, "channel_id": channel_id, "source_digest": source_digest, "export_format": "comfy-api", "candidate_digest": candidate_digest, "target": preview_target, "diff": {"nodes": {"added": {"count": 2, "ids": ["1", "2"], "truncated": False}, "removed": {"count": 0, "ids": [], "truncated": False}, "changed": {"count": 1, "ids": ["3"], "truncated": False}}, "changed_fields": ["workflow_api"]}, "validation": {"publishable": False, "code": "draft_incomplete", "message": "可导入草稿；冻结前须补全绑定、输出、依赖与运行画像"}, "expires_at": int(time.time()) + 1800, "status": "pending", "result": None}, 201
        elif path == "/admin/api/v1/workflow-import-previews/" + preview_id + "/commit" and request.method == "POST":
            captured["commit"] = request.post_data_json
            if mode == "conflict":
                payload, status = {"error": {"code": "config_revision_conflict", "message": "配置已由其他操作更新，请刷新后重试"}}, 409
            else:
                result = {**workflow, "id": imported_id, "name": preview_target["name"], "revision": 1}
                if not imported:
                    rows.append(result)
                imported = True
                payload = {"workflow": result, "changed": True}
        else:
            route.continue_()
            return
        route.fulfill(status=status, json=payload, headers={"Cache-Control": "no-store"})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1050}, locale="zh-CN")
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
            dialog.get_by_role("button", name="读取来源", exact=True).click()
            expect(dialog.get_by_text("请选择来源渠道", exact=True)).to_be_visible()
            checks.append("required_source_fields_are_inline_and_do_not_submit")
            dialog.get_by_label("来源渠道", exact=True).click()
            page.get_by_role("option", name="合成导出渠道", exact=True).click()
            dialog.get_by_label("导出文件相对路径", exact=True).fill("images/synthetic.json")
            dialog.get_by_role("button", name="读取来源", exact=True).click()
            expect(dialog.get_by_text("来源快照已保存，可以预览导入差异", exact=True)).to_be_visible()
            dialog.get_by_label("Workflow 名称", exact=True).fill("渠道导入合成工作流")
            dialog.get_by_role("button", name="预览差异", exact=True).click()
            expect(dialog.get_by_text("新增节点", exact=True)).to_be_visible()
            expect(dialog.get_by_text("可导入草稿；冻结前须补全绑定、输出、依赖与运行画像", exact=True)).to_be_visible()
            page.screenshot(path=str(EVIDENCE / "admin-workflow-import-preview.png"), full_page=True)
            assert captured["pull"] == {"channel_id": channel_id, "expected_revision": 3, "relative_path": "images/synthetic.json"}
            assert captured["preview"]["source_digest"] == source_digest
            dialog.get_by_role("button", name="确认导入草稿", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page.get_by_text("渠道导入合成工作流", exact=True)).to_be_visible()
            assert captured["commit"] == {"candidate_digest": candidate_digest}
            checks.append("source_revision_digest_diff_and_confirmed_draft_creation")

            mode = "conflict"
            page.get_by_role("row").filter(has_text="合成原有 Workflow").get_by_role("button", name="从渠道更新", exact=True).click()
            dialog = page.get_by_role("dialog", name="从渠道更新 Workflow 草稿", exact=True)
            dialog.get_by_text("已有读取操作：恢复检查", exact=True).click()
            dialog.get_by_role("textbox", name="读取操作 ID", exact=True).fill(operation_id)
            dialog.get_by_role("button", name="检查操作", exact=True).click()
            expect(dialog.get_by_text("来源快照已保存，可以预览导入差异", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="预览差异", exact=True).click()
            expect(dialog.get_by_role("button", name="确认导入草稿", exact=True)).to_be_visible()
            assert captured["preview"]["target"] == {"mode": "update", "workflow_id": workflow_id, "expected_revision": 4}
            dialog.get_by_role("button", name="确认导入草稿", exact=True).click()
            expect(dialog.get_by_text("配置已由其他操作更新，请刷新后重试", exact=True)).to_be_visible()
            expect(dialog).to_be_visible()
            checks.append("resume_captured_source_and_expose_revision_conflict")
            dialog.get_by_role("button", name="关闭", exact=True).click()

            mode = "unconfigured"
            page.get_by_role("button", name="从渠道导入", exact=True).click()
            dialog = page.get_by_role("dialog", name="从渠道导入 Workflow", exact=True)
            dialog.get_by_text("已有读取操作：恢复检查", exact=True).click()
            dialog.get_by_role("textbox", name="读取操作 ID", exact=True).fill(operation_id)
            dialog.get_by_role("button", name="检查操作", exact=True).click()
            expect(dialog.get_by_text("此渠道未配置 Workflow 导出目录，请先在该 Windows Worker 中登记目录。", exact=True)).to_be_visible()
            checks.append("unconfigured_export_root_has_actionable_failure_message")
            dialog.get_by_role("button", name="关闭", exact=True).click()

            mode = "pending"
            page.get_by_role("button", name="从渠道导入", exact=True).click()
            dialog = page.get_by_role("dialog", name="从渠道导入 Workflow", exact=True)
            dialog.get_by_text("已有读取操作：恢复检查", exact=True).click()
            dialog.get_by_role("textbox", name="读取操作 ID", exact=True).fill(operation_id)
            before = polls
            dialog.get_by_role("button", name="检查操作", exact=True).click()
            expect(dialog.get_by_text("等待渠道读取导出文件…", exact=True)).to_be_visible()
            page.wait_for_timeout(200)
            assert polls > before
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))")
            expect(dialog.get_by_text("页面已隐藏，读取已暂停；返回后可重新检查操作。", exact=True)).to_be_visible()
            paused = polls
            page.wait_for_timeout(1800)
            assert polls == paused
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
            dialog.get_by_role("button", name="检查操作", exact=True).click()
            page.wait_for_timeout(200)
            dialog.get_by_role("button", name="关闭", exact=True).click()
            closed = polls
            page.wait_for_timeout(1800)
            assert polls == closed
            checks.append("hidden_page_and_dialog_close_abort_polling_without_orphan_timers")
            assert not errors
        finally:
            if context is not None:
                context.close()
            browser.close()
    report = {"at": datetime.now(UTC).isoformat(), "duration_seconds": round(time.monotonic() - started, 2), "checks": checks, "runtime_errors": errors, "browser": "native installed Edge, headless", "backend_mode": "real HTTPS login and production static build; synthetic Workflow route fixtures", "workflow_database_or_worker_mutations": False, "real_admin_session_created": True, "real_comfy_or_s3_tested": False, "browser_context_closed": True}
    (EVIDENCE / "admin-workflow-import-browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "runtime_errors": len(errors), "browser_context_closed": True}))


if __name__ == "__main__":
    main()
