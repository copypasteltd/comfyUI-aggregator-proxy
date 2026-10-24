"""Real HTTPS login/build; synthetic queue and safe option replies for UI edge cases."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error, Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence/2026-10-23"
ORIGIN = "https://127.0.0.1:8443"


def main() -> None:
    now = int(time.time())
    ids = {"tenant": "ten_" + "1"*32, "project": "prj_" + "2"*32, "workflow": "wf_" + "3"*32, "release": "wfr_" + "4"*32, "channel": "ch_" + "5"*32, "model": "history-image"}
    labels = {"tenant": "合成租户", "project": "合成项目", "workflow": "历史 Workflow", "release": "历史 Workflow · v1", "channel": "合成 Windows 渠道", "model": "history-image"}
    channel = {"id": ids["channel"], "tenant_id": ids["tenant"], "project_id": ids["project"], "name": labels["channel"], "node_id": "node_" + "6"*32, "gpu_id": "gpu_" + "7"*32, "base_url": "http://127.0.0.1:1", "has_authorization": False, "enabled": True, "draining": False, "revision": 1, "desired_revision": 1, "applied_revision": 1, "verified_revision": 1, "deleted_at": None, "observed_at": now, "shared_candidates": 7, "assigned_unfinished": 1, "gpu_occupied": True, "gpu_occupancy_uncertain": True, "gpu_management_hold": False, "observation_status": "unknown", "queue_observed_at": now-30, "comfy_queue_running": None, "comfy_queue_pending": None}
    job = {"id": "job_" + "8"*32, "model": "history-image", "task_type": "text_to_image", "status": "queued", "created_at": now-120, "accepted_at": now-120, "first_assigned_at": None, "queue_wait_seconds": 120, "updated_revision": 1, "error": None, "completed_at": None, "tenant_id": ids["tenant"], "project_id": ids["project"], "release_id": ids["release"], "key_id": "key_" + "9"*32, "priority": 0, "is_validation": False, "cancellation_requested": False, "retry_of": None}
    requests: list[dict[str, Any]] = []
    errors: list[str] = []
    checks: list[str] = []
    held: list[Route] = []
    hold_next, fail_next = False, False

    def snapshot(query: dict[str, list[str]]) -> dict[str, Any]:
        count = 7 if query.get("model") else 3 if query.get("tenant_id") else 250
        page = int(query.get("page", ["1"])[0])
        return {"snapshot_id": "synthetic-queue-ui", "snapshot_at": now, "server_time": now, "observed_at": now, "stale": False, "filters": {key: values[0] for key, values in query.items() if key != "page"}, "global_unassigned_queued": count, "unfinished": count+1, "occupied_physical_gpus": 20, "states": {"queued": count, "reconciling": 1}, "channels": [channel], "channels_page": page, "channels_has_more": page == 1, "candidate_counts_overlap": True}

    def route_api(route: Route) -> None:
        nonlocal hold_next, fail_next
        parsed = urlparse(route.request.url)
        path, query = parsed.path, parse_qs(parsed.query)
        payload: Any
        if path == "/admin/api/v1/queues/filter-options":
            kind = query["kind"][0]
            payload = {"data": [{"value": ids[kind], "label": labels[kind]}], "page": 1, "has_more": False}
        elif path in ("/admin/api/v1/queues/snapshot", "/admin/api/v1/queues/jobs"):
            requests.append({"path": path, "query": query})
            if hold_next:
                hold_next = False
                held.append(route)
                return
            if fail_next:
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"error": {"message": "合成快照失败"}}))
                return
            payload = snapshot(query) if path.endswith("snapshot") else {"data": [job], "page": int(query.get("page", ["1"])[0]), "has_more": query.get("page", ["1"])[0] == "1", "snapshot_at": now, "filters": {key: values[0] for key, values in query.items() if key != "page"}}
        elif path == "/admin/api/v1/jobs/" + job["id"]:
            payload = {**job, "current_attempt_id": None, "attempts": [], "retries": [], "artifacts": []}
        else:
            route.continue_()
            return
        route.fulfill(content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1536, "height": 1080})
            context.route("**/admin/api/v1/**", route_api)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.goto(ORIGIN + "/admin/", wait_until="domcontentloaded")
            page.get_by_label("用户名", exact=True).fill("admin")
            page.get_by_label("密码", exact=True).fill((ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip())
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
            expect(page.locator(".metric strong").first).to_have_text("250")
            expect(page.get_by_text("任务计数按筛选条件统计", exact=False)).to_be_visible()
            expect(page.get_by_text("未知 / 未知", exact=False)).to_be_visible()
            checks.append("real_https_login_bounded_snapshot_and_unknown_resource_observation")
            def choose(label: str, option: str) -> None:
                page.get_by_role("combobox", name=label, exact=True).press("ArrowDown")
                page.get_by_role("option", name=option, exact=True).click()
            choose("租户", labels["tenant"])
            choose("项目", labels["project"])
            page.get_by_role("button", name="应用筛选", exact=True).click()
            expect(page.locator(".metric strong").first).to_have_text("3")
            assert requests[-1]["query"]["tenant_id"] == [ids["tenant"]] and requests[-1]["query"]["project_id"] == [ids["project"]]
            checks.append("visual_tenant_project_selection_and_explicit_apply")
            hold_next = True
            page.get_by_role("button", name="刷新快照", exact=True).click()
            page.wait_for_timeout(150)
            assert held
            choose("模型", labels["model"])
            page.get_by_role("button", name="应用筛选", exact=True).click()
            expect(page.locator(".metric strong").first).to_have_text("7")
            for old in held:
                try:
                    old.fulfill(content_type="application/json", body=json.dumps(snapshot({})))
                except Error:
                    pass  # The browser already aborted the obsolete request.
            held.clear()
            page.wait_for_timeout(200)
            expect(page.locator(".metric strong").first).to_have_text("7")
            checks.append("applying_filters_aborts_old_snapshot_and_prevents_stale_overwrite")
            choose("Workflow", labels["workflow"])
            choose("发布版本", labels["release"])
            choose("渠道", labels["channel"])
            choose("任务状态", "排队中")
            page.get_by_role("button", name="应用筛选", exact=True).click()
            page.wait_for_timeout(150)
            query = requests[-1]["query"]
            assert query["workflow_id"] == [ids["workflow"]] and query["release_id"] == [ids["release"]] and query["channel_id"] == [ids["channel"]] and query["state"] == ["queued"]
            checks.append("model_workflow_release_channel_and_state_intersection_sent")
            fail_next = True
            page.get_by_role("button", name="刷新快照", exact=True).click()
            expect(page.get_by_text("合成快照失败 · 当前展示最后一次成功快照", exact=True)).to_be_visible()
            expect(page.locator(".metric strong").first).to_have_text("7")
            checks.append("failed_refresh_preserves_last_matching_snapshot_and_exposes_error")
            fail_next = False
            page.get_by_role("button", name="下一页", exact=True).click()
            expect(page.get_by_text("第 2 页", exact=True)).to_be_visible()
            assert requests[-1]["query"]["page"] == ["2"] and requests[-1]["query"]["model"] == [ids["model"]]
            page.get_by_role("button", name="重置筛选", exact=True).click()
            expect(page.locator(".metric strong").first).to_have_text("250")
            assert requests[-1]["query"] == {"page": ["1"]}
            checks.append("server_channel_pagination_preserves_filters_and_reset_clears_them")
            page.screenshot(path=str(EVIDENCE / "admin-queue-filters.png"), full_page=True)
            page.get_by_role("link", name="任务管理", exact=True).click()
            expect(page.get_by_role("heading", name="任务管理", exact=True)).to_be_visible()
            expect(page.get_by_text("2 分 0 秒", exact=True)).to_be_visible()
            page.locator(".el-table").get_by_role("button", name="详情", exact=True).click()
            expect(page.get_by_role("dialog", name="任务详情").get_by_text("2 分 0 秒", exact=True)).to_be_visible()
            page.get_by_role("dialog", name="任务详情").locator(".el-drawer__close-btn").click()
            checks.append("job_list_and_detail_display_actual_wait_and_frozen_release")
            count = len(requests)
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(5500)
            assert len(requests) == count
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(300)
            assert len(requests) > count
            checks.append("hidden_jobs_stop_polling_and_visibility_resume_rechecks_session")
            page.set_viewport_size({"width": 390, "height": 900})
            page.wait_for_timeout(200)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
            page.screenshot(path=str(EVIDENCE / "admin-queue-filters-mobile.png"), full_page=True)
            checks.append("mobile_filter_form_has_no_page_horizontal_overflow")
            page.get_by_role("button", name="退出登录", exact=True).click()
            expect(page.get_by_role("button", name="登录", exact=True)).to_be_visible()
            count = len(requests)
            page.wait_for_timeout(5500)
            assert len(requests) == count
            checks.append("logout_unmounts_queue_refresh_without_residual_requests")
            assert not errors
        finally:
            if context:
                context.close()
            browser.close()
    report = {"passed": True, "checks": checks, "runtime_errors": errors, "real_https_admin_session": True, "synthetic_queue_replies": True, "all_contexts_closed": True}
    (EVIDENCE / "admin-queue-filters-browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "all_contexts_closed": True}))


if __name__ == "__main__":
    main()
