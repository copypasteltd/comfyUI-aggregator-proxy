"""Native production-build browser smoke. Uses only this run's browser context."""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import UTC, datetime
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs" / "evidence" / datetime.now().strftime("%Y-%m-%d")


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    password = (ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip()
    started = time.monotonic()
    checks: list[str] = []
    errors: list[str] = []
    origin = "https://127.0.0.1:8443"
    name = "浏览器验收 Key " + datetime.now(UTC).strftime("%H%M%S")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        business = None
        observer = None
        try:
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.goto(origin + "/admin/", wait_until="domcontentloaded")
            page.get_by_label("用户名", exact=True).fill("admin")
            page.get_by_label("密码", exact=True).fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
            checks.append("secure_session_login")
            page.screenshot(path=str(EVIDENCE / "admin-overview-desktop.png"), full_page=True)

            for link, title in [("Windows 渠道", "Windows 渠道"), ("任务管理", "任务管理"), ("Workflow 管理", "Workflow 管理"), ("模型与路由", "模型与路由"), ("模型文件目录", "模型文件目录"), ("内容管理", "内容管理"), ("网关接入", "网关接入"), ("操作中心", "操作中心"), ("运行告警", "运行告警"), ("审计事件", "审计事件"), ("管理账号", "管理账号")]:
                page.get_by_role("link", name=link, exact=True).click()
                expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                if link == "运行告警":
                    response = context.request.get(origin + "/admin/api/v1/alerts")
                    assert response.status == 200
                    current_alerts = response.json()
                    expect(page.locator(".snapshot-line")).to_contain_text("当前筛选 " + str(current_alerts["total"]) + " 项")
                    if current_alerts["total"] == 0:
                        expect(page.get_by_text("当前筛选没有告警", exact=True)).to_be_visible()
                    page.screenshot(path=str(EVIDENCE / "admin-alerts-desktop.png"), full_page=True)
                    checks.append("alert_snapshot_matches_real_database_and_visible_state")
            checks.append("admin_navigation_and_real_api_empty_states")

            page.get_by_role("button", name="添加管理账号", exact=True).click()
            user_form = page.get_by_role("dialog", name="添加管理账号", exact=True)
            qa_username = "browser-qa-" + datetime.now(UTC).strftime("%H%M%S")
            qa_password, changed_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
            user_form.get_by_label("用户名", exact=True).fill(qa_username)
            user_form.get_by_label("初始密码", exact=True).fill(qa_password)
            user_form.get_by_role("button", name="保存账号", exact=True).click()
            expect(user_form).not_to_be_visible()
            user_row = page.get_by_role("row").filter(has_text=qa_username)
            expect(user_row.get_by_text("只读观察者", exact=True)).to_be_visible()
            observer = browser.new_context(ignore_https_errors=True)
            assert observer.request.post(origin + "/admin/api/v1/session", data={"username": qa_username, "password": qa_password}, headers={"Origin": origin}).status == 200
            assert observer.request.get(origin + "/admin/api/v1/queues/snapshot").status == 200
            assert observer.request.get(origin + "/admin/api/v1/admin-users").status == 403
            checks.append("browser_admin_user_creation_and_observer_permissions")

            user_row.get_by_role("button", name="编辑", exact=True).click()
            user_edit = page.get_by_role("dialog", name="编辑管理账号", exact=True)
            user_edit.get_by_label("新密码（留空保留）", exact=True).fill(changed_password)
            user_edit.get_by_role("button", name="保存账号", exact=True).click()
            page.get_by_role("button", name="确认保存", exact=True).click()
            expect(user_edit).not_to_be_visible()
            assert observer.request.get(origin + "/admin/api/v1/session").status == 401
            assert observer.request.post(origin + "/admin/api/v1/session", data={"username": qa_username, "password": qa_password}, headers={"Origin": origin}).status == 401
            assert observer.request.post(origin + "/admin/api/v1/session", data={"username": qa_username, "password": changed_password}, headers={"Origin": origin}).status == 200
            user_row.get_by_role("button", name="编辑", exact=True).click()
            user_edit.locator(".el-switch").click()
            user_edit.get_by_role("button", name="保存账号", exact=True).click()
            page.get_by_role("button", name="确认保存", exact=True).click()
            expect(user_edit).not_to_be_visible()
            expect(user_row.get_by_text("已停用", exact=True)).to_be_visible()
            assert observer.request.get(origin + "/admin/api/v1/session").status == 401
            page.screenshot(path=str(EVIDENCE / "admin-users-desktop.png"), full_page=True)
            checks.append("browser_password_reset_and_disable_revoke_old_sessions")
            qa_password = changed_password = ""

            page.get_by_role("link", name="模型文件目录", exact=True).click()
            page.get_by_role("button", name="登记模型文件", exact=True).click()
            file_form = page.get_by_role("dialog", name="登记模型文件", exact=True)
            file_name = "浏览器合成文件登记 " + datetime.now(UTC).strftime("%H%M%S")
            file_form.get_by_label("显示名称", exact=True).fill(file_name)
            file_form.get_by_label("models 目录内相对路径", exact=True).fill("checkpoints/browser-synthetic-" + datetime.now(UTC).strftime("%H%M%S") + ".safetensors")
            file_form.get_by_label("SHA-256", exact=True).fill("a" * 64)
            file_form.get_by_label("文件字节数", exact=True).fill("42")
            file_form.get_by_role("button", name="保存登记", exact=True).click()
            expect(file_form).not_to_be_visible()
            file_row = page.get_by_role("row").filter(has_text=file_name)
            expect(file_row).to_be_visible()
            file_row.get_by_role("button", name="编辑", exact=True).click()
            file_edit = page.get_by_role("dialog", name="编辑模型文件登记", exact=True)
            expect(file_edit.get_by_label("SHA-256", exact=True)).to_be_disabled()
            file_edit.get_by_label("显示名称", exact=True).fill(file_name + " 已核对")
            file_edit.get_by_role("button", name="保存登记", exact=True).click()
            expect(file_edit).not_to_be_visible()
            file_row.get_by_role("button", name="归档", exact=True).click()
            page.get_by_role("button", name="确认归档", exact=True).click()
            expect(file_row).not_to_be_visible()
            page.get_by_text("显示已归档", exact=True).click()
            expect(file_row.get_by_text("已归档", exact=True)).to_be_visible()
            page.screenshot(path=str(EVIDENCE / "admin-model-artifacts-desktop.png"), full_page=True)
            checks.append("model_file_catalog_create_edit_immutable_identity_and_archive")

            page.get_by_role("link", name="API 密钥", exact=True).click()
            page.get_by_role("button", name="创建 Key", exact=True).click()
            drawer = page.get_by_role("dialog", name="创建业务 API Key")
            expect(drawer).to_be_visible()
            drawer.get_by_role("textbox", name="Key 名称", exact=True).fill(name)
            drawer.get_by_role("button", name="创建 Key", exact=True).click()
            secret_dialog = page.get_by_role("dialog", name="请保存 API Key")
            expect(secret_dialog).to_be_visible()
            secret = secret_dialog.get_by_label("新 API Key").inner_text().strip()
            assert len(secret) > 40, "secret not issued"
            business = playwright.request.new_context(ignore_https_errors=True, base_url=origin, extra_http_headers={"Authorization": "Bearer " + secret})
            assert business.get("/v1/access-policy").status == 200, "business API authorization failed"
            assert business.get("/admin/api/v1/api-keys").status == 401, "business credential crossed admin realm"
            checks.append("browser_key_creation_and_http_identity_isolation")
            secret_dialog.get_by_role("button", name="已保存，关闭").click()
            expect(secret_dialog).not_to_be_visible()
            page.wait_for_timeout(350)
            assert secret not in page.content(), "secret persisted in DOM"
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0, "browser persisted credentials"
            checks.append("one_time_secret_and_no_browser_storage")
            page.screenshot(path=str(EVIDENCE / "admin-keys-desktop.png"), full_page=True)

            source = BytesIO()
            Image.new("RGB", (32, 32), "teal").save(source, "PNG")
            uploaded = business.post("/v1/assets", multipart={"file": {"name": "synthetic-browser-test.png", "mimeType": "image/png", "buffer": source.getvalue()}})
            assert uploaded.status == 201, "synthetic input upload failed"
            asset_id = uploaded.json()["id"]
            page.get_by_role("link", name="内容管理", exact=True).click()
            page.get_by_role("tab", name="输入素材", exact=True).click()
            content_row = page.get_by_role("row").filter(has_text=asset_id)
            expect(content_row).to_be_visible()
            content_row.get_by_role("button", name="image/png", exact=True).click()
            detail = page.get_by_role("dialog", name="内容详情", exact=True)
            expect(detail.get_by_text(asset_id, exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(detail).not_to_be_visible()
            with page.expect_download() as download_info:
                content_row.get_by_role("button", name="下载", exact=True).click()
            download = download_info.value
            assert download.failure() is None, "admin input download failed"
            download.delete()
            content_row.get_by_role("button", name="删除", exact=True).click()
            page.get_by_role("button", name="删除", exact=True).last.click()
            expect(content_row.get_by_text(re.compile("^(等待回收|已回收)$"))).to_be_visible()
            assert business.get("/v1/assets/" + asset_id).status == 404, "deleted input still visible"
            page.screenshot(path=str(EVIDENCE / "admin-content-desktop.png"), full_page=True)
            checks.append("content_input_metadata_download_and_logical_delete")
            page.get_by_role("link", name="API 密钥", exact=True).click()
            row = page.get_by_role("row").filter(has_text=name)
            row.get_by_role("button", name="停用", exact=True).click()
            page.get_by_role("button", name="确认", exact=True).click()
            expect(row.get_by_text("已停用", exact=True)).to_be_visible()
            assert business.get("/v1/access-policy").status == 401, "disabled credential still accepted"
            checks.append("browser_disable_immediately_revokes_business_access")
            key_id = row.locator(".id-line").first.inner_text().strip()
            with page.expect_response(lambda response: "/access-events" in response.url and response.request.method == "GET") as history_response:
                row.get_by_role("link", name="访问记录", exact=True).click()
            assert history_response.value.status == 200
            expect(page.get_by_role("heading", name="Key 访问记录", exact=True)).to_be_visible()
            expect(page.get_by_text(key_id, exact=True)).to_be_visible()
            expect(page.get_by_text("Key 已停用", exact=True)).to_be_visible()
            history = context.request.get(origin + "/admin/api/v1/api-keys/" + key_id + "/access-events").json()
            assert any(event["status_code"] == 401 and event["error_code"] == "invalid_credentials" for event in history["data"]), "verified disabled Key rejection missing from history"
            assert any(event["status_code"] == 201 and event["route"] == "/v1/assets" for event in history["data"]), "successful input upload missing from history"
            page.get_by_role("combobox", name="调用结果", exact=True).focus()
            page.get_by_role("combobox", name="调用结果", exact=True).press("ArrowDown")
            page.get_by_role("option", name="拒绝", exact=True).click()
            expect(page.get_by_text("invalid_credentials", exact=True)).to_be_visible()
            expect(page.get_by_text("201 · 成功", exact=True)).not_to_be_visible()
            expect(page.locator(".el-loading-mask:visible")).to_have_count(0)
            expect(page.locator(".el-message:visible")).to_have_count(0)
            page.screenshot(path=str(EVIDENCE / "admin-key-access-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Key access history mobile viewport overflow"
            page.screenshot(path=str(EVIDENCE / "admin-key-access-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            checks.append("key_access_history_matches_real_upload_and_disabled_credential_rejection")
            page.get_by_role("button", name="返回 API 密钥", exact=True).click()
            row.get_by_role("button", name="删除", exact=True).click()
            page.get_by_role("button", name="确认删除", exact=True).click()
            expect(row.get_by_text("已删除", exact=True)).to_be_visible()
            row.get_by_role("link", name="访问记录", exact=True).click()
            expect(page.get_by_text("已删除 · 历史保留", exact=True)).to_be_visible()
            expect(page.get_by_text("invalid_credentials", exact=True)).to_be_visible()
            checks.append("logically_deleted_key_retains_access_history_in_admin")
            checks.append("browser_key_logical_delete")

            page.get_by_role("link", name="网关接入", exact=True).click()
            page.get_by_role("button", name="添加网关", exact=True).click()
            gateway_form = page.get_by_role("dialog", name="添加网关", exact=True)
            gateway_name = "浏览器验收网关 " + datetime.now(UTC).strftime("%H%M%S")
            gateway_form.get_by_label("名称", exact=True).fill(gateway_name)
            gateway_form.get_by_label("允许来源网段", exact=True).fill("127.0.0.1/32")
            gateway_form.get_by_role("button", name="保存网关", exact=True).click()
            service_secret = page.get_by_role("dialog", name="请保存网关凭证", exact=True)
            expect(service_secret).to_be_visible()
            service_value = service_secret.get_by_label("新网关凭证").inner_text().strip()
            assert service_value.startswith("gateway_"), "gateway realm was not issued"
            service_secret.get_by_role("button", name="已保存，关闭", exact=True).click()
            expect(service_secret).not_to_be_visible()
            page.wait_for_timeout(350)
            assert service_value not in page.content(), "gateway credential persisted in DOM"
            gateway_row = page.get_by_role("row").filter(has_text=gateway_name)
            expect(gateway_row).to_be_visible()
            gateway_row.get_by_role("button", name="停用", exact=True).click()
            page.get_by_role("button", name="确认", exact=True).click()
            expect(gateway_row.get_by_text("已停用", exact=True)).to_be_visible()
            probe = context.request.get(origin + "/gateway/v1/models", headers={"Authorization": "Bearer " + service_value})
            assert probe.status == 401, "disabled gateway credential still authorized"
            service_value = ""
            page.screenshot(path=str(EVIDENCE / "admin-gateways-desktop.png"), full_page=True)
            checks.append("gateway_creation_one_time_secret_and_disable")

            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("link", name="运行总览", exact=True).click()
            expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "mobile viewport overflow"
            page.screenshot(path=str(EVIDENCE / "admin-overview-mobile.png"), full_page=True)
            checks.append("390px_responsive_layout")
            page.get_by_role("button", name="退出登录", exact=True).click()
            expect(page.get_by_role("heading", name="登录管理后台", exact=True)).to_be_visible()
            assert context.request.get(origin + "/admin/api/v1/session").status == 401, "logout did not revoke session"
            checks.append("logout_revokes_session_and_closes_queue_view")
            assert not errors, "browser runtime errors"
        finally:
            if observer:
                observer.close()
            if business:
                business.dispose()
            if context:
                context.close()
            browser.close()
    report = {"at": datetime.now(UTC).isoformat(), "playwright": version("playwright"), "browser": "installed Microsoft Edge, headless", "origin": origin, "duration_seconds": round(time.monotonic() - started, 2), "checks": checks, "runtime_errors": errors, "browser_context_closed": True, "real_comfy_or_s3_tested": False}
    (EVIDENCE / "admin-browser-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "runtime_errors": len(errors), "browser_context_closed": True}))


if __name__ == "__main__":
    main()
