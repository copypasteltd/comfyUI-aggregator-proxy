"""Real Edge, HTTPS, PostgreSQL, generated media and deliberately lost PATCH ACK."""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from playwright.sync_api import Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence/2026-10-23"
ORIGIN = "https://127.0.0.1:8443"


def fixture(action: str) -> dict:
    result = subprocess.run([str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "scripts/content-browser-fixture.py"), action], cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, check=True, timeout=180)  # noqa: S603 -- fixed owned native fixture helper
    return json.loads(result.stdout.decode("utf-8"))


def main() -> None:
    checks: list[str] = []
    errors: list[str] = []
    lists: list[dict] = []
    patches: list[dict] = []
    preview_requests: list[str] = []
    session_reads: list[str] = []
    report: dict[str, Any] = {"passed": False, "real_https_admin_session": True, "real_postgresql_content_and_preview": True, "synthetic_owned_media": True, "lost_patch_ack_is_injected": True, "checks": checks, "runtime_errors": errors}
    owned = ROOT / ".local/content-browser-fixture.json"
    if owned.exists() and not json.loads(owned.read_text(encoding="utf-8")).get("cleaned"):
        fixture("cleanup")
    try:
        fixture("seed")
    except BaseException:
        if owned.exists() and not json.loads(owned.read_text(encoding="utf-8")).get("cleaned"):
            fixture("cleanup")
        raise
    values = json.loads(owned.read_text(encoding="utf-8"))
    image, video = values["ids"]["image"], values["ids"]["video"]
    lost_next = False
    contexts = []
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                context = browser.new_context(ignore_https_errors=True, viewport={"width": 1360, "height": 1000}, timezone_id="Asia/Taipei")
                contexts.append(context)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(type(error).__name__))

                def observe(request: Any) -> None:
                    parsed = urlparse(request.url)
                    if parsed.path == "/admin/api/v1/content":
                        lists.append(parse_qs(parsed.query))
                    if parsed.path.endswith("/preview"):
                        preview_requests.append(parsed.path)
                    if parsed.path == "/admin/api/v1/session" and request.method == "GET":
                        session_reads.append(request.url)
                page.on("request", observe)

                def intercept(route: Route) -> None:
                    nonlocal lost_next
                    if route.request.method == "PATCH":
                        patches.append({"body": route.request.post_data_json, "token": route.request.headers.get("idempotency-key"), "if_match": route.request.headers.get("if-match"), "has_csrf": bool(route.request.headers.get("x-csrf-token"))})
                        if lost_next:
                            lost_next = False
                            committed = route.fetch()
                            assert committed.status == 200
                            route.fulfill(status=503, json={"error": {"message": "合成测试：保存已提交但响应丢失，请重试"}})
                            return
                    route.continue_()
                page.route("**/admin/api/v1/content/**", intercept)

                def login(target: Any) -> None:
                    target.goto(ORIGIN + "/admin/", wait_until="domcontentloaded")
                    target.get_by_label("用户名", exact=True).fill("admin")
                    target.get_by_label("密码", exact=True).fill((ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip())
                    target.get_by_role("button", name="登录", exact=True).click()
                    expect(target.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()

                def select(label: str, text: str) -> None:
                    page.get_by_role("combobox", name=label, exact=True).press("ArrowDown")
                    page.get_by_role("option", name=text, exact=True).click()

                def open_row(identifier: str, media: str) -> None:
                    page.get_by_text(identifier, exact=True).locator("xpath=ancestor::tr[1]").get_by_role("button", name=media, exact=True).click()
                    expect(page.get_by_role("dialog", name="内容详情").get_by_text(identifier, exact=True)).to_be_visible()

                def close_detail() -> None:
                    page.get_by_role("dialog", name="内容详情").locator(".el-drawer__close-btn").click()
                    expect(page.get_by_role("dialog", name="内容详情")).not_to_be_visible()

                def add_tag(label: str) -> None:
                    combo = page.get_by_role("combobox", name="整理标签", exact=True)
                    combo.fill(label)
                    expect(page.get_by_role("option", name=label, exact=True)).to_be_visible()
                    page.get_by_role("option", name=label, exact=True).click()
                    combo.press("Escape")
                    expect(page.get_by_role("dialog", name="内容详情").get_by_text(label, exact=True)).to_be_visible()

                login(page)
                page.get_by_role("link", name="内容管理", exact=True).click()
                expect(page.get_by_role("heading", name="内容管理", exact=True)).to_be_visible()
                expect(page.get_by_text(image, exact=True)).to_be_visible()
                assert len(page.locator(".el-table__body-wrapper tbody tr").all()) == 50
                assert not preview_requests
                checks.append("real_https_catalog_is_bounded_and_never_autoloads_media")

                before = len(lists)
                select("租户", values["labels"]["tenant"])
                select("项目", values["labels"]["project"])
                key_combo = page.get_by_role("combobox", name="创建密钥", exact=True)
                key_combo.fill("48 浏览器密钥 " + values["run_id"][:8])
                expect(page.get_by_role("button", name="加载更多选项", exact=True)).to_be_visible()
                page.get_by_role("button", name="加载更多选项", exact=True).click()
                page.get_by_role("option", name=values["labels"]["key"], exact=True).click()
                assert len(lists) == before
                page.get_by_role("button", name="应用筛选", exact=True).click()
                expect(page.get_by_text(image, exact=True)).to_be_visible()
                assert lists[-1]["key_id"] == [values["key"]]
                page.get_by_role("button", name="下一页", exact=True).click()
                expect(page.get_by_text("第 2 页", exact=False)).to_be_visible()
                expect(page.get_by_role("button", name="下一页", exact=True)).to_be_disabled()
                assert lists[-1]["page"] == ["2"] and lists[-1]["tenant_id"] == [values["tenant"]]
                page.get_by_role("button", name="上一页", exact=True).click()
                expect(page.get_by_text(image, exact=True)).to_be_visible()
                checks.append("name_options_page_search_and_explicit_apply_preserve_catalog_pagination")

                select("模型", values["labels"]["model"])
                select("Workflow", values["labels"]["workflow"])
                select("发布版本", values["labels"]["release"])
                select("文件类型", "图片")
                select("内容状态", "可用")
                page.get_by_role("textbox", name="关联任务", exact=True).fill(values["job"])
                page.get_by_role("textbox", name="标签", exact=True).fill("48 初始标签")
                start = datetime.now(UTC) + timedelta(hours=7)
                end = start + timedelta(hours=2)
                date_inputs = page.locator('.content-date-filter .el-range-input')
                date_inputs.nth(0).fill(start.strftime("%Y-%m-%d %H:%M:%S"))
                date_inputs.nth(1).fill(end.strftime("%Y-%m-%d %H:%M:%S"))
                date_inputs.nth(1).press("Tab")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                expect(page.locator(".el-table__body-wrapper tbody tr")).to_have_count(1)
                for field in ("model", "workflow", "release", "job"):
                    query_name = {"workflow": "workflow_id", "release": "release_id", "job": "job_id"}.get(field, field)
                    assert lists[-1][query_name] == [values[field]]
                assert int(lists[-1]["created_from"][0]) < time.time() < int(lists[-1]["created_before"][0])
                checks.append("frozen_model_workflow_key_tag_media_state_and_local_time_intersection")

                open_row(image, "image/png")
                drawer = page.get_by_role("dialog", name="内容详情")
                assert drawer.locator("img,video").count() == 0 and not preview_requests
                page.get_by_role("button", name="预览文件", exact=True).click()
                expect(drawer.locator("img")).to_be_visible()
                for _ in range(80):
                    if drawer.locator("img").evaluate("element => element.complete && element.naturalWidth === 24"):
                        break
                    page.wait_for_timeout(50)
                assert drawer.locator("img").evaluate("element => element.naturalWidth") == 24
                assert preview_requests[-1].endswith(image + "/preview")
                page.get_by_role("button", name="关闭预览", exact=True).click()
                expect(drawer.locator("img")).to_have_count(0)
                checks.append("explicit_same_origin_image_preview_reads_verified_bytes_and_releases_on_close")

                add_tag("48 已整理")
                page.get_by_role("button", name="保存标签", exact=True).click()
                expect(page.get_by_text("标签已保存", exact=True)).to_be_visible()
                assert fixture("inspect")["metadata"]["metadata_revision"] == 2
                add_tag("48 重试标签")
                lost_next = True
                page.get_by_role("button", name="保存标签", exact=True).click()
                expect(page.get_by_text("合成测试：保存已提交但响应丢失，请重试", exact=True)).to_be_visible()
                page.get_by_role("button", name="保存标签", exact=True).click()
                expect(page.get_by_role("button", name="保存标签", exact=True)).to_be_disabled()
                assert patches[-2] == patches[-1] and patches[-1]["has_csrf"]
                current = fixture("inspect")
                assert current["metadata"]["metadata_revision"] == 3 and current["tag_audits"] == 2
                checks.append("tag_edit_sends_csrf_ifmatch_and_reuses_idempotency_after_committed_lost_ack")

                add_tag("48 草稿保留")
                csrf = context.request.get(ORIGIN + "/admin/api/v1/session").json()["csrf_token"]
                changed = context.request.patch(ORIGIN + "/admin/api/v1/content/" + image, data={"expected_revision": 3, "tags": ["48 其他管理修改"]}, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf, "If-Match": '"3"', "Idempotency-Key": str(uuid4())})
                assert changed.status == 200
                page.get_by_role("button", name="保存标签", exact=True).click()
                expect(page.get_by_text("内容版本或幂等请求已变化。请重新加载详情，核对标签后保存。当前草稿已保留。", exact=True)).to_be_visible()
                expect(drawer.get_by_text("48 草稿保留", exact=True)).to_be_visible()
                page.get_by_role("button", name="重新加载详情", exact=True).click()
                expect(page.get_by_role("button", name="重新加载详情", exact=True)).to_be_enabled()
                expect(drawer.get_by_text("48 草稿保留", exact=True)).to_be_visible()
                page.get_by_role("button", name="保存标签", exact=True).click()
                expect(page.get_by_role("button", name="保存标签", exact=True)).to_be_disabled()
                assert fixture("inspect")["metadata"]["metadata_revision"] == 5
                checks.append("real_concurrent_revision_conflict_preserves_draft_until_explicit_reload_and_save")
                page.screenshot(path=str(EVIDENCE / "admin-content-tags.png"))
                close_detail()

                page.get_by_role("button", name="重置筛选", exact=True).click()
                expect(page.get_by_text(video, exact=True)).to_be_visible()
                open_row(video, "video/webm")
                assert drawer.locator("video").count() == 0
                page.get_by_role("button", name="预览文件", exact=True).click()
                media = drawer.locator("video")
                expect(media).to_be_visible()
                assert media.evaluate("element => { window.ownedVideo = element; return element.preload === 'none' && !element.autoplay && element.paused }")
                media.evaluate("element => element.play()")
                assert media.evaluate("element => !element.paused && element.videoWidth === 96")
                close_detail()
                assert page.evaluate("window.ownedVideo.paused && !window.ownedVideo.hasAttribute('src')")
                checks.append("real_generated_video_plays_without_autoplay_or_blob_and_drawer_close_clears_src")

                open_row(video, "video/webm")
                page.get_by_role("button", name="预览文件", exact=True).click()
                media = drawer.locator("video")
                media.evaluate("element => { window.ownedVideo = element; return element.play() }")
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
                expect(drawer.locator("video")).to_have_count(0)
                assert page.evaluate("window.ownedVideo.paused && !window.ownedVideo.hasAttribute('src')")
                before_list, before_session, before_preview = len(lists), len(session_reads), len(preview_requests)
                page.wait_for_timeout(750)
                assert len(lists) == before_list
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
                for _ in range(80):
                    if len(lists) > before_list:
                        break
                    page.wait_for_timeout(50)
                assert len(session_reads) > before_session and len(lists) > before_list and len(preview_requests) == before_preview
                checks.append("hidden_page_releases_video_and_visibility_reauthenticates_without_media_reload")
                close_detail()
                open_row(video, "video/webm")
                page.get_by_role("button", name="预览文件", exact=True).click()
                media = drawer.locator("video")
                media.evaluate("element => { window.ownedVideo = element; return element.play() }")
                # Browser Back uses the production history router while the drawer is open.
                page.go_back(wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
                assert page.evaluate("window.ownedVideo.paused && !window.ownedVideo.hasAttribute('src')")
                checks.append("route_unmount_pauses_detached_video_and_removes_its_source")

                page.get_by_role("link", name="内容管理", exact=True).click()
                page.get_by_role("tab", name="输入素材", exact=True).click()
                expect(page.get_by_text(values["ids"]["input"], exact=True)).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(EVIDENCE / "admin-content-mobile.png"), full_page=True)
                checks.append("sensitive_input_catalog_and_390px_filter_layout_remain_usable")

                page.set_viewport_size({"width": 1360, "height": 1000})
                page.get_by_role("tab", name="生成产物", exact=True).click()
                expect(page.get_by_text(video, exact=True)).to_be_visible()
                open_row(video, "video/webm")
                page.get_by_role("button", name="预览文件", exact=True).click()
                drawer.locator("video").evaluate("element => { window.ownedVideo = element; return element.play() }")
                # Revoke the real session, then exercise the store's session-expiry event.
                csrf = context.request.get(ORIGIN + "/admin/api/v1/session").json()["csrf_token"]
                assert context.request.delete(ORIGIN + "/admin/api/v1/session", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status == 204
                page.evaluate("window.dispatchEvent(new Event('session-expired'))")
                expect(page.get_by_role("button", name="登录", exact=True)).to_be_visible()
                assert page.evaluate("window.ownedVideo.paused && !window.ownedVideo.hasAttribute('src')")
                before = len(lists)
                page.wait_for_timeout(750)
                assert len(lists) == before
                checks.append("session_expiry_unmounts_catalog_and_frees_active_media")

                restricted = browser.new_context(ignore_https_errors=True, viewport={"width": 1360, "height": 1000})
                contexts.append(restricted)
                limited = restricted.new_page()
                limited.on("pageerror", lambda error: errors.append(type(error).__name__))
                def restrict(route: Route) -> None:
                    response = route.fetch()
                    if response.status == 200:
                        payload = response.json()
                        payload["permissions"] = [permission for permission in payload["permissions"] if permission not in {"content:manage", "content:sensitive"}]
                        route.fulfill(response=response, json=payload)
                    else:
                        route.fulfill(response=response)
                limited.route("**/admin/api/v1/session", restrict)
                login(limited)
                limited.get_by_role("link", name="内容管理", exact=True).click()
                limited.get_by_role("tab", name="输入素材", exact=True).click()
                expect(limited.get_by_text(values["ids"]["input"], exact=True)).to_be_visible()
                limited.get_by_text(values["ids"]["input"], exact=True).locator("xpath=ancestor::tr[1]").get_by_role("button", name="image/png", exact=True).click()
                limited_drawer = limited.get_by_role("dialog", name="内容详情")
                expect(limited_drawer.get_by_text(values["ids"]["input"], exact=True)).to_be_visible()
                assert limited.get_by_role("button", name="保存标签", exact=True).count() == 0
                assert limited.get_by_role("button", name="预览文件", exact=True).count() == 0
                assert limited.get_by_role("button", name="下载", exact=True).count() == 0
                checks.append("reduced_permission_ui_hides_tag_mutation_and_sensitive_input_preview_download")
                report["permission_reduction_only_in_ui_fixture"] = True
                report["tag_metadata_revision"] = fixture("inspect")["metadata"]["metadata_revision"]
                limited_drawer.locator(".el-drawer__close-btn").click()
                limited.get_by_role("button", name="退出登录", exact=True).click()
                expect(limited.get_by_role("button", name="登录", exact=True)).to_be_visible()
                assert not errors
                report["passed"] = True
            finally:
                report["observed_patch_requests"] = len(patches)
                if not report["passed"]:
                    page.screenshot(path=str(ROOT / '.local/content-management-browser-failed.png'))
                for context in contexts:
                    context.close()
                browser.close()
                report["all_contexts_closed"] = True
    finally:
        report["fixture_cleanup"] = fixture("cleanup")
        report["all_contexts_closed"] = report.get("all_contexts_closed", browser is None)
        (EVIDENCE / "admin-content-management-browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "all_contexts_closed": True, "fixture_cleaned": True}))


if __name__ == "__main__":
    main()
