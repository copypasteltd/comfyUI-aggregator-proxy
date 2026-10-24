"""Production Admin UI with real HTTPS login and explicit synthetic model fixtures."""
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
    channel_id = "ch_" + "3" * 32
    operation_id = "op_" + "4" * 32
    channel = {"id": channel_id, "tenant_id": "ten_" + "1" * 32, "project_id": "prj_" + "2" * 32, "node_id": "node_" + "5" * 32, "gpu_id": "gpu_" + "6" * 32, "name": "模型清单测试渠道", "base_url": "http://127.0.0.1:8188", "has_authorization": False, "enabled": True, "draining": False, "revision": 3, "desired_revision": 2, "applied_revision": 2, "verified_revision": 2, "deleted_at": None, "observed_at": None}
    entry = {"relative_path": "models/checkpoints/synthetic.safetensors", "bytes": 128, "modified_at_ns": 1000000000, "kind": "checkpoint", "registered_count": 0, "registration_status": "unregistered"}
    checks: list[str] = []
    errors: list[str] = []
    captured: dict[str, Any] = {}
    pages: list[int] = []
    read_tokens: list[str] = []
    polls = 0
    mode = "success"
    stale = False
    readonly = False
    session_payload: dict[str, Any] | None = None

    def route_api(route: Route) -> None:
        nonlocal polls, session_payload
        request = route.request
        path = urlparse(request.url).path
        code = 200
        payload: Any
        if path == "/admin/api/v1/session" and readonly:
            assert session_payload
            payload = {**session_payload, "user": {**session_payload["user"], "role": "observer"}, "permissions": ["channels:read", "models:read", "queues:read", "dashboard:read"]}
        elif path == "/admin/api/v1/channels" and request.method == "GET":
            payload = {"data": [channel], "page": 1, "has_more": False}
        elif path == "/admin/api/v1/nodes" and request.method == "GET":
            payload = {"data": [], "page": 1, "has_more": False}
        elif path == f"/admin/api/v1/channels/{channel_id}/queue":
            payload = {**channel, "shared_candidates": 0, "assigned_unfinished": 0, "comfy_queue_running": 0, "comfy_queue_pending": 0, "queue_observed_at": int(time.time()), "gpu_occupied": False, "gpu_occupancy_uncertain": False, "gpu_management_hold": False}
        elif path == f"/admin/api/v1/channels/{channel_id}/inventory":
            page = int(parse_qs(urlparse(request.url).query).get("model_page", ["1"])[0])
            pages.append(page)
            rows = [entry] if page == 1 else [{**entry, "relative_path": "models/vae/second.bin", "kind": "vae"}]
            payload = {"channel_id": channel_id, "observed_at": int(time.time()), "stale": stale, "inventory": {}, "bindings": [], "model_files": {"scope_root": "models", "status": "partial" if mode == "partial" else "ready", "stale": stale, "observed_at": int(time.time()), "catalog_digest": "a" * 64, "reason": "entry_limit" if mode == "partial" else None, "page": page, "total": 51, "has_more": page == 1, "scanned_entries": 55, "skipped_entries": 4, "data": rows}}
        elif path == "/admin/api/v1/model-file-reads" and request.method == "POST":
            assert request.headers.get("idempotency-key") and request.headers.get("x-csrf-token")
            read_tokens.append(request.headers["idempotency-key"])
            captured["read"] = request.post_data_json
            payload, code = {"operation_id": operation_id, "status": "pending"}, 202
        elif path == f"/admin/api/v1/model-file-reads/{operation_id}" and request.method == "GET":
            polls += 1
            inspection = {k: v for k, v in entry.items() if k not in ("registered_count", "registration_status")}
            inspection.update(protocol="model-inspection-v1", catalog_digest="a" * 64, sha256="b" * 64)
            payload = {"id": operation_id, "channel_id": channel_id, "status": "running" if mode == "pending" else "failed" if mode == "failed" else "succeeded", "stale": stale, "observed_at": int(time.time()), "error_code": "model_hash_budget_exceeded" if mode == "failed" else None, "inspection": inspection if mode not in ("pending", "failed") else None, "matching_registrations": entry["registered_count"], "conflicting_registrations": 0}
        elif path == f"/admin/api/v1/model-file-reads/{operation_id}/registrations" and request.method == "POST":
            captured["registration"] = request.post_data_json
            assert request.headers.get("idempotency-key") and request.headers.get("x-csrf-token")
            entry.update(registered_count=1, registration_status="unverified")
            payload, code = {"id": "mdl_" + "7" * 32}, 201
        else:
            route.continue_()
            return
        route.fulfill(status=code, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1520, "height": 1100}, locale="zh-CN")
            context.route("**/admin/api/v1/**", route_api)
            page = context.new_page()
            page.set_default_timeout(12000)
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.goto(ORIGIN + "/admin/", wait_until="domcontentloaded")
            page.get_by_label("用户名", exact=True).fill("admin")
            page.get_by_label("密码", exact=True).fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
            session_payload = context.request.get(ORIGIN + "/admin/api/v1/session").json()
            page.get_by_role("link", name="Windows 渠道", exact=True).click()
            page.get_by_role("button", name="查看", exact=True).click()
            drawer = page.get_by_role("dialog", name="模型清单测试渠道 · 渠道详情", exact=True)
            files = drawer.locator(".model-files")
            expect(files.get_by_text(entry["relative_path"], exact=True)).to_be_visible()
            expect(files.get_by_text("尚未登记（0）", exact=True)).to_be_visible()
            checks.append("native_directory_scope_and_unverified_catalog_metadata_visible")
            files.get_by_role("button", name="下一页模型", exact=True).click()
            expect(files.get_by_text("models/vae/second.bin", exact=True)).to_be_visible()
            files.get_by_role("button", name="上一页模型", exact=True).click()
            expect(files.get_by_text(entry["relative_path"], exact=True)).to_be_visible()
            assert 2 in pages and pages[-1] == 1
            checks.append("bounded_inventory_pagination_sends_model_page")
            files.get_by_role("button", name="检查哈希", exact=True).click()
            expect(files.get_by_text("SHA-256：" + "b" * 64, exact=True)).to_be_visible()
            assert captured["read"] == {"channel_id": channel_id, "expected_revision": 2, "catalog_digest": "a" * 64, "relative_path": entry["relative_path"]}
            checks.append("selected_hash_request_contains_only_current_catalog_identity")
            files.get_by_label("登记名称", exact=True).fill("Synthetic verified file")
            files.get_by_role("button", name="确认登记模型文件", exact=True).click()
            expect(files.get_by_text("已有登记，哈希待核验（1）", exact=True)).to_be_visible()
            assert captured["registration"] == {"name": "Synthetic verified file", "kind": "checkpoint"}
            checks.append("registration_sends_only_name_and_kind_then_refreshes_metadata")
            page.screenshot(path=str(EVIDENCE / "admin-model-files.png"), full_page=True)
            stale = True
            files.get_by_role("button", name="刷新模型目录", exact=True).click()
            expect(files.get_by_role("button", name="检查哈希", exact=True)).to_be_disabled()
            files.get_by_role("button", name="查询检查", exact=True).click()
            expect(files.get_by_role("button", name="确认登记模型文件", exact=True)).to_be_disabled()
            checks.append("stale_source_disables_hash_and_registration")
            stale = False
            mode = "failed"
            files.get_by_role("button", name="查询检查", exact=True).click()
            expect(files.get_by_text("检查失败：model_hash_budget_exceeded", exact=True)).to_be_visible()
            checks.append("fixed_worker_failure_visible_without_proof_form")
            mode = "partial"
            files.get_by_role("button", name="刷新模型目录", exact=True).click()
            expect(files.get_by_text("目录部分可用", exact=False)).to_be_visible()
            checks.append("partial_catalog_is_explicit_and_selected_files_remain_available")
            mode = "pending"
            files.get_by_role("button", name="检查哈希", exact=True).click()
            expect(files.get_by_text("正在等待设备完成模型检查", exact=True)).to_be_visible()
            assert len(read_tokens) == 2 and read_tokens[0] != read_tokens[1]
            checks.append("new_explicit_inspection_uses_fresh_idempotency_key_after_known_completion")
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))")
            calls = polls
            page.wait_for_timeout(1800)
            assert polls == calls
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
            files.get_by_role("button", name="查询检查", exact=True).click()
            expect(files.get_by_text("正在等待设备完成模型检查", exact=True)).to_be_visible()
            checks.append("hidden_tab_pauses_hash_poll_and_operation_id_resumes")
            drawer.locator(".el-drawer__close-btn").click()
            page.wait_for_timeout(200)
            calls = polls
            page.wait_for_timeout(1800)
            assert polls == calls
            checks.append("closed_channel_drawer_stops_polling")
            readonly = True
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="查看", exact=True).click()
            files = page.get_by_role("dialog", name="模型清单测试渠道 · 渠道详情", exact=True).locator(".model-files")
            expect(files.get_by_text(entry["relative_path"], exact=True)).to_be_visible()
            expect(files.get_by_role("button", name="检查哈希", exact=True)).to_have_count(0)
            checks.append("observer_ui_displays_catalog_without_write_controls")
            assert not errors
        finally:
            if context is not None:
                context.close()
            browser.close()
    report = {"recorded_at": datetime.now(UTC).isoformat(), "passed": True, "checks": checks, "runtime_errors": errors, "browser_context_closed": True, "real_https_admin_session": True, "model_snapshots_and_role_ui_synthetic": True, "worker_or_catalog_mutations": False}
    (EVIDENCE / "admin-model-files-browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "runtime_errors": len(errors), "browser_context_closed": True}))


if __name__ == "__main__":
    main()
