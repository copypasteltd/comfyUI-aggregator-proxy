"""Real Edge/UI interactions with bounded synthetic Workflow API fixtures.

The login and static build use the local HTTPS application. Synthetic channels,
commands and media never enter the application database or any Windows Worker.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "docs/evidence/2026-10-22"
ORIGIN = "https://127.0.0.1:8443"


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    password = (ROOT / ".local/bootstrap-admin.password").read_text(encoding="utf-8").strip()
    started = time.monotonic()
    checks: list[str] = []
    errors: list[str] = []
    requests: dict[str, Any] = {}
    tenant, project = "ten_" + "1" * 32, "prj_" + "2" * 32
    node_id, gpu_id = "node_" + "3" * 32, "gpu_" + "4" * 32
    workflow_id, release_id = "wf_" + "5" * 32, "wfr_" + "6" * 32
    model_id, operation_id = "mdl_" + "7" * 32, "op_" + "8" * 32
    job_id, artifact_id = "job_" + "9" * 32, "content_" + "a" * 32
    channel_ids = ["ch_" + "b" * 32, "ch_" + "c" * 32]
    approved = False
    channels = [
        {
            "id": identifier,
            "tenant_id": tenant,
            "project_id": project,
            "name": "合成渠道" + str(index + 1),
            "node_id": node_id,
            "gpu_id": gpu_id,
            "base_url": "http://127.0.0.1:18" + str(188 + index),
            "has_authorization": False,
            "enabled": True,
            "draining": False,
            "revision": 7,
            "desired_revision": 3,
            "applied_revision": 3,
            "verified_revision": 3,
            "deleted_at": None,
            "observed_at": 1792683000,
        }
        for index, identifier in enumerate(channel_ids)
    ]
    workflow = {
        "id": workflow_id,
        "tenant_id": tenant,
        "project_id": project,
        "name": "合成 Workflow 界面验证",
        "task_type": "text_to_image",
        "revision": 2,
        "archived": False,
        "created_at": 1792683000,
    }
    model = {
        "id": model_id,
        "tenant_id": tenant,
        "project_id": project,
        "alias": "synthetic-ui-image",
        "task_type": "text_to_image",
        "release_id": release_id,
        "channel_ids": channel_ids,
        "defaults": {"size": "1024x1024", "quality": "standard", "n": 1},
        "revision": 4,
        "published": False,
    }
    accepted = {
        "id": job_id,
        "object": "generation.job",
        "model": model["alias"],
        "task_type": "text_to_image",
        "status": "queued",
        "created_at": 1792683000,
        "updated_revision": 1,
        "error": None,
    }
    job = {
        **accepted,
        "status": "succeeded",
        "tenant_id": tenant,
        "project_id": project,
        "key_id": "key_" + "d" * 32,
        "priority": 0,
        "is_validation": True,
        "cancellation_requested": False,
        "retry_of": None,
        "current_attempt_id": None,
        "release_id": release_id,
        "attempts": [],
        "retries": [],
        "artifacts": [artifact_id],
    }

    def page_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"data": rows, "page": 1, "has_more": False}

    def route_api(route: Route) -> None:
        nonlocal approved
        request = route.request
        path = urlparse(request.url).path
        if path == "/admin/api/v1/session" or path.startswith("/admin/api/v1/session/"):
            route.continue_()
            return
        payload: Any = None
        status = 200
        if request.method == "GET":
            if path == "/admin/api/v1/workflows":
                payload = {
                    **page_data([workflow]),
                    "total": 1,
                    "filters": {},
                    "snapshot_at": 1792683000,
                }
            elif path == "/admin/api/v1/workflow-sync-options":
                query = parse_qs(urlparse(request.url).query)
                kind = query["kind"][0]
                rows = (
                    [
                        {
                            "kind": "release",
                            "id": release_id,
                            "version": 1,
                            "digest": "e" * 64,
                            "validated": True,
                        }
                    ]
                    if kind == "release"
                    else [
                        {
                            "kind": "channel",
                            **{
                                key: channel[key]
                                for key in ("id", "name", "revision", "enabled", "draining")
                            },
                        }
                        for channel in channels
                    ]
                )
                if query.get("ids"):
                    rows = [row for row in rows if row["id"] in query["ids"]]
                payload = {
                    **page_data(rows),
                    "kind": kind,
                    "project_id": project,
                    "workflow_id": workflow_id,
                    "snapshot_at": 1792683000,
                    "total": len(rows),
                }
            elif path == "/admin/api/v1/workflows/" + workflow_id:
                payload = {
                    **workflow,
                    "draft": {},
                    "releases": [
                        {"id": release_id, "version": 1, "digest": "e" * 64, "validated": True}
                    ],
                }
            elif path == "/admin/api/v1/channels":
                payload = page_data(channels)
            elif path in ["/admin/api/v1/channels/" + identifier for identifier in channel_ids]:
                channel = next(c for c in channels if c["id"] == path.rsplit("/", 1)[1])
                payload = {**channel, "revisions": []}
            elif path == "/admin/api/v1/nodes":
                payload = {
                    "data": [
                        {
                            "id": node_id,
                            "name": "合成 Windows 协议节点",
                            "enabled": True,
                            "observed_at": 1792683000,
                            "gpus": [
                                {
                                    "id": gpu_id,
                                    "name": "合成 GPU",
                                    "hardware_id": "SYNTHETIC-UI-GPU",
                                    "memory_bytes": None,
                                    "enabled": True,
                                }
                            ],
                        }
                    ]
                }
            elif path.endswith("/queue") and any(identifier in path for identifier in channel_ids):
                channel = next(c for c in channels if c["id"] in path)
                payload = {
                    **channel,
                    "shared_candidates": 0,
                    "assigned_unfinished": 0,
                    "gpu_occupied": False,
                    "gpu_occupancy_uncertain": False,
                    "gpu_management_hold": False,
                    "observation_status": "unknown",
                    "queue_observed_at": None,
                    "comfy_queue_running": None,
                    "comfy_queue_pending": None,
                }
            elif path.endswith("/inventory") and any(
                identifier in path for identifier in channel_ids
            ):
                payload = {
                    "channel_id": channel_ids[0],
                    "observed_at": None,
                    "stale": True,
                    "inventory": {},
                    "bindings": [
                        {
                            "release_id": release_id,
                            "desired_digest": "e" * 64,
                            "applied_digest": "e" * 64,
                            "verified_digest": "e" * 64,
                            "observed_at": 1792683000,
                            "runtime_approved": approved,
                        }
                    ],
                }
            elif path == "/admin/api/v1/workflow-releases/" + release_id:
                payload = {
                    "id": release_id,
                    "workflow_id": workflow_id,
                    "version": 1,
                    "digest": "e" * 64,
                    "validated": True,
                    "validation": {},
                    "manifest": {
                        "runtime_profiles": [
                            {
                                "id": "synthetic-ui-profile",
                                "gpu_hardware_id": "SYNTHETIC-UI-GPU",
                                "execution_timeout_seconds": 60,
                            }
                        ]
                    },
                }
            elif path == "/admin/api/v1/public-models":
                payload = {**page_data([model]), "snapshot_at": 1792683000, "total": 1}
            elif path == "/admin/api/v1/model-mapping-options":
                query = parse_qs(urlparse(request.url).query)
                assert query["project_id"] == [project]
                assert query.get("model_id", [model_id]) == [model_id]
                kind = query["kind"][0]
                rows: list[dict[str, Any]]
                if kind == "channel":
                    rows = [
                        {
                            "kind": kind,
                            **{
                                key: channel[key]
                                for key in ("id", "name", "enabled", "draining", "revision")
                            },
                        }
                        for channel in channels
                    ]
                elif kind == "workflow":
                    rows = [
                        {
                            "kind": kind,
                            **{
                                key: workflow[key]
                                for key in ("id", "name", "task_type", "archived")
                            },
                        }
                    ]
                elif kind == "release":
                    rows = [
                        {
                            "kind": kind,
                            "id": release_id,
                            "workflow_id": workflow_id,
                            "task_type": "text_to_image",
                            "version": 1,
                            "validated": True,
                        }
                    ]
                else:
                    assert kind == "preset" and query["release_id"] == [release_id]
                    rows = [
                        {
                            "kind": kind,
                            "id": "preset_0",
                            "release_id": release_id,
                            "task_type": "text_to_image",
                            "defaults": model["defaults"],
                            "usable": True,
                        }
                    ]
                if query.get("ids"):
                    rows = [row for row in rows if row["id"] in query["ids"]]
                payload = {
                    **page_data(rows),
                    "kind": kind,
                    "project_id": project,
                    "snapshot_at": 1792683000,
                    "total": len(rows),
                }
            elif path == "/admin/api/v1/operations":
                payload = page_data(
                    [
                        {
                            "id": operation_id,
                            "command": "apply_workflow_release",
                            "status": "partial_failed",
                            "revision": 3,
                            "created_at": 1792683000,
                            "summary": {"target_count": 2},
                        }
                    ]
                )
            elif path == "/admin/api/v1/operations/" + operation_id:
                payload = {
                    "id": operation_id,
                    "command": "apply_workflow_release",
                    "status": "partial_failed",
                    "has_more": False,
                    "targets": [
                        {
                            "id": "cmd_" + str(i + 1) * 32,
                            "channel_id": identifier,
                            "status": "failed" if i else "succeeded",
                            "receipt": {
                                "success": False,
                                "error_code": "synthetic_dependency_missing",
                            }
                            if i
                            else {
                                "success": True,
                                "applied_revision": 3,
                                "environment_verified": True,
                            },
                        }
                        for i, identifier in enumerate(channel_ids)
                    ],
                }
            elif path == "/admin/api/v1/queues/jobs":
                payload = page_data([job])
            elif path == "/admin/api/v1/jobs/" + job_id:
                payload = job
            elif path in (
                "/admin/api/v1/queues/snapshot",
                "/admin/api/v1/queues/statistics",
                "/admin/api/v1/events",
            ):
                route.continue_()
                return
        elif request.method == "POST":
            body = request.post_data_json
            token = request.headers.get("idempotency-key", "")
            assert len(token) >= 16
            if path == "/admin/api/v1/workflow-syncs":
                assert body == {
                    "direction": "push",
                    "release_id": release_id,
                    "release_digest": "e" * 64,
                    "targets": [
                        {"channel_id": identifier, "expected_revision": 7}
                        for identifier in channel_ids
                    ],
                }
                requests["sync"] = body
                payload, status = {"operation_id": operation_id, "status": "pending"}, 202
            elif path == "/admin/api/v1/workflow-releases/" + release_id + "/validate":
                assert (
                    body["model_id"] == model_id
                    and body["expected_model_revision"] == 4
                    and body["expected_channel_revision"] == 7
                    and body["sample_budget"] == 1
                )
                assert (
                    body["channel_id"] == channel_ids[0] and body["request"]["parameters"]["n"] == 1
                )
                requests["validation"] = True
                payload, status = accepted, 202
            elif path.endswith("/workflow-bindings/approvals"):
                assert body == {
                    "release_id": release_id,
                    "profile_id": "synthetic-ui-profile",
                    "evidence_id": artifact_id,
                    "expected_revision": 7,
                }
                assert request.headers["if-match"] == '"7"'
                requests["approval"] = True
                approved = True
                channels[0]["revision"] = 8
                payload = {"approved": True, "revision": 8}
        if payload is None:
            errors.append("unexpected_synthetic_api_request")
            payload, status = {"error": {"message": "Unexpected fixture request"}}, 500
        route.fulfill(status=status, json=payload, headers={"Cache-Control": "no-store"})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = None
        try:
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1440, "height": 1000}, locale="zh-CN"
            )
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
            page.get_by_role("button", name="同步到渠道", exact=True).click()
            dialog = page.get_by_role("dialog", name="同步 Workflow 到渠道", exact=True)
            dialog.get_by_role("combobox", name="同步冻结版本", exact=True).locator(
                'xpath=ancestor::div[contains(@class,"el-select__wrapper")]'
            ).click()
            page.get_by_role("option", name="v1 · " + release_id, exact=True).click()
            dialog.get_by_role("combobox", name="同步目标渠道", exact=True).locator(
                'xpath=ancestor::div[contains(@class,"el-select__wrapper")]'
            ).click()
            page.get_by_role("option", name="合成渠道1", exact=True).click()
            page.get_by_role("option", name="合成渠道2", exact=True).click()
            page.keyboard.press("Escape")
            dialog.get_by_role("button", name="推送并排队", exact=True).click()
            expect(dialog).not_to_be_visible()
            assert "sync" in requests
            checks.append("multi_channel_push_uses_one_versioned_request")
            page.get_by_role("link", name="操作中心", exact=True).first.click()
            page.get_by_role("button", name="查看目标", exact=True).click()
            targets = page.get_by_role("dialog", name="管理操作目标", exact=True)
            expect(targets.get_by_text("synthetic_dependency_missing", exact=True)).to_be_visible()
            expect(targets.get_by_text("环境已验证", exact=True)).to_be_visible()
            page.screenshot(
                path=str(EVIDENCE / "admin-workflow-targets-fixture.png"), full_page=True
            )
            targets.get_by_role("button", name="关闭此对话框", exact=True).click()
            checks.append("partial_failure_target_receipts_are_visible")
            page.get_by_role("link", name="模型与路由", exact=True).click()
            page.get_by_role("button", name="验证样本", exact=True).click()
            validation = page.get_by_role("dialog", name="提交 Workflow 验证样本", exact=True)
            validation.get_by_label("验证渠道", exact=True).focus()
            validation.get_by_label("验证渠道", exact=True).press("ArrowDown")
            page.get_by_role("option", name="合成渠道1", exact=True).click()
            validation.get_by_role("button", name="提交单个样本", exact=True).click()
            validation.get_by_role("button", name="查看验证任务", exact=True).click()
            expect(page.get_by_role("heading", name="任务管理", exact=True)).to_be_visible()
            job_drawer = page.get_by_role("dialog", name="任务详情", exact=True)
            expect(job_drawer.get_by_text(artifact_id, exact=True)).to_be_visible()
            job_drawer.get_by_role("button", name="关闭此对话框", exact=True).click()
            assert requests.get("validation") is True
            checks.append("explicit_release_validation_and_job_detail_navigation")
            page.get_by_role("link", name="Windows 渠道", exact=True).click()
            page.get_by_role("row").filter(has_text="合成渠道1").get_by_role(
                "button", name="查看", exact=True
            ).click()
            channel_drawer = page.get_by_role("dialog", name="合成渠道1 · 渠道详情", exact=True)
            channel_drawer.get_by_role("button", name="批准运行", exact=True).click()
            approval = page.get_by_role("dialog", name="批准 GPU 运行画像", exact=True)
            approval.get_by_label("成功验证产物 ID", exact=True).fill(artifact_id)
            approval.get_by_role("button", name="确认运行批准", exact=True).click()
            expect(approval).not_to_be_visible()
            expect(
                channel_drawer.get_by_text("环境已验证 / 运行已批准", exact=True)
            ).to_be_visible()
            assert requests.get("approval") is True
            checks.append("gpu_runtime_approval_includes_current_revision_and_evidence")
            assert not errors, "Workflow browser runtime or fixture request errors"
        finally:
            if context is not None:
                context.close()
            browser.close()
    report = {
        "at": datetime.now(UTC).isoformat(),
        "browser": "installed Microsoft Edge, headless",
        "origin": ORIGIN,
        "backend_mode": "real local HTTPS login/build; synthetic bounded route fixtures for Workflow interactions",
        "database_or_worker_mutations": False,
        "real_comfy_or_s3_tested": False,
        "duration_seconds": round(time.monotonic() - started, 2),
        "checks": checks,
        "runtime_errors": errors,
        "browser_context_closed": True,
    }
    (EVIDENCE / "admin-workflow-browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": len(checks),
                "runtime_errors": len(errors),
                "browser_context_closed": True,
                "synthetic_workflow_responses": True,
            }
        )
    )


if __name__ == "__main__":
    main()
