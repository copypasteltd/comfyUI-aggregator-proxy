"""Owned child of test-queue-load.py; three real HTTPS Admin browser sessions."""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import expect, sync_playwright

TRACK = """(() => {
  window.__queueNet = { events: 0, fetches: 0, queries: 0 };
  const Source = window.EventSource;
  window.EventSource = class extends Source {
    constructor(...args) { super(...args); this.__closed = false; window.__queueNet.events++; }
    close() { if (!this.__closed) { this.__closed = true; window.__queueNet.events--; } super.close(); }
  };
  const original = window.fetch;
  window.fetch = async (...args) => {
    const url = String(args[0]?.url ?? args[0]);
    const queue = url.includes('/admin/api/v1/queues/');
    if (queue) { window.__queueNet.fetches++; window.__queueNet.queries++; }
    try { return await original(...args); } finally { if (queue) window.__queueNet.fetches--; }
  };
})();"""


def write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def wait_network(page: Any, expression: str) -> None:
    # Direct CDP evaluation respects the app's CSP; wait_for_function(string)
    # invokes eval in Playwright's injected page helper and needs unsafe-eval.
    deadline = time.monotonic() + 10
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, "queue_connection_cleanup_timeout"
        page.wait_for_timeout(50)


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    app = Path(__file__).resolve().parents[1]
    assert root.parent == (app / ".local/queue-load").resolve()
    config = json.loads((root / "browser-config.json").read_text(encoding="utf-8"))
    samples: list[dict[str, Any]] = []
    durations: list[float] = []
    statistics_durations: list[float] = []
    statistics_responses: list[int] = []
    errors: list[str] = []
    latest: dict[int, dict[str, Any]] = {}
    contexts, pages = [], []
    report: dict[str, Any] = {"passed": False, "samples": samples, "runtime_errors": errors, "all_contexts_closed": False, "hidden_connections_closed": False, "hidden_polling_stopped": False, "logout_connections_closed": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            for index in range(3):
                context = browser.new_context(ignore_https_errors=True, viewport={"width": 1536, "height": 1080})
                contexts.append(context)
                context.add_init_script(TRACK)
                page = context.new_page()
                pages.append(page)
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                starts: dict[str, float] = {}
                def request_started(request: Any, starts: dict[str, float] = starts) -> None:
                    if any(path in request.url for path in ("/admin/api/v1/queues/snapshot", "/admin/api/v1/queues/statistics")):
                        starts[request.url] = time.monotonic()
                def snapshot(response: Any, index: int = index, starts: dict[str, float] = starts) -> None:
                    if "/admin/api/v1/queues/statistics" in response.url:
                        statistics_responses.append(response.status)
                        assert response.status == 200, "statistics_http_failed_under_load"
                        statistics = response.json()
                        assert len(statistics["buckets"]) == 24 and len(statistics["task_types"]) == 7 and len(statistics["failure_classes"]) == 6
                        assert statistics["totals"]["completed"] == 0 and statistics["success_rate_bps"] is None
                        if response.url in starts:
                            statistics_durations.append(time.monotonic() - starts.pop(response.url))
                    if "/admin/api/v1/queues/snapshot" in response.url and response.status == 200:
                        payload = response.json()
                        # response.json() may pump another response callback while
                        # waiting for the body. Preserve monotonic test observation.
                        if payload["states"].get("preparing", 0) >= latest.get(index, {}).get("states", {}).get("preparing", 0):
                            latest[index] = payload
                        if response.url in starts:
                            durations.append(time.monotonic() - starts.pop(response.url))
                page.on("request", request_started)
                page.on("response", snapshot)
                page.goto(config["origin"] + "/admin/", wait_until="domcontentloaded")
                page.get_by_label("用户名", exact=True).fill(config["username"])
                page.get_by_label("密码", exact=True).fill(config["password"])
                page.get_by_role("button", name="登录", exact=True).click()
                expect(page.get_by_role("heading", name="运行总览", exact=True)).to_be_visible()
                expect(page.locator(".statistics-panel .history-metrics")).to_be_visible()
                expect(page.locator(".metric").filter(has_text="未结束任务").locator("strong")).to_have_text("500")
                expect(page.locator(".metric").filter(has_text="占用中的物理 GPU").locator("strong")).to_have_text("20")
                assert latest[index]["global_unassigned_queued"] == 480 and len(latest[index]["channels"]) == 40
            write(root / "browser-ready.json", {"sessions": 3, "initial_unfinished": 500, "initial_occupied_gpus": 20})
            for wave in range(10):
                deadline = time.monotonic() + 20
                while True:
                    path = root / "phase.json"
                    phase = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
                    if phase and phase["wave"] == wave:
                        break
                    assert time.monotonic() < deadline, "parent_phase_missing"
                    pages[0].wait_for_timeout(50)
                for index, page in enumerate(pages):
                    expect(page.locator(".queue-state-count").filter(has_text=f"准备中 {phase['preparing']}")).to_be_visible(timeout=12000)
                    assert latest[index]["states"].get("preparing") == phase["preparing"]
                    assert latest[index]["unfinished"] == 500 and latest[index]["occupied_physical_gpus"] == 20
                    latency = time.monotonic() - phase["before_commit_monotonic"]
                    samples.append({"wave": wave, "session": index + 1, "preparing": phase["preparing"], "seconds": round(latency, 4)})
                write(root / f"ack-{wave}.json", {"sessions_observed": 3})
            pages[0].screenshot(path=str(root / "three-session-overview.png"), full_page=True)
            for page in pages:
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
                wait_network(page, "window.__queueNet.events === 0 && window.__queueNet.fetches === 0")
            report["hidden_connections_closed"] = True
            counts = [page.evaluate("window.__queueNet.queries") for page in pages]
            pages[0].wait_for_timeout(5500)
            assert counts == [page.evaluate("window.__queueNet.queries") for page in pages]
            report["hidden_polling_stopped"] = True
            for page in pages:
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
                wait_network(page, "window.__queueNet.events === 1")
                page.get_by_role("button", name="退出登录", exact=True).click()
                expect(page.get_by_role("button", name="登录", exact=True)).to_be_visible()
                wait_network(page, "window.__queueNet.events === 0 && window.__queueNet.fetches === 0")
            report["logout_connections_closed"] = True
            assert not errors
            report["update_p95_seconds"] = round(p95([sample["seconds"] for sample in samples]), 4)
            report["snapshot_http_p95_seconds"] = round(p95(durations), 4)
            report["snapshot_http_requests"] = len(durations)
            assert len(statistics_responses) >= 3 and statistics_durations
            report["statistics_http_requests"] = len(statistics_responses)
            report["statistics_http_p95_seconds"] = round(p95(statistics_durations), 4)
            report["statistics_responses_all_successful"] = all(status == 200 for status in statistics_responses)
            report["measurement"] = "Conservative upper bound: before the first of each pair of authenticated Worker event HTTP calls, through committed states and rendered DOM in each browser. 500 unfinished jobs and 20 occupied GPUs remain throughout."
            assert report["update_p95_seconds"] <= 10
            assert report["statistics_http_p95_seconds"] <= 10
            report["passed"] = True
        finally:
            for context in contexts:
                context.close()
            browser.close()
            report["all_contexts_closed"] = True
            write(root / "browser-report.json", report)
    print(json.dumps({"passed": report["passed"], "samples": len(samples), "update_p95_seconds": report.get("update_p95_seconds"), "contexts_closed": True}), flush=True)


if __name__ == "__main__":
    main()
