/** Native, isolated HTTP acceptance. The API below is a synthetic protocol fixture, never GPU evidence. */
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile, mkdir, readdir, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const web = path.join(root, "infinite-canvas/web");
const dist = path.join(web, "dist");
async function artifactSnapshot() {
    const values = {};
    async function visit(folder) {
        for (const entry of await readdir(folder, { withFileTypes: true })) {
            const file = path.join(folder, entry.name);
            if (entry.isDirectory()) await visit(file);
            else values[path.relative(dist, file).replaceAll("\\", "/")] = createHash("sha256").update(await readFile(file)).digest("hex");
        }
    }
    await visit(dist);
    return values;
}
const artifactBefore = await artifactSnapshot();
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const fixture = JSON.parse(await readFile(path.join(web, "tests/fixtures/catalog.json"), "utf8"));
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const evidence = path.join(root, "docs/research/studio-implementation");
const screenshots = path.join(root, `docs/assets/studio/${label}`);
const temporary = path.join(root, `.local/studio-browser/${label}`);
await Promise.all([mkdir(evidence, { recursive: true }), mkdir(screenshots, { recursive: true }), mkdir(temporary, { recursive: true })]);
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/aggregator-budget";', resolveDir: web }, bundle: true, platform: "browser", format: "esm", outfile: path.join(temporary, "journal.mjs"), tsconfig: path.join(web, "tsconfig.json"), logLevel: "warning" });
const key = "synthetic-studio-browser-key-do-not-use";
const report = { started_at: new Date().toISOString(), scope: "active managed UI with synthetic API; plain non-localhost HTTP; desktop/mobile Chromium emulation, not a real phone or GPU", checks: [], requests: [], screenshots: [], page_errors: [], cleanup: { pages_closed: false, contexts_closed: false, browser_closed: false, server_closed: false } };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
const jobs = new Map();
const idempotency = new Map();
let png;
let sequence = 0;
let dropSubmitResponse = true;
let browser;
let server;
let activePage;
const contexts = [];
const pages = [];
const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json" };
const mainCsp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const json = (res, status, value, extra = {}) => { res.writeHead(status, { "Content-Type": "application/json", ...extra }); res.end(JSON.stringify(value)); };
const metadata = (id) => ({ id, bytes: png.length, sha256: createHash("sha256").update(png).digest("hex"), media_type: "image/png", expires_at: Math.floor(Date.now() / 1000) + 8 * 3600, metadata: { width: 512, height: 512 } });
async function body(req) { const chunks = []; for await (const chunk of req) chunks.push(chunk); return Buffer.concat(chunks).toString("utf8"); }
async function handler(req, res) {
    const url = new URL(req.url, "http://fixture.invalid");
    if (url.pathname.startsWith("/v1/")) {
        report.requests.push({ method: req.method, path: url.pathname, bearer_present: req.headers.authorization === `Bearer ${key}`, cookie_present: Boolean(req.headers.cookie), release_header_present: Boolean(req.headers["x-expected-release-digest"]) });
        if (req.headers.authorization !== `Bearer ${key}`) return json(res, 401, { error: { code: "invalid_key", message: "Synthetic key required" } });
        if (url.pathname === "/v1/access-policy") return json(res, 200, { scopes: [...fixture.scopes, "jobs:cancel"], limits: { rpm: { mode: "unlimited", value: null } } }, { "X-Studio-Contract": "1" });
        if (url.pathname === "/v1/models") return json(res, 200, { object: "list", data: fixture.models });
        if (url.pathname === "/v1/capabilities") return json(res, 200, { object: "list", data: fixture.capabilities });
        if (url.pathname === "/v1/jobs" && req.method === "GET") return json(res, 200, { data: [...jobs.values()], has_more: false, next_cursor: null });
        if (url.pathname === "/v1/jobs" && req.method === "POST") {
            const frozen = await body(req);
            const token = req.headers["idempotency-key"];
            const digest = req.headers["x-expected-release-digest"];
            if (digest !== "a".repeat(64)) return json(res, 409, { error: { code: "model_release_changed", message: "Synthetic release changed" } });
            const previous = idempotency.get(token);
            if (previous && previous.body !== frozen) return json(res, 409, { error: { code: "idempotency_conflict", message: "Frozen request changed" } });
            if (previous) {
                if (dropSubmitResponse) return res.destroy();
                return json(res, 202, jobs.get(previous.id), { "Retry-After": "2" });
            }
            const request = JSON.parse(frozen);
            const id = `job_${(++sequence).toString(16).padStart(32, "0")}`;
            const contentId = `content_${sequence.toString(16).padStart(32, "0")}`;
            const job = { id, object: "generation.job", model: request.model, task_type: request.task_type, status: "queued", updated_revision: 1, created_at: Math.floor(Date.now() / 1000), artifacts: [], _content: contentId };
            jobs.set(id, job); idempotency.set(token, { id, body: frozen });
            if (dropSubmitResponse) return res.destroy();
            return json(res, 202, job, { "Retry-After": "2" });
        }
        const jobMatch = /^\/v1\/jobs\/(job_[a-f0-9]{32})$/.exec(url.pathname);
        if (jobMatch && req.method === "GET") {
            const job = jobs.get(jobMatch[1]);
            if (!job) return json(res, 404, { error: { code: "not_found", message: "Unknown synthetic job" } });
            job.status = "succeeded"; job.updated_revision = 2; job.artifacts = [{ id: job._content, position: 0 }];
            return json(res, 200, job);
        }
        const contentMatch = /^\/v1\/content\/(content_[a-f0-9]{32})(\/download)?$/.exec(url.pathname);
        if (contentMatch) {
            if (!contentMatch[2]) return json(res, 200, metadata(contentMatch[1]));
            res.writeHead(200, { "Content-Type": "image/png", "Content-Length": png.length }); res.end(png); return;
        }
        return json(res, 404, { error: { code: "fixture_route_missing", message: "Not implemented by synthetic fixture" } });
    }
    if (url.pathname === "/studio/__probe__/csp.js") {
        res.writeHead(200, { "Content-Type": "application/javascript", "Cache-Control": "no-store" });
        res.end("try { new Function('return 1')(); document.documentElement.dataset.cspProbe = 'allowed'; } catch { document.documentElement.dataset.cspProbe = 'blocked'; }");
        return;
    }
    let target;
    if (url.pathname === "/studio/__probe__/journal.mjs") target = path.join(temporary, "journal.mjs");
    else if (url.pathname.startsWith("/studio/")) {
        target = path.resolve(dist, decodeURIComponent(url.pathname.slice(8)));
        if (!target.startsWith(dist + path.sep) && target !== dist) return json(res, 403, {});
        try { if (!(await stat(target)).isFile()) target = path.join(dist, "index.html"); } catch { target = path.join(dist, "index.html"); }
    } else return json(res, 404, {});
    const worker = path.basename(target).startsWith("schema-worker-");
    res.writeHead(200, { "Content-Type": mime[path.extname(target)] || "application/octet-stream", "Content-Security-Policy": worker ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : mainCsp, "Cache-Control": "no-store" });
    res.end(await readFile(target));
}
async function shot(page, name) {
    const filename = `${name}.png`; const target = path.join(screenshots, filename);
    await page.screenshot({ path: target, fullPage: true });
    report.screenshots.push({ name, file: path.relative(root, target).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(target)).digest("hex") });
}
try {
    server = createServer((req, res) => void handler(req, res).catch(() => json(res, 500, { error: { code: "fixture_error" } })));
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://canvas-studio.test:${server.address().port}`;
    report.origin_kind = "non-localhost plain HTTP";
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--host-resolver-rules=MAP canvas-studio.test 127.0.0.1", "--no-proxy-server"] });
    const context = await browser.newContext({ viewport: { width: 1360, height: 900 } }); contexts.push(context);
    await context.addCookies([{ name: "synthetic_admin_cookie", value: "must-not-cross-business-boundary", url: origin }]);
    const page = await context.newPage(); pages.push(page); activePage = page;
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    png = Buffer.from(await page.evaluate(() => { const canvas = document.createElement("canvas"); canvas.width = canvas.height = 512; const c = canvas.getContext("2d"); c.fillStyle = "#d2f53c"; c.fillRect(0, 0, 512, 512); c.fillStyle = "#0c0c0c"; c.font = "bold 28px sans-serif"; c.fillText("PROTOCOL FIXTURE", 86, 250); c.font = "18px sans-serif"; c.fillText("Synthetic QA asset / no GPU", 112, 290); return canvas.toDataURL("image/png").split(",")[1]; }), "base64");
    await page.goto(`${origin}/studio/config`);
    await page.getByPlaceholder("输入管理员提供的 API Key").fill(key);
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "连接并读取模型" }).click();
    await page.getByText("服务已连接", { exact: true }).waitFor();
    assert.equal(await page.getByText("服务端画布契约尚未就绪", { exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => isSecureContext), false);
    check("connection/catalog/Schema Worker succeed on plain HTTP");
    // A page.evaluate() debugger call can permit unsafe-eval; test an actual page script instead.
    await page.addScriptTag({ url: `${origin}/studio/__probe__/csp.js` });
    assert.equal(await page.locator("html").getAttribute("data-csp-probe"), "blocked");
    check("main document CSP blocks arbitrary function compilation; Ajv stays in isolated Worker");
    await shot(page, "desktop-connection");
    await page.getByRole("link", { name: "模型", exact: true }).click();
    await page.locator(".studio-model-card").first().waitFor();
    assert.equal(await page.locator(".studio-model-card").count(), fixture.models.length);
    assert.equal(await page.locator(".studio-model-warning").count(), 0);
    check("all 11 historical model descriptors accepted through actual Worker");
    await shot(page, "desktop-models");
    await page.getByRole("link", { name: "画布", exact: true }).click();
    await page.getByRole("button", { name: "新建画布", exact: true }).first().click();
    await page.getByRole("button", { name: "新建创作", exact: true }).waitFor();
    const projectUrl = page.url();
    await shot(page, "desktop-canvas");
    await page.getByRole("button", { name: "新建创作", exact: true }).click();
    await page.getByRole("combobox", { name: "生成模型" }).click();
    await page.locator(".ant-select-item-option").filter({ hasText: /lustify/i }).first().click();
    await page.getByRole("textbox", { name: "创作描述", exact: true }).fill("Synthetic fixture: a lime studio");
    await shot(page, "desktop-composer");
    await context.route("**/v1/jobs", async (route) => {
      try {
        if (route.request().method() !== "POST") return route.continue();
        const frozen = route.request().postData();
        const committed = await page.evaluate(async (body) => {
            const db = await new Promise((resolve, reject) => { const req = indexedDB.open("comfy-aggregator-studio:journal"); req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); });
            try { return await new Promise((resolve, reject) => { const tx = db.transaction("intents", "readonly"); const req = tx.objectStore("intents").getAll(); let records; req.onsuccess = () => { records = req.result; }; tx.oncomplete = () => resolve(records.some((record) => record.body === body)); tx.onabort = () => reject(tx.error); }); } finally { db.close(); }
        }, frozen);
        assert.equal(committed, true);
        await route.continue();
      } catch (error) {
        report.route_failure = error.message;
        await route.abort("failed").catch(() => {});
      }
    });
    await page.getByRole("button", { name: "提交 1 项生成任务", exact: true }).click();
    await page.locator(".ant-message-notice-warning").waitFor();
    await page.waitForFunction(() => {
        const toast = document.querySelector(".ant-message-notice-warning")?.getBoundingClientRect();
        const header = document.querySelector(".studio-header")?.getBoundingClientRect();
        return toast && header && toast.y >= header.bottom;
    });
    const toastBounds = await page.locator(".ant-message-notice-warning").last().boundingBox();
    const navigationBounds = await page.locator(".studio-header").boundingBox();
    assert.ok(toastBounds.y >= navigationBounds.y + navigationBounds.height);
    check("uncertain-submit notification leaves primary navigation accessible");
    await page.getByRole("link", { name: "任务", exact: true }).click();
    await page.getByRole("button", { name: "核对原提交", exact: true }).waitFor();
    assert.equal(jobs.size, 1);
    check("POST is preceded by committed durable intent; lost response stays unknown");
    await shot(page, "desktop-unknown-submit");
    dropSubmitResponse = false;
    await page.getByRole("button", { name: "核对原提交", exact: true }).click();
    await page.getByText("已完成", { exact: true }).waitFor();
    assert.equal(jobs.size, 1); assert.equal(idempotency.size, 1);
    check("reconcile uses original body/key/digest and receives exactly the same job");
    await page.goto(projectUrl);
    await page.getByRole("button", { name: "载入产物", exact: true }).click();
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    await shot(page, "desktop-result");
    await page.reload();
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    check("verified result survives page reload and returns to original canvas");
    const probeUrl = `${origin}/studio/__probe__/journal.mjs`;
    await page.evaluate(async (url) => { const j = await import(url); const item = (await j.journalList("results"))[0]; await j.journalDelete("results", item.id); }, probeUrl);
    await page.getByRole("link", { name: "任务", exact: true }).click();
    await page.getByRole("button", { name: "读取服务端历史", exact: true }).click();
    await page.getByRole("button", { name: "查询任务与产物", exact: true }).click();
    await page.getByRole("button", { name: "查看产物信息", exact: true }).click();
    await page.getByRole("button", { name: "校验并载入本机", exact: true }).waitFor();
    await page.getByRole("button", { name: "校验并载入本机", exact: true }).click();
    await page.getByRole("button", { name: /确.?定|OK/ }).last().click();
    await page.waitForFunction(async (url) => (await (await import(url)).journalList("results")).some((item) => item.intentId.startsWith("remote:")), probeUrl);
    assert.equal(jobs.size, 1);
    check("server history queries original scoped jobs and metadata without creating tasks");
    await page.getByRole("link", { name: "画布", exact: true }).click();
    const downloadEvent = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出", exact: true }).first().click();
    const archive = await downloadEvent;
    const archivePath = path.join(temporary, "synthetic-canvas-backup.zip");
    await archive.saveAs(archivePath);
    const { unzipSync } = require("fflate");
    const entries = unzipSync(await readFile(archivePath));
    const manifest = JSON.parse(Buffer.from(entries["projects.json"]).toString("utf8"));
    assert.equal(manifest.studio.semantics, "local-media-snapshot");
    assert.ok(!JSON.stringify(manifest).includes(key));
    for (const file of manifest.projects[0].files) {
        assert.equal(createHash("sha256").update(entries[file.path]).digest("hex"), file.sha256);
        assert.equal(entries[file.path].length, file.bytes);
    }
    assert.ok(manifest.projects[0].files.length > 0);
    await page.locator('input[type="file"]').setInputFiles(archivePath);
    await page.getByText("已导入离线画布和本机原件，任务不会因导入而重新提交", { exact: true }).waitFor();
    assert.equal(jobs.size, 1);
    check("actual canvas ZIP exports verified bytes without credentials and imports without generation");
    await page.goto(projectUrl);
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    check("history-first original reattaches to the original canvas without a second job");
    assert.ok(report.requests.every((request) => request.bearer_present && !request.cookie_present));
    check("all business requests omit same-origin Admin cookie");
    const original = await page.evaluate(async (url) => (await (await import(url)).journalList("intents"))[0], probeUrl);
    const second = await context.newPage(); pages.push(second); await second.goto(`${origin}/studio/tasks`);
    const competing = (id) => ({ ...original, id, nodeId: "cross-tab-node", body: original.body.replace(original.id, id), idempotencyKey: `studio-job-${id}`, job: undefined, stage: "prepared", activeNodeKey: "synthetic-cross-tab-unique-node", canvas: undefined });
    const outcomes = await Promise.allSettled([page.evaluate(async ({ url, value }) => (await import(url)).prepareIntent(value), { url: probeUrl, value: competing("cross-tab-a") }), second.evaluate(async ({ url, value }) => (await import(url)).prepareIntent(value), { url: probeUrl, value: competing("cross-tab-b") })]);
    assert.equal(outcomes.filter((value) => value.status === "fulfilled").length, 1);
    check("two pages cannot commit competing intents for the same active node");
    const immutable = await page.evaluate(async ({ url, id }) => { const journal = await import(url); try { await journal.updateIntent(id, (intent) => ({ ...intent, body: "changed" })); return false; } catch { return (await journal.journalGet("intents", id)).body !== "changed"; } }, { url: probeUrl, id: original.id });
    assert.equal(immutable, true); check("committed original request body cannot be changed");
    const mutate = await page.evaluate(async ({ url, id }) => { const journal = await import(url); const before = await journal.journalGet("intents", id); try { await journal.updateIntent(id, (intent) => { intent.body = "mutated in place"; intent.output.maxBytes++; return intent; }); return false; } catch { const after = await journal.journalGet("intents", id); return after.body === before.body && after.output.maxBytes === before.output.maxBytes; } }, { url: probeUrl, id: original.id });
    assert.equal(mutate, true); check("in-place mutations of frozen body/output are rejected atomically");
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }); contexts.push(mobile);
    const phone = await mobile.newPage(); pages.push(phone); activePage = phone;
    phone.on("pageerror", (error) => report.page_errors.push(error.message));
    await phone.goto(`${origin}/studio/canvas`);
    await phone.getByRole("button", { name: "新建画布", exact: true }).first().click();
    await phone.getByRole("button", { name: "新建创作", exact: true }).waitFor();
    const mobileBounds = await phone.evaluate(() => {
        const r = (node) => { const b = node?.getBoundingClientRect(); return b && { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width }; };
        return { documentWidth: document.documentElement.scrollWidth, width: innerWidth, height: innerHeight, toolbar: r(document.querySelector("[data-canvas-bottom-toolbar]")), header: r(document.querySelector(".studio-header")), title: r(document.querySelector("[data-canvas-title]")) };
    });
    report.mobile_bounds = mobileBounds;
    await shot(phone, "mobile-canvas");
    assert.ok(mobileBounds.documentWidth <= mobileBounds.width + 1);
    assert.ok(mobileBounds.toolbar && mobileBounds.toolbar.left >= 0 && mobileBounds.toolbar.right <= mobileBounds.width + 1 && mobileBounds.toolbar.bottom <= mobileBounds.height + 1);
    check("390px viewport: no document overflow, canvas toolbar remains accessible", mobileBounds);
    const toggle = phone.locator(".studio-canvas-topbar button").first();
    await toggle.click();
    await phone.locator(".studio-canvas-sidepanel").waitFor();
    const panelBounds = await phone.locator(".studio-canvas-sidepanel").boundingBox();
    const topbarBounds = await phone.locator(".studio-canvas-topbar").boundingBox();
    assert.ok(panelBounds.y >= topbarBounds.y + topbarBounds.height - 1, "Mobile panel must leave the canvas toolbar accessible");
    await phone.getByRole("link", { name: "模型", exact: true }).click({ trial: true });
    await toggle.click();
    await phone.locator(".studio-canvas-sidepanel").waitFor({ state: "detached" });
    await shot(phone, "mobile-panel-closed");
    check("mobile side panel opens and closes without covering global navigation or canvas controls", { panelBounds, topbarBounds });
    const zoomBounds = await phone.locator(".studio-canvas-zoom").boundingBox();
    const toolsBounds = await phone.locator("[data-canvas-bottom-toolbar]").boundingBox();
    assert.ok(zoomBounds.y + zoomBounds.height <= toolsBounds.y, "Zoom controls must not cover creation tools");
    await phone.locator("[data-canvas-bottom-toolbar] button").first().click({ trial: true });
    await phone.getByRole("button", { name: "重置视图", exact: true }).click({ trial: true });
    check("zoom and creation controls remain independently clickable on a narrow canvas", { zoomBounds, toolsBounds });
    activePage = page;
    await page.bringToFront();
    await page.setViewportSize({ width: 390, height: 844 });
    const selectedToggle = page.locator(".studio-canvas-topbar button").first();
    if (await selectedToggle.getAttribute("aria-label") !== "收起面板") await selectedToggle.click();
    await page.locator(".studio-canvas-sidepanel").getByTitle("定位到节点", { exact: true }).filter({ has: page.locator("img") }).first().click();
    await page.waitForTimeout(700);
    await selectedToggle.click();
    await page.locator(".studio-canvas-sidepanel").waitFor({ state: "detached" });
    await page.getByRole("button", { name: "居中聚焦", exact: true }).click();
    await page.waitForTimeout(700);
    await page.setViewportSize({ width: 844, height: 390 });
    await page.locator("[data-node-toolbar]").first().waitFor();
    await page.locator("[data-canvas-bottom-toolbar] button").first().click({ trial: true });
    await page.getByRole("button", { name: "重置视图", exact: true }).click({ trial: true });
    await page.getByRole("button", { name: "居中聚焦", exact: true }).click();
    await page.waitForTimeout(700);
    const focusedNodeId = await page.locator("[data-node-toolbar]").first().getAttribute("data-node-toolbar");
    report.focus_diagnostics = await page.evaluate((id) => { const node = document.querySelector(`[data-node-id="${id}"]`); const area = node?.parentElement; return { visibility: document.visibilityState, id, node: node?.getBoundingClientRect().toJSON(), transform: area?.style.transform, parents: [area?.parentElement, area?.parentElement?.parentElement, document.querySelector(".studio-content")].map((item) => ({ rect: item?.getBoundingClientRect().toJSON(), scrollTop: item?.scrollTop, scrollLeft: item?.scrollLeft })) }; }, focusedNodeId);
    const focusedImage = await page.locator(`[data-node-id="${focusedNodeId}"] img[src^="blob:"]`).first().boundingBox();
    const fixedDock = await page.locator("[data-canvas-bottom-toolbar]").boundingBox();
    const fixedTop = await page.locator(".studio-canvas-topbar").boundingBox();
    const nodeActions = await page.locator("[data-node-toolbar]").first().boundingBox();
    report.selected_landscape_bounds = { focusedImage, fixedDock, fixedTop, nodeActions };
    assert.ok(focusedImage.y + focusedImage.height <= fixedDock.y);
    assert.ok(nodeActions.y >= fixedTop.y + fixedTop.height);
    await shot(page, "selected-node-landscape-controls");
    check("selected node toolbar cannot intercept fixed controls after portrait-to-landscape resizing");
    assert.deepEqual(report.page_errors, []);
    assert.equal(report.route_failure, undefined);
    report.passed = true;
} catch (error) {
    report.passed = false; report.failure = error instanceof Error ? error.message : String(error);
    if (activePage && !activePage.isClosed()) await shot(activePage, "failure").catch(() => {});
} finally {
    for (const page of pages) if (!page.isClosed()) await page.close().catch(() => {});
    report.cleanup.pages_closed = pages.every((page) => page.isClosed());
    for (const context of contexts) await context.close().catch(() => {});
    report.cleanup.contexts_closed = true;
    if (browser) { await browser.close(); report.cleanup.browser_closed = true; }
    if (server) { await new Promise((resolve) => server.close(resolve)); report.cleanup.server_closed = true; }
    report.finished_at = new Date().toISOString();
    const artifactAfter = await artifactSnapshot();
    report.static_artifact_sha256 = artifactAfter;
    report.static_artifact_unchanged = JSON.stringify(artifactBefore) === JSON.stringify(artifactAfter);
    report.passed = report.passed && report.static_artifact_unchanged;
    await writeFile(path.join(evidence, `browser-${label}.json`), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, failure: report.failure, report: `browser-${label}.json`, cleanup: report.cleanup }));
    if (!report.passed) process.exitCode = 1;
}
