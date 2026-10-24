"""Real HTTPS Admin login/build, synthetic isolation snapshots and mutation replies."""

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
    now = int(time.time())
    job_id, validation_id = "job_" + "a" * 32, "job_" + "b" * 32
    row: dict[str, Any] = {
        "id": "iso_" + "1" * 32,
        "tenant_id": "ten_" + "2" * 32,
        "project_id": "prj_" + "3" * 32,
        "gpu_id": "gpu_" + "4" * 32,
        "release_id": "wfr_" + "5" * 32,
        "spec_digest": "6" * 64,
        "specification": {
            "protocol": "generation-spec-v1",
            "task_type": "text_to_image",
            "parameters": {"size": "1024x1024", "quality": "standard", "n": 1},
            "source_image": False,
            "mask": False,
            "reference_count": 0,
        },
        "is_active": True,
        "revision": 3,
        "failure_count": 2,
        "last_failure_at": now,
        "last_failure_job_id": job_id,
        "last_error_code": "comfy_oom",
        "last_success_generation_at": None,
        "isolated_at": now,
        "reason": "repeated_failure",
        "cleared_validation_job_id": None,
        "updated_at": now,
    }
    job = {
        "id": job_id,
        "model": "synthetic-image",
        "task_type": "text_to_image",
        "status": "failed",
        "created_at": now,
        "updated_revision": 9,
        "error": {"code": "comfy_oom", "message": "Synthetic OOM"},
        "tenant_id": row["tenant_id"],
        "project_id": row["project_id"],
        "key_id": "key_" + "7" * 32,
        "priority": 0,
        "is_validation": False,
        "cancellation_requested": False,
        "retry_of": None,
    }
    checks: list[str] = []
    errors: list[str] = []
    requests: list[dict[str, Any]] = []
    pages: list[int] = []
    polls = 0
    readonly, fail_list, reject_resume, reject_create = False, False, True, True
    session_payload: dict[str, Any] | None = None

    def route_api(route: Route) -> None:
        nonlocal polls, session_payload
        request = route.request
        url = urlparse(request.url)
        path, code = url.path, 200
        payload: Any
        if path == "/admin/api/v1/session" and readonly:
            assert session_payload
            payload = {
                **session_payload,
                "user": {**session_payload["user"], "role": "observer"},
                "permissions": ["channels:read", "jobs:read", "queues:read", "operations:read"],
            }
        elif path == "/admin/api/v1/spec-isolations" and request.method == "GET":
            polls += 1
            query = parse_qs(url.query)
            page = int(query.get("page", ["1"])[0])
            pages.append(page)
            if fail_list:
                payload, code = {"error": {"message": "合成读取失败"}}, 503
            else:
                state = query.get("state", ["all"])[0]
                payload = {
                    "data": [row] if state != "isolated" or row["is_active"] else [],
                    "page": page,
                    "has_more": page == 1,
                }
        elif path.endswith("/actions") and path.startswith("/admin/api/v1/spec-isolations/"):
            body = request.post_data_json
            requests.append(
                {
                    "body": body,
                    "token": request.headers.get("idempotency-key"),
                    "match": request.headers.get("if-match"),
                    "csrf": bool(request.headers.get("x-csrf-token")),
                }
            )
            if body["action"] == "resume" and reject_resume:
                payload, code = {"error": {"message": "验证证明不匹配，请重新验证"}}, 409
            else:
                row.update(is_active=body["action"] == "isolate", revision=row["revision"] + 1)
                if body["action"] == "resume":
                    row.update(failure_count=0, cleared_validation_job_id=body["validation_job_id"])
                payload = row
        elif path == "/admin/api/v1/spec-isolations" and request.method == "POST":
            body = request.post_data_json
            requests.append(
                {
                    "body": body,
                    "token": request.headers.get("idempotency-key"),
                    "match": request.headers.get("if-match"),
                    "csrf": bool(request.headers.get("x-csrf-token")),
                }
            )
            if reject_create:
                payload, code = {"error": {"message": "合成写入响应未知，请重试"}}, 503
            else:
                row.update(
                    is_active=True, revision=row["revision"] + 1, reason="manual_investigation"
                )
                payload, code = row, 201
        elif path == "/admin/api/v1/queues/jobs":
            payload = {"data": [job], "page": 1, "has_more": False}
        elif path == "/admin/api/v1/jobs/" + job_id:
            payload = {
                **job,
                "current_attempt_id": "att_" + "8" * 32,
                "release_id": row["release_id"],
                "attempts": [],
                "retries": [],
                "artifacts": [],
            }
        elif path == "/admin/api/v1/alerts":
            payload = {
                "snapshot_at": now,
                "data": [
                    {
                        "id": "alert:spec_isolation:" + row["id"],
                        "kind": "spec_isolation",
                        "severity": "warning",
                        "resource_id": row["id"],
                        "recorded_at": now,
                    }
                ],
                "total": 1,
                "page": 1,
                "has_more": False,
            }
        else:
            route.continue_()
            return
        route.fulfill(status=code, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1440, "height": 1000}
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            context.route("**/admin/api/v1/**", route_api)
            page.goto(ORIGIN + "/admin/spec-isolations", wait_until="domcontentloaded")
            page.get_by_label("用户名", exact=True).fill("admin")
            page.get_by_label("密码", exact=True).fill(
                (ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip()
            )
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="规格隔离", exact=True)).to_be_visible()
            session_payload = context.request.get(ORIGIN + "/admin/api/v1/session").json()
            expect(page.get_by_text("2 次 · 显存不足", exact=True)).to_be_visible()
            expect(page.get_by_text(row["gpu_id"], exact=True)).to_be_visible()
            checks.append("physical_gpu_spec_and_fixed_cause_visible_after_real_https_login")
            page.screenshot(path=str(EVIDENCE / "admin-spec-isolations.png"), full_page=True)
            page.get_by_role("button", name="下一页", exact=True).click()
            expect(page.get_by_text("第 2 页", exact=True)).to_be_visible()
            page.get_by_role("button", name="上一页", exact=True).click()
            assert 2 in pages
            checks.append("bounded_pagination_uses_server_page")
            fail_list = True
            page.get_by_role("button", name="刷新隔离状态", exact=True).click()
            expect(
                page.get_by_text("合成读取失败 · 当前展示最后一次成功快照", exact=True)
            ).to_be_visible()
            expect(page.get_by_text(row["gpu_id"], exact=True)).to_be_visible()
            fail_list = False
            checks.append("read_failure_retains_last_successful_snapshot")
            page.get_by_role("button", name="验证后恢复", exact=True).click()
            dialog = page.get_by_role("dialog", name="验证后恢复规格", exact=True)
            dialog.get_by_label("成功验证任务 ID", exact=True).fill(validation_id)
            dialog.get_by_role("button", name="验证并恢复", exact=True).click()
            expect(dialog.get_by_text("验证证明不匹配，请重新验证", exact=True)).to_be_visible()
            reject_resume = False
            dialog.get_by_role("button", name="验证并恢复", exact=True).click()
            expect(dialog).not_to_be_visible()
            assert requests[0] == requests[1]
            assert requests[0]["body"] == {
                "action": "resume",
                "expected_revision": 3,
                "validation_job_id": validation_id,
            }
            assert requests[0]["match"] == '"3"' and requests[0]["csrf"] and requests[0]["token"]
            checks.append(
                "resume_requires_validation_id_and_retries_same_frozen_revision_and_token"
            )
            page.get_by_role("combobox", name="隔离状态", exact=True).press("ArrowDown")
            page.get_by_role("option", name="全部状态", exact=True).click()
            expect(page.get_by_text("已恢复", exact=True)).to_be_visible()
            page.get_by_role("button", name="人工隔离", exact=True).click()
            dialog = page.get_by_role("dialog", name="人工隔离规格", exact=True)
            dialog.get_by_label("调查记录编号", exact=True).fill("incident-browser")
            dialog.get_by_role("button", name="确认隔离", exact=True).click()
            expect(dialog).not_to_be_visible()
            assert requests[-1]["body"] == {
                "action": "isolate",
                "expected_revision": 4,
                "evidence_id": "incident-browser",
            }
            assert requests[-1]["token"] != requests[0]["token"]
            checks.append("fresh_manual_isolation_uses_new_intent_key")
            page.evaluate(
                "Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))"
            )
            before = polls
            page.wait_for_timeout(5300)
            assert polls == before
            page.evaluate(
                "Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))"
            )
            page.get_by_role("button", name="刷新隔离状态", exact=True).click()
            checks.append("hidden_document_stops_polling_and_visible_document_refreshes")
            page.get_by_role("link", name="任务管理", exact=True).click()
            page.get_by_role("button", name="详情", exact=True).click()
            page.get_by_role("button", name="隔离此生成规格", exact=True).click()
            dialog = page.get_by_role("dialog", name="隔离此生成规格", exact=True)
            dialog.get_by_label("调查记录编号", exact=True).fill("incident-from-job")
            dialog.get_by_role("button", name="确认隔离", exact=True).click()
            expect(dialog.get_by_text("合成写入响应未知，请重试", exact=True)).to_be_visible()
            reject_create = False
            dialog.get_by_role("button", name="确认隔离", exact=True).click()
            expect(dialog).not_to_be_visible()
            assert requests[-2] == requests[-1]
            assert requests[-1]["body"] == {
                "job_id": job_id,
                "expected_revision": 9,
                "evidence_id": "incident-from-job",
            }
            assert requests[-1]["match"] == '"9"' and requests[-1]["csrf"]
            checks.append("job_detail_manual_isolation_derives_only_job_revision_and_evidence")
            before = polls
            page.wait_for_timeout(5300)
            assert polls == before
            checks.append("leaving_page_disposes_isolation_poll")
            page.get_by_role("dialog", name="任务详情", exact=True).locator(
                ".el-drawer__close-btn"
            ).click()
            page.get_by_role("link", name="运行告警", exact=True).click()
            expect(
                page.locator(".el-table").get_by_text("生成规格已隔离", exact=False)
            ).to_be_visible()
            page.get_by_role("link", name=row["id"], exact=True).click()
            expect(page.get_by_role("heading", name="规格隔离", exact=True)).to_be_visible()
            checks.append("derived_isolation_alert_links_to_management_page")
            readonly = True
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_text(row["gpu_id"], exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="验证后恢复", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="人工隔离", exact=True)).to_have_count(0)
            checks.append("observer_can_read_without_mutation_controls")
            assert not errors
        except BaseException:
            if context is not None:
                (ROOT / ".local/spec-isolation-browser-diagnostics.json").write_text(
                    json.dumps(
                        {
                            "url": page.url,
                            "body": page.locator("body").inner_text()[:10000],
                            "runtime_errors": errors,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                page.screenshot(
                    path=str(ROOT / ".local/spec-isolation-browser-failure.png"), full_page=True
                )
            raise
        finally:
            if context is not None:
                context.close()
            browser.close()
    report = {
        "at": datetime.now(UTC).isoformat(),
        "passed": True,
        "checks": checks,
        "runtime_errors": errors,
        "browser_context_closed": True,
        "real_https_admin_session": True,
        "isolation_mutations_synthetic": True,
        "workflow_database_or_worker_mutations": False,
    }
    (EVIDENCE / "admin-spec-isolations-browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"passed": len(checks), "runtime_errors": len(errors), "browser_context_closed": True}
        )
    )


if __name__ == "__main__":
    main()
