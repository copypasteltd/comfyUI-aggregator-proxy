from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text("utf-8"))
    origin, media, private = fixture["origin"], Path(fixture["media"]), Path(fixture["private"])
    app, root = Path.cwd(), Path.cwd().parent

    def sources():
        return {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (app / "apps/admin_web/src").rglob("*")
            if p.is_file()
        }

    before = sources()
    checks, errors, contexts, requests, responses = [], [], [], [], []
    report = {
        "passed": False,
        "format_version": 1,
        "at": datetime.now(UTC).isoformat(),
        "synthetic_data": True,
        "real_comfyui": False,
        "real_resource_acceptance": False,
        "full_acceptance": "pending",
        "screenshots": [],
        "browser_closed": False,
        "closed_contexts": 0,
    }
    logged_out, session, desktop, video = False, None, None, None

    def shot(page, name):
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(media / name), full_page=False)
        report["screenshots"].append(
            {
                "path": "screenshots/" + name,
                "sha256": hashlib.sha256((media / name).read_bytes()).hexdigest(),
            }
        )

    def usage(page):
        page.goto(origin + "/admin/models", wait_until="domcontentloaded")
        row = page.locator(".el-table__row").filter(has_text="demo-multimodal-context")
        row.get_by_role("button", name="使用说明", exact=True).click()
        expect(page.get_by_role("heading", name="素材槽位", exact=True)).to_be_visible(
            timeout=20000
        )
        return page.get_by_role("dialog", name="模型使用说明", exact=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            desktop = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 1000},
                locale="zh-CN",
                reduced_motion="reduce",
                record_video_dir=str(private / "recording"),
                record_video_size={"width": 1440, "height": 1000},
            )
            contexts.append(desktop)
            login = desktop.request.post(
                origin + "/admin/api/v1/session",
                data={"username": "demo-admin", "password": fixture["password"]},
                headers={"Origin": origin},
            )
            assert login.status == 200
            session = login.json()
            page = desktop.new_page()
            video = page.video
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.on("request", lambda request: requests.append((request.method, request.url)))
            dialog = usage(page)
            assert page.evaluate("document.documentElement.dataset.c==='lime'")
            assert page.evaluate("document.fonts.check('14px \"Manrope\"')")
            text = dialog.inner_text()
            assert all(
                name in text
                for name in [
                    "source_video",
                    "first_frame",
                    "voice",
                    "coarse_mesh",
                    "script",
                    "源视频音轨",
                    "8 小时",
                ]
            )
            example = json.loads(dialog.locator(".usage-json").first.inner_text())
            assert len(example["input"]["media"]) == 4 and "script" in example["input"]["texts"]
            checks.append(
                "Actual Admin usage displays typed media/text roles, source audio policy, producer context and controlled request example from synthetic native API fixture"
            )
            dialog.get_by_role("heading", name="素材槽位", exact=True).scroll_into_view_if_needed()
            shot(page, "typed-model-usage.png")
            responses.append(
                {
                    "route": "/admin/api/v1/public-models/{id}/usage",
                    "synthetic_data": True,
                    "request_example": example,
                }
            )
            dialog.get_by_role("button", name="关闭", exact=True).click()
            page.goto(origin + "/admin/workflows", wait_until="domcontentloaded")
            row = page.locator(".el-table__row").filter(has_text="多模态输入与续拍 · DEMO")
            row.get_by_role("button", name="编辑草稿", exact=True).click()
            raw = page.get_by_role("textbox", name="Workflow 发布包 JSON", exact=True)
            expect(raw).not_to_have_value("{}", timeout=20000)
            original = json.loads(raw.input_value())
            page.get_by_role("button", name="载入当前 JSON", exact=True).click()
            pointer = page.get_by_role(
                "textbox", name="参数绑定 6 JSON Pointer（可选）", exact=True
            )
            # Find the pointer field by the controlled text binding index rather than node IDs.
            pointer_index = next(
                i for i, b in enumerate(original["bindings"], 1) if b.get("pointer")
            )
            pointer = page.get_by_role(
                "textbox", name=f"参数绑定 {pointer_index} JSON Pointer（可选）", exact=True
            )
            expect(pointer).to_have_value("/dialogue")
            pointer.scroll_into_view_if_needed()
            shot(page, "nested-text-binding.png")
            checks.append(
                "Structured Workflow 1.3 editor loads an existing scalar JSON Pointer without replacing the entire frozen JSON document"
            )
            page.get_by_role("tab", name="素材槽位", exact=True).click()
            expect(
                page.get_by_role("textbox", name="素材槽位 2 槽位标识", exact=True)
            ).to_have_value("source_video")
            page.get_by_role(
                "textbox", name="素材槽位 2 功能与用法", exact=True
            ).scroll_into_view_if_needed()
            shot(page, "typed-input-slots.png")
            page.get_by_role("tab", name="文本槽位", exact=True).click()
            description = page.get_by_role("textbox", name="文本槽位 1 功能与用法", exact=True)
            description.fill("Dialogue text / 对话文本；只作为冻结字段内容使用。")
            shot(page, "typed-text-slots.png")
            page.get_by_role("tab", name="视频声音", exact=True).click()
            expect(page.get_by_role("textbox", name="声音来源槽位", exact=True)).to_have_value(
                "source_video"
            )
            page.get_by_role(
                "textbox", name="声音功能说明", exact=True
            ).scroll_into_view_if_needed()
            shot(page, "video-audio-policy.png")
            page.get_by_role("tab", name="续拍上下文", exact=True).click()
            expect(page.get_by_role("textbox", name="保存上下文节点", exact=True)).to_have_value(
                "7"
            )
            expect(
                page.get_by_role("switch", name="需要父任务上下文", exact=True)
            ).not_to_be_checked()
            shot(page, "workflow-continuation.png")
            page.get_by_role("button", name="应用到本地草稿", exact=True).click()
            current = json.loads(raw.input_value())
            assert current["text_slots"][0]["description"].startswith("Dialogue text")
            assert (
                next(b for b in current["bindings"] if b.get("pointer"))["pointer"] == "/dialogue"
            )
            page.locator(".el-drawer:visible").get_by_role(
                "button", name="关闭", exact=True
            ).click()
            checks.append(
                "Media/text/audio/context form edits round trip into the local JSON draft and can be cancelled without saving or publishing"
            )
            page.goto(origin + "/admin/keys", wait_until="domcontentloaded")
            page.get_by_role("button", name="创建 Key", exact=True).click()
            limit = (
                page.locator(".el-form-item")
                .filter(has=page.get_by_text("最多参考图数量", exact=True))
                .get_by_role("spinbutton")
            )
            limit.fill("16")
            limit.press("Tab")
            expect(limit).to_have_value("16")
            assert limit.get_attribute("aria-valuemax") == "16"
            limit.scroll_into_view_if_needed()
            shot(page, "key-sixteen-references.png")
            page.locator(".el-drawer:visible").get_by_role(
                "button", name="取消", exact=True
            ).click()
            checks.append(
                "Key editor allows an explicit maximum of sixteen named image references and cancels without creating a key"
            )
            mobile = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
                locale="zh-CN",
                reduced_motion="reduce",
                storage_state=desktop.storage_state(),
            )
            contexts.append(mobile)
            small = mobile.new_page()
            dialog = usage(small)
            for width in (390, 320):
                small.set_viewport_size({"width": width, "height": 844})
                assert not small.evaluate("document.documentElement.scrollWidth>innerWidth")
                small.keyboard.press("Tab")
                assert small.evaluate("document.activeElement!==document.body")
            small.set_viewport_size({"width": 390, "height": 844})
            dialog.get_by_role("heading", name="素材槽位", exact=True).scroll_into_view_if_needed()
            shot(small, "typed-usage-mobile.png")
            assert small.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
            checks.append(
                "Typed model usage supports 320px and 390px layouts, keyboard focus, reduced motion and the shared lime theme"
            )
            assert not any(
                method not in {"GET", "HEAD", "OPTIONS"}
                and not (method == "POST" and url == origin + "/admin/api/v1/session/activity")
                for method, url in requests
            ), "Unexpected Admin write from local form interaction"
            assert not any(
                url.startswith(("http:", "https:")) and not url.startswith(origin + "/")
                for method, url in requests
            )
            logout = desktop.request.delete(
                origin + "/admin/api/v1/session",
                headers={"Origin": origin, "X-CSRF-Token": session["csrf_token"]},
            )
            assert logout.status in (200, 204)
            logged_out = True
            report["passed"] = not errors and before == sources()
        finally:
            if desktop and session and not logged_out:
                try:
                    logout = desktop.request.delete(
                        origin + "/admin/api/v1/session",
                        headers={"Origin": origin, "X-CSRF-Token": session["csrf_token"]},
                    )
                    logged_out = logout.status in (200, 204)
                except Exception as error:
                    errors.append("logout:" + type(error).__name__)
            for context in reversed(contexts):
                context.close()
            browser.close()
            report.update(
                browser_closed=True,
                closed_contexts=len(contexts),
                sources_unchanged=before == sources(),
                source_sha256=before,
                scope="Real local Admin and native API against explicitly synthetic PostgreSQL fixtures; no physical GPU or production acceptance.",
                checks=checks,
                page_errors=errors,
                non_get_requests=[
                    {"method": method, "path": url.removeprefix(origin)}
                    for method, url in requests
                    if method != "GET"
                ],
                cleanup={
                    "contexts_closed": True,
                    "browser_closed": True,
                    "admin_session_revoked": logged_out,
                },
                browser_process={
                    "status": "passed" if not errors else "failed",
                    "owned_process_tree_closed": True,
                },
            )
            if video:
                (private / "recording-path.json").write_text(
                    json.dumps({"path": str(video.path())}), "utf-8"
                )
            (media / "capture.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8"
            )
            (media.parent / "api-responses.json").write_text(
                json.dumps(
                    {
                        "synthetic_data": True,
                        "authentication_omitted": True,
                        "responses": responses,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "utf-8",
            )
    assert report["passed"] and all(report["cleanup"].values())


if __name__ == "__main__":
    main()
