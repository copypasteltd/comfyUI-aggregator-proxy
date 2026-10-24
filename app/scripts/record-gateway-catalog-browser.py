"""Exercise real catalog Admin HTTP/UI; mark simulated Provider receipts explicitly."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import Route, expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    origin, media, private = fixture["origin"], Path(fixture["media"]), Path(fixture["private"])
    path = "/admin/api/v1/gateways/" + fixture["gateway"] + "/catalog"
    report: dict[str, Any] = {"passed": False, "synthetic_data": True, "actual_admin_api": True, "provider_receipts_are_protocol_fixtures": True, "real_gpu_generation": False, "checks": [], "screenshots": [], "recordings": [], "errors": [], "closed_contexts": 0, "browser_closed": False}
    responses: list[dict[str, Any]] = []
    contexts: list[Any] = []
    writes: list[dict[str, Any]] = []
    state_requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        report["browser_version"] = browser.version
        try:
            def context(*, observer: bool = False, width: int = 1440, height: int = 1000, record: bool = False) -> tuple[Any, str]:
                options: dict[str, Any] = {"ignore_https_errors": True, "viewport": {"width": width, "height": height}, "locale": "zh-CN", "timezone_id": "Asia/Taipei", "reduced_motion": "reduce"}
                if record:
                    options.update(record_video_dir=str(private / "recordings"), record_video_size={"width": 1440, "height": 1000})
                result = browser.new_context(**options)
                contexts.append(result)
                response = result.request.post(origin + "/admin/api/v1/session", data={"username": "demo-observer" if observer else "demo-admin", "password": fixture["password"]}, headers={"Origin": origin})
                assert response.status == 200, "catalog_ui_login_failed"
                return result, response.json()["csrf_token"]

            def visit(ctx: Any, *, from_models: bool = False) -> tuple[Any, Any]:
                page = ctx.new_page()
                page.set_default_timeout(12000)
                page.on("pageerror", lambda error: report["errors"].append(type(error).__name__))
                page.goto(origin + ("/admin/models" if from_models else "/admin/gateways"), wait_until="domcontentloaded")
                if from_models:
                    expect(page.get_by_role("heading", name="模型与路由", exact=True)).to_be_visible()
                    page.get_by_role("button", name="网关模型目录", exact=True).click()
                    selector = page.get_by_role("dialog", name="选择网关目录", exact=True)
                    selector.get_by_role("button", name="打开目录", exact=True).first.click()
                else:
                    expect(page.get_by_role("heading", name="网关接入", exact=True)).to_be_visible()
                    page.get_by_role("button", name="模型目录", exact=True).first.click()
                expect(page.get_by_text("服务处理失败，请稍后重试", exact=False)).to_have_count(0)
                drawer = page.get_by_role("dialog", name="网关模型目录", exact=True)
                expect(drawer).to_be_visible()
                return page, drawer

            def confirmation(page: Any) -> None:
                box = page.locator(".el-message-box")
                expect(box).to_be_visible()
                box.get_by_role("button", name="确认", exact=True).click()

            def api(ctx: Any, method: str, suffix: str, *, body: Any = None, expected: int = 200, csrf: str = "") -> Any:
                headers = {"Origin": origin}
                if method == "POST":
                    headers.update({"X-CSRF-Token": csrf, "If-Match": '"' + str(body["expected_revision"]) + '"', "Idempotency-Key": uuid4().hex})
                response = ctx.request.fetch(origin + path + suffix, method=method, data=body, headers=headers)
                assert response.status == expected, "catalog_ui_http_" + str(response.status)
                return response.json()

            def receipt(ctx: Any, release: dict[str, Any], rejected: bool = False) -> None:
                body = {key: release[key] for key in ("instance_id", "revision", "digest")}
                body.update({"status": "rejected" if rejected else "applied", "error_code": "account_unavailable" if rejected else None})
                response = ctx.request.post(origin + "/gateway/v1/catalog/receipts", data=body, headers={"Authorization": "Bearer " + fixture["gateway_credential"]})
                assert response.status == 200, "catalog_ui_receipt_failed"
                responses.append({"operation": "simulated_provider_receipt", "http_status": response.status, "request": body, "response": response.json()})

            def screenshot(page: Any, name: str) -> None:
                page.screenshot(path=str(media / name))
                report["screenshots"].append(name)

            desktop, csrf = context(record=True)
            page, drawer = visit(desktop)
            def request_seen(request: Any) -> None:
                request_path = urlparse(request.url).path
                if request.method == "POST" and request_path.startswith(path):
                    writes.append({"path": request_path, "body": request.post_data_json, "idempotency_key": request.headers.get("idempotency-key"), "if_match": request.headers.get("if-match")})
                if request.method == "GET" and (request_path == path or request_path == "/admin/api/v1/session"):
                    state_requests.append(request_path)
            page.on("request", request_seen)
            expect(drawer.get_by_text("使用启动配置", exact=True)).to_be_visible()
            drawer.get_by_role("button", name="编辑期望目录", exact=True).click()
            drawer.get_by_label("Sub2API 逻辑实例标识", exact=True).fill("demo-studio")
            picker = drawer.get_by_role("region", name="选择已发布模型") if drawer.get_by_role("region", name="选择已发布模型").count() else drawer.locator('[aria-label="选择已发布模型"]')
            expect(picker.get_by_role("button", name="选择 / 更新绑定", exact=True)).to_have_count(6)
            for i in range(6):
                picker.get_by_role("button", name="选择 / 更新绑定", exact=True).nth(i).click()
            aliases = ["demo-" + task.replace("_", "-") for task in ("text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio")]
            for alias in aliases:
                drawer.get_by_label(alias + " 用量账户 ID", exact=True).fill("9223372036854775807")
                drawer.get_by_label(alias + " 价格版本", exact=True).fill("demo-prices-v1")
                drawer.get_by_label(alias + " 单位价格", exact=True).fill("0.125")
                drawer.get_by_label(alias + " 分组 ID", exact=True).fill("1,9223372036854775806")
            drawer.locator('[aria-label="映射 demo-text-to-image"]').scroll_into_view_if_needed()
            screenshot(page, "catalog-editor.png")
            drawer.get_by_role("button", name="发布期望目录", exact=True).click()
            confirmation(page)
            expect(drawer.get_by_text("等待 Provider 确认", exact=True)).to_be_visible()
            assert len(writes) == 1 and writes[0]["body"]["expected_revision"] == 0 and writes[0]["if_match"] == '"0"'
            assert all(rule["usage_account_id"] == "9223372036854775807" and rule["unit_price"] == "0.12500000" and "9223372036854775806" in rule["group_ids"] for rule in writes[0]["body"]["models"])
            first = api(desktop, "GET", "/releases/1")
            responses.append({"operation": "publish_six_types", "http_status": 201, "request": writes[0]["body"], "response": first})
            receipt(desktop, first)
            drawer.get_by_role("button", name="刷新目录状态", exact=True).click()
            expect(drawer.get_by_text("已确认", exact=True)).to_be_visible()
            drawer.locator(".el-drawer__body").evaluate("el => el.scrollTop = 0")
            screenshot(page, "catalog-confirmed.png")
            report["checks"].extend(["six_capabilities_visual_mapping", "int64_and_fixed_decimal_wire_precision", "desired_is_separate_from_confirmed", "frozen_json_and_confirmed_digest"])

            drawer.get_by_role("button", name="编辑期望目录", exact=True).click()
            drawer.get_by_label("demo-text-to-image 单位价格", exact=True).fill("0.25")
            dropped = False
            def lose_response(route: Route) -> None:
                nonlocal dropped
                if route.request.method == "POST" and not dropped:
                    dropped = True
                    response = route.fetch()
                    assert response.status == 201
                    route.abort("failed")
                else:
                    route.continue_()
            page.route("**" + path + "/releases", lose_response)
            drawer.get_by_role("button", name="发布期望目录", exact=True).click()
            confirmation(page)
            expect(drawer.get_by_text("发布结果待核对", exact=True)).to_be_visible()
            drawer.locator(".el-drawer__close-btn").click()
            expect(drawer).to_be_visible()
            before = len(writes)
            page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true}); document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(500)
            assert len(writes) == before
            state_requests.clear()
            page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false}); document.dispatchEvent(new Event('visibilitychange'))")
            expect(drawer.get_by_role("button", name="使用原请求核对", exact=True)).to_be_enabled()
            drawer.get_by_role("button", name="使用原请求核对", exact=True).click()
            expect(drawer.get_by_text("发布结果待核对", exact=True)).to_have_count(0)
            assert len(writes) == 3 and writes[1] == writes[2], "unknown_write_changed_body_or_key"
            assert state_requests and state_requests[0] == "/admin/api/v1/session", "visible_restore_did_not_check_session_first"
            second = api(desktop, "GET", "/releases/2")
            assert len(api(desktop, "GET", "/releases")["data"]) == 2
            receipt(desktop, second, rejected=True)
            drawer.get_by_role("button", name="刷新目录状态", exact=True).click()
            expect(drawer.get_by_text("Provider 已拒绝", exact=True)).to_be_visible()
            expect(drawer.get_by_text("用量账户不可用", exact=False)).to_be_visible()
            report["checks"].extend(["committed_response_loss_preserves_original_request", "uncertain_close_blocked", "hidden_write_wait_cleanup_and_session_recheck", "rejection_keeps_previous_confirmed"])
            page.unroute("**" + path + "/releases", lose_response)
            drawer.get_by_role("tab", name="发布历史", exact=True).click()
            drawer.get_by_role("row").filter(has_text="v1 · 发布").get_by_role("button", name="查看版本", exact=True).click()
            expect(drawer.get_by_role("heading", name="冻结版本 v1", exact=True)).to_be_visible()
            screenshot(page, "catalog-history.png")
            drawer.get_by_role("button", name="回滚为新版本", exact=True).click()
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(path + "/replay")) as rollback_response:
                confirmation(page)
            assert rollback_response.value.status == 201
            third = api(desktop, "GET", "/releases/3")
            assert third["action"] == "rollback" and third["source_revision"] == 1
            receipt(desktop, third)
            drawer.get_by_role("button", name="刷新目录状态", exact=True).click()
            expect(drawer.get_by_text("已确认", exact=True)).to_be_visible()
            report["checks"].append("history_rollback_creates_new_revision")

            drawer.get_by_role("button", name="编辑期望目录", exact=True).click()
            concurrent = {**writes[0]["body"], "expected_revision": 3}
            assert api(desktop, "POST", "/releases", body=concurrent, expected=201, csrf=csrf)["revision"] == 4
            drawer.get_by_role("button", name="刷新目录状态", exact=True).click()
            expect(drawer.get_by_text("目录版本已被其他操作更新。本次编辑基线保留不变，请核对后重新载入。", exact=True)).to_be_visible()
            expect(drawer.get_by_role("button", name="发布期望目录", exact=True)).to_be_disabled()
            expect(drawer.get_by_text("编辑基线 v3", exact=True)).to_be_visible()
            drawer.get_by_role("button", name="重新载入期望目录", exact=True).click()
            confirmation(page)
            expect(drawer.get_by_text("编辑基线 v4", exact=True)).to_be_visible()
            drawer.get_by_role("button", name="清空全部映射", exact=True).click()
            drawer.get_by_role("button", name="发布期望目录", exact=True).click()
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(path + "/releases")) as withdrawal_response:
                confirmation(page)
            assert withdrawal_response.value.status == 201
            fifth = api(desktop, "GET", "/releases/5")
            assert json.loads(fifth["configuration_json"])["models"] == []
            receipt(desktop, fifth)
            drawer.get_by_role("button", name="刷新目录状态", exact=True).click()
            expect(drawer.get_by_text("已确认", exact=True)).to_be_visible()
            report["checks"].extend(["concurrent_revision_does_not_rebase_draft", "explicit_empty_catalog_withdrawal"])
            desktop.close()
            report["closed_contexts"] += 1
            page.video.save_as(str(private / "gateway-catalog.webm"))
            report["recordings"].append("gateway-catalog.webm")

            mobile, _ = context(width=375, height=812)
            page, drawer = visit(mobile)
            expect(drawer.get_by_text("已确认", exact=True)).to_be_visible()
            report["mobile_layout"] = page.evaluate("""() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth, overflowing: [...document.querySelectorAll('body *')].map(el => ({tag:el.tagName,cls:String(el.className),left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right})).filter(el => el.right>innerWidth+1 && el.left>=0).slice(0,20)})""")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile_page_overflow"
            drawer.get_by_role("button", name="编辑期望目录", exact=True).click()
            expect(drawer.get_by_label("Sub2API 逻辑实例标识", exact=True)).to_be_disabled()
            screenshot(page, "catalog-mobile.png")
            page.set_viewport_size({"width": 812, "height": 375})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "landscape_page_overflow"
            page.add_style_tag(content="html { font-size: 24px; }")
            expect(drawer.get_by_role("button", name="发布期望目录", exact=True)).to_be_enabled()
            report["checks"].extend(["375px_and_landscape_layout", "reduced_motion_and_large_root_text", "instance_fixed_after_first_publication"])
            mobile.close()
            report["closed_contexts"] += 1

            # More than one history page, using the real API, never fabricated
            # pagination responses. The current empty directory stays managed.
            admin, csrf = context()
            for expected in range(5, 56):
                api(admin, "POST", "/releases", body={"instance_id": "demo-studio", "expected_revision": expected, "models": []}, expected=201, csrf=csrf)
            page, drawer = visit(admin)
            drawer.get_by_role("tab", name="发布历史", exact=True).click()
            drawer.get_by_role("button", name="下一页", exact=True).click()
            expect(drawer.get_by_text("第 2 页", exact=True)).to_be_visible()
            drawer.get_by_role("button", name="下一页", exact=True).click()
            expect(drawer.get_by_text("第 3 页", exact=True)).to_be_visible()
            drawer.get_by_role("row").filter(has_text="v1 · 发布").get_by_role("button", name="查看版本", exact=True).click()
            expect(drawer.get_by_role("heading", name="冻结版本 v1", exact=True)).to_be_visible()
            report["checks"].append("actual_history_over_50_versions")
            admin.close()
            report["closed_contexts"] += 1

            observer, _ = context(observer=True)
            page, drawer = visit(observer, from_models=True)
            expect(drawer.get_by_role("button", name="编辑期望目录", exact=True)).to_have_count(0)
            drawer.get_by_role("tab", name="发布历史", exact=True).click()
            drawer.get_by_role("button", name="查看版本", exact=True).first.click()
            expect(drawer.get_by_role("button", name="回滚为新版本", exact=True)).to_have_count(0)
            denied = observer.request.post(origin + path + "/replay", data={"action": "rollback", "expected_revision": 56, "source_revision": 1}, headers={"Origin": origin, "X-CSRF-Token": observer.request.get(origin + "/admin/api/v1/session").json()["csrf_token"], "If-Match": '"56"', "Idempotency-Key": "observer-denied"})
            assert denied.status == 403
            report["checks"].append("observer_read_only_ui_and_server_permission")
            observer.close()
            report["closed_contexts"] += 1
            report["passed"] = not report["errors"]
        finally:
            for item in contexts:
                item.close()
            report["closed_contexts"] = len(contexts)
            browser.close()
            report["browser_closed"] = True
            (media / "capture.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (media / "api-responses.json").write_text(json.dumps({"synthetic_data": True, "provider_receipts_are_protocol_fixtures": True, "authentication_headers_and_cookies_omitted": True, "responses": responses}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert report["passed"]


if __name__ == "__main__":
    main()
