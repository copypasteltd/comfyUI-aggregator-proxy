/** Native IDB + shipped UI. Synthetic HTTP only, never GPU or fleet acceptance. */
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const web = path.join(root, "infinite-canvas/web"), dist = path.join(web, "dist");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const temporary = path.join(root, `.local/studio-batch/${label}`), shots = path.join(root, `docs/assets/studio/batch-${label}`), evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([temporary, shots, evidence].map((folder) => mkdir(folder, { recursive: true })));
const fixture = JSON.parse(await readFile(path.join(web, "tests/fixtures/catalog.json"), "utf8"));
const report = { started_at: new Date().toISOString(), scope: "Native Chromium strict IDB transactions and production UI, synthetic HTTP on insecure non-localhost origin; desktop/390px emulation, not real GPU, phone or fleet", checks: [], screenshots: [], page_errors: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
async function snapshot() { const files = {}; async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else files[path.relative(dist, file).replaceAll("\\", "/")] = createHash("sha256").update(await readFile(file)).digest("hex"); } } await visit(dist); return files; }
const before = await snapshot(), worker = Object.keys(before).find((file) => /schema-worker-.*\.js$/.test(file)); assert.ok(worker);
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/task-journal-format"; export * from "./src/services/task-journal-recovery"; export * from "./src/services/aggregator-batch"; export { createIntentBatch, submitPreparedBatch, submitIntent, lookupIntent } from "./src/services/aggregator-engine"; export { AggregatorClient } from "./src/services/api/aggregator/client"; export { useStudioStore } from "./src/stores/use-studio-store"; import localforage from "localforage"; export const profiles = localforage.createInstance({name:"comfy-aggregator-studio",storeName:"profiles",driver:localforage.INDEXEDDB});', resolveDir: web }, bundle: true, format: "esm", platform: "browser", define: { "import.meta.env.VITE_STUDIO_MODE": '"managed"' }, tsconfig: path.join(web, "tsconfig.json"), outfile: path.join(temporary, "probe.mjs"), logLevel: "warning" });
const key = "synthetic-batch-acceptance-key", requests = [], originals = new Map();
let sequence = 0, lostOrdinal = -1, holdNext = false, heldResponse, heldSignal;
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const creates = () => requests.filter((item) => item.method === "POST" && item.path === "/v1/jobs").length;
const server = createServer((req, res) => void (async () => {
    const pathname = new URL(req.url, "http://fixture.invalid").pathname;
    const json = (status, value, headers = {}) => { res.writeHead(status, { "Content-Type": "application/json", ...headers }); res.end(JSON.stringify(value)); };
    if (pathname.startsWith("/v1/")) {
        requests.push({ method: req.method, path: pathname, bearer_valid: req.headers.authorization === `Bearer ${key}`, cookie_present: Boolean(req.headers.cookie) });
        if (req.headers.authorization !== `Bearer ${key}`) return json(401, { error: { code: "invalid_key", message: "Synthetic key required" } });
        if (pathname === "/v1/access-policy") return json(200, { scopes: [...fixture.scopes, "jobs:cancel"], limits: { rpm: { mode: "unlimited", value: null } } }, { "X-Studio-Contract": "1", "X-Studio-Submission-Lookup": "1" });
        if (pathname === "/v1/models") return json(200, { object: "list", data: fixture.models });
        if (pathname === "/v1/capabilities") return json(200, { object: "list", data: fixture.capabilities });
        if (pathname === "/v1/jobs" && req.method === "GET") return json(200, { object: "list", data: [], has_more: false, next_cursor: null });
        if (/^\/v1\/jobs\/job_[a-f0-9]{32}$/.test(pathname) && req.method === "GET") {
            const original = [...originals.values()].find((item) => pathname.endsWith(item.job.id));
            if (!original) return json(404, { error: { code: "not_found", message: "Synthetic job missing" } });
            original.job.status = original.finalStatus || "succeeded"; original.job.updated_revision = 2;
            return json(200, original.job);
        }
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const body = Buffer.concat(chunks).toString("utf8"), request = JSON.parse(body), token = req.headers["idempotency-key"], digest = req.headers["x-expected-release-digest"];
        if (pathname === "/v1/jobs" && req.method === "POST") {
            assert.ok(!originals.has(token), "No generation POST may replay an original key");
            assert.equal(digest, "a".repeat(64)); assert.equal(request.parameters.n, 1);
            const job = { id: `job_${(++sequence).toString(16).padStart(32, "0")}`, object: "generation.job", model: request.model, task_type: request.task_type, status: "queued", updated_revision: 1, created_at: 1, artifacts: [] };
            originals.set(token, { body, digest, job });
            if (creates() === lostOrdinal) { originals.get(token).finalStatus = "failed"; res.writeHead(202, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(JSON.stringify(job)) }); res.flushHeaders(); res.write('{"id":'); setImmediate(() => res.destroy()); return; }
            if (holdNext) { holdNext = false; heldResponse = () => json(202, job); heldSignal(); return; }
            return json(202, job, { "Retry-After": "2" });
        }
        if (pathname === "/v1/jobs/lookup" && req.method === "POST") {
            const original = originals.get(token);
            if (!original) return json(200, { object: "generation.submission_lookup", outcome: "not_observed", job: null });
            assert.equal(body, original.body); assert.equal(digest, original.digest);
            return json(200, { object: "generation.submission_lookup", outcome: "found", job: original.job });
        }
        throw new Error(`Unexpected synthetic mutation: ${req.method} ${pathname}`);
    }
    if (pathname === "/blank") { res.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp }); res.end("<!doctype html><title>Owned batch acceptance</title>"); return; }
    let file;
    if (pathname === "/probe.mjs") file = path.join(temporary, "probe.mjs");
    else if (pathname === "/schema-worker.ts") file = path.join(dist, worker);
    else if (pathname.startsWith("/studio/")) { file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); if (file !== dist && !file.startsWith(dist + path.sep)) throw new Error("Invalid owned path"); try { if (!(await stat(file)).isFile()) file = path.join(dist, "index.html"); } catch { file = path.join(dist, "index.html"); } }
    else { res.writeHead(404); res.end(); return; }
    const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };
    res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": file.endsWith(worker.replaceAll("/", path.sep)) ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
})().catch((error) => { report.handler_failure = error.message; if (!res.headersSent) res.writeHead(500); res.end(); }));
let browser; const contexts = [], pages = [];
async function pageIn(context, origin) { const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message)); await page.goto(origin + "/blank"); return page; }
async function setup(page) { return page.evaluate(async (key) => { const j = await import("/probe.mjs"), client = new j.AggregatorClient(location.origin, key), catalog = await client.catalog(); j.useStudioStore.setState({ client, catalog, activeProfileId: "batch-native" }); return { profileId: "batch-native", origin: client.origin, fingerprint: client.fingerprint }; }, key); }
async function create(page, batchId, count = 3, taskType = "text_to_image") { return page.evaluate(async ({ batchId, count, taskType }) => { const j = await import("/probe.mjs"), model = j.useStudioStore.getState().catalog.models.find((model) => model.task_type === taskType); const request = JSON.parse(JSON.stringify(model.usage.example_request).replace(/asset_REPLACE_WITH_UPLOADED_ID(?:_\d+)?/g, "asset_" + "1".repeat(32))); return j.createIntentBatch(batchId, model, request, Array.from({ length: count }, (_, index) => ({ nodeId: `${batchId}-${index}` })), batchId); }, { batchId, count, taskType }); }
async function shot(page, name) { const file = path.join(shots, `${name}.png`); await page.screenshot({ path: file, fullPage: true }); const bytes = await readFile(file); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), viewport: page.viewportSize() }); }
try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://canvas-batch.test:${server.address().port}`;
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--host-resolver-rules=MAP canvas-batch.test 127.0.0.1"] });
    const native = await browser.newContext(); contexts.push(native); const page = await pageIn(native, origin), identity = await setup(page);
    const maximum = await create(page, "existing-maximum", 15);
    assert.equal(maximum.length, 15); assert.equal(new Set(maximum.map((task) => task.idempotencyKey)).size, 15); assert.equal(creates(), 0);
    check("all 15 members reuse the existing range and commit independent keys before any generation request");
    const failure = await page.evaluate(async () => { const j = await import("/probe.mjs"), model = j.useStudioStore.getState().catalog.models.find((model) => model.task_type === "text_to_image"), add = IDBObjectStore.prototype.add; let writes = 0, message; IDBObjectStore.prototype.add = function (...args) { if (this.name === "intents" && ++writes === 2) throw new DOMException("Injected batch quota failure", "QuotaExceededError"); return add.apply(this, args); }; try { await j.createIntentBatch("quota-batch", model, model.usage.example_request, [0, 1, 2].map((index) => ({ nodeId: `quota-${index}` })), "quota-batch"); } catch (error) { message = error.message; } finally { IDBObjectStore.prototype.add = add; } return { message, retained: (await j.journalList("intents")).filter((task) => task.projectId === "quota-batch").length }; });
    assert.ok(failure.message); assert.equal(failure.retained, 0); assert.equal(creates(), 0);
    check("second member QuotaExceeded abort rolls back the entire native transaction and sends nothing");
    const collision = await create(page, "node-collision", 2);
    const collisionError = await page.evaluate(async () => { const j = await import("/probe.mjs"), model = j.useStudioStore.getState().catalog.models.find((model) => model.task_type === "text_to_image"); try { await j.createIntentBatch("node-collision", model, model.usage.example_request, [{ nodeId: "fresh-before-collision" }, { nodeId: "node-collision-0" }], "another-batch"); } catch (error) { return { message: error.message, partial: (await j.journalList("intents")).some((task) => task.nodeId === "fresh-before-collision") }; } });
    assert.ok(collisionError.message); assert.equal(collisionError.partial, false); assert.equal(collision.length, 2);
    check("active-node collision aborts preceding additions and preserves both original members");
    const modes = [];
    for (const type of ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"]) { const tasks = await create(page, `schema-${type}`, 2, type); assert.equal(tasks.length, 2); assert.equal(tasks[0].releaseDigest, tasks[1].releaseDigest); const a = JSON.parse(tasks[0].body), b = JSON.parse(tasks[1].body); delete a.client_request_id; delete b.client_request_id; assert.deepEqual(a, b); modes.push(type); }
    check("seven task schemas preserve exact inputs, asset references, presets, seed and release across a batch", modes);
    const archived = await page.evaluate(async (identity) => { const j = await import("/probe.mjs"); const tasks = (await j.journalList("intents")).filter((task) => task.projectId === "schema-text_to_image"); const source = j.createTaskJournal(tasks, identity); for (const task of tasks) await j.journalDelete("intents", task.id); const restored = await j.restoreTaskJournal(source, identity, () => true); let blocked; try { await j.submitPreparedBatch("schema-text_to_image", "schema-text_to_image", identity); } catch (error) { blocked = error.message; } return { version: JSON.parse(source).version, restored, counts: j.batchSummary((await j.journalList("intents")).filter((task) => task.projectId === "schema-text_to_image")), blocked }; }, identity);
    assert.equal(archived.version, 2); assert.equal(archived.restored.added, 2); assert.equal(archived.counts.prepared, 0); assert.equal(archived.counts.unverified, 2); assert.equal(creates(), 0);
    check("native v2 import preserves grouping but never authorizes prepared backup resubmission");
    const race = await create(page, "two-page-batch", 2), other = await pageIn(native, origin); await setup(other);
    const held = new Promise((resolve) => { heldSignal = resolve; }); holdNext = true;
    const first = page.evaluate(async (identity) => { const j = await import("/probe.mjs"); return j.submitPreparedBatch("two-page-batch", "two-page-batch", identity); }, identity); await held;
    const second = other.evaluate(async (identity) => { const j = await import("/probe.mjs"); return j.submitPreparedBatch("two-page-batch", "two-page-batch", identity); }, identity);
    await second; heldResponse(); heldResponse = undefined; await first; assert.equal(creates(), 2); assert.ok(race.every((task) => originals.has(task.idempotencyKey)));
    check("two actual pages continue one batch without repeating any member generation POST");
    const mismatch = await page.evaluate(async (identity) => { const j = await import("/probe.mjs"); try { await j.submitPreparedBatch("existing-maximum", "existing-maximum", { ...identity, fingerprint: "f".repeat(64) }); } catch (error) { return error.message; } }, identity);
    assert.match(mismatch, /原 API Key/); assert.equal(creates(), 2);
    check("stale connection identity is rejected before selecting or sending a batch");
    assert.equal(await page.evaluate(() => isSecureContext), false); check("native journal, hashes and schema Worker operate on plain HTTP without WebCrypto");
    await native.close();

    const ui = await browser.newContext({ viewport: { width: 1440, height: 960 }, locale: "zh-CN", recordVideo: { dir: temporary } }); contexts.push(ui); const uiPage = await pageIn(ui, origin); await setup(uiPage);
    await uiPage.evaluate(async (key) => { const j = await import("/probe.mjs"), client = j.useStudioStore.getState().client; await j.profiles.setItem("connections", { version: 1, profiles: [{ id: "batch-ui", origin: client.origin, fingerprint: client.fingerprint, name: "批次恢复合成验收", savedKey: key }], activeProfileId: "batch-ui" }); }, key);
    await uiPage.goto(origin + "/studio/canvas"); await uiPage.getByRole("button", { name: "新建画布", exact: true }).first().click(); await uiPage.getByRole("button", { name: "新建创作", exact: true }).click(); const projectUrl = uiPage.url(); assert.match(new URL(projectUrl).pathname, /^\/studio\/canvas\/[^/]+$/); report.project_url = projectUrl;
    await uiPage.getByRole("combobox", { name: "生成模型" }).click(); await uiPage.locator(".ant-select-item-option").filter({ hasText: /lustify/i }).first().click();
    await uiPage.getByRole("textbox", { name: "创作描述", exact: true }).fill("Synthetic fixture: three independent lime study frames");
    const count = uiPage.getByRole("spinbutton", { name: "任务数量", exact: true }); assert.equal(await count.inputValue(), "1"); await count.fill("3"); await count.blur(); await shot(uiPage, "desktop-composer-three");
    let verifiedCommit = false;
    await ui.route("**/v1/jobs", async (route) => { try { if (route.request().method() !== "POST") return route.continue(); const committed = await uiPage.evaluate(async () => { const request = indexedDB.open("comfy-aggregator-studio:journal"); const db = await new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); }); try { return await new Promise((resolve, reject) => { const tx = db.transaction("intents", "readonly"), read = tx.objectStore("intents").getAll(); let records; read.onsuccess = () => { records = read.result; }; tx.oncomplete = () => resolve(records); tx.onabort = () => reject(tx.error); }); } finally { db.close(); } }); assert.equal(committed.length, 3); assert.equal(new Set(committed.map((task) => task.batch.id)).size, 1); assert.ok(committed.every((task) => task.batch.total === 3)); verifiedCommit = true; await route.continue(); } catch (error) { report.route_failure = error.stack; await route.abort("failed").catch(() => {}); } });
    lostOrdinal = creates() + 2;
    await uiPage.getByRole("button", { name: "提交 3 项生成任务", exact: true }).click(); await uiPage.locator(".ant-message-notice-warning").waitFor(); assert.equal(creates(), 4); assert.ok(verifiedCommit);
    check("real composer defaults to one; all three members commit before first POST and second-response loss stops the third");
    await uiPage.getByRole("link", { name: "任务", exact: true }).click(); const group = uiPage.getByRole("region", { name: "批次概览", exact: true }); await group.getByRole("button", { name: "继续提交未发送项（1）", exact: true }).waitFor(); await shot(uiPage, "desktop-partial-batch");
    const downloadPromise = uiPage.waitForEvent("download"); await uiPage.getByRole("button", { name: "导出任务清单", exact: true }).click(); const downloaded = await downloadPromise, archivePath = path.join(temporary, "pending-batch.json"); await downloaded.saveAs(archivePath); const archive = await readFile(archivePath, "utf8"), backupTasks = JSON.parse(JSON.parse(archive).payload).tasks;
    assert.equal(JSON.parse(archive).version, 2); assert.equal(backupTasks.length, 3); assert.ok(!archive.includes(key)); report.archive = { version: 2, tasks: 3, bytes: Buffer.byteLength(archive), sha256: createHash("sha256").update(archive).digest("hex") };
    check("actual task-page export preserves three original submissions and batch metadata in v2 without the saved API Key");
    await uiPage.reload(); await group.getByRole("button", { name: "继续提交未发送项（1）", exact: true }).waitFor(); assert.equal(creates(), 4);
    await uiPage.setViewportSize({ width: 390, height: 844 }); assert.ok(await uiPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); await shot(uiPage, "mobile-partial-after-restart");
    check("restart preserves the pending and unknown members without automatic POST or mobile overflow");
    const lost = [...originals.values()].find((item) => item.finalStatus === "failed"); lost.job.status = "failed"; lost.job.updated_revision = 2;
    await uiPage.getByRole("button", { name: "只读核对原提交", exact: true }).click(); await group.getByText("失败 1", { exact: true }).waitFor(); assert.equal(creates(), 4);
    await group.getByRole("button", { name: "继续提交未发送项（1）", exact: true }).click(); await uiPage.getByText("本次已确认 1 项，其他原记录保留", { exact: true }).waitFor(); assert.equal(await group.getByRole("button", { name: /继续提交未发送项/ }).count(), 0); assert.equal(creates(), 5); await shot(uiPage, "mobile-independent-results");
    await group.getByText("已完成 2", { exact: true }).waitFor(); const finalTasks = await uiPage.evaluate(async () => { const j = await import("/probe.mjs"); return j.journalList("intents"); }); assert.ok(finalTasks.every((task) => task.job)); assert.equal(finalTasks.find((task) => task.job.status === "failed").job.id, lost.job.id);
    check("explicit continue sends only the never-sent member; failed and successful siblings retain original job IDs");
    await uiPage.setViewportSize({ width: 1440, height: 960 }); await uiPage.goto(projectUrl); const cards = uiPage.locator(".studio-node-card"); await cards.filter({ hasText: "失败" }).getByRole("button", { name: "继续查询", exact: true }).waitFor(); assert.equal(await cards.count(), 3); assert.equal(await cards.filter({ hasText: "已完成" }).count(), 2);
    const queryPath = `/v1/jobs/${lost.job.id}`, queries = requests.filter((request) => request.path === queryPath && request.method === "GET").length;
    const originalQuery = uiPage.waitForResponse((response) => response.url().endsWith(queryPath) && response.request().method() === "GET"); await cards.filter({ hasText: "失败" }).getByRole("button", { name: "继续查询", exact: true }).click(); await originalQuery; assert.equal(requests.filter((request) => request.path === queryPath && request.method === "GET").length, queries + 1); assert.equal(creates(), 5); await shot(uiPage, "desktop-three-result-nodes");
    check("all three reconstructed task cards show real states and the failed card queries its original job without generic retry or new generation");
    await uiPage.goto(origin + "/studio/tasks"); await uiPage.getByRole("button", { name: "导入任务清单", exact: true }).waitFor();
    await uiPage.evaluate(async () => { const j = await import("/probe.mjs"); for (const task of await j.journalList("intents")) await j.journalDelete("intents", task.id); }); await uiPage.reload();
    const beforeImport = creates(), chooser = uiPage.waitForEvent("filechooser"); await uiPage.getByRole("button", { name: "导入任务清单", exact: true }).click(); await (await chooser).setFiles(archivePath); const dialog = uiPage.getByRole("dialog"); await dialog.getByText("确认恢复任务清单", { exact: true }).waitFor(); await dialog.getByRole("button", { name: "仅导入记录", exact: true }).click();
    await group.getByText("备份待核对 3", { exact: true }).waitFor(); assert.equal(await group.getByRole("button", { name: /继续提交未发送项/ }).count(), 0); assert.equal(creates(), beforeImport); await uiPage.setViewportSize({ width: 390, height: 844 }); await shot(uiPage, "mobile-imported-batch-readonly");
    check("actual v2 backup import reconstructs the batch as read-only recovery, including its prepared member, with zero create POST");
    await ui.close(); report.private_recording = path.relative(root, await uiPage.video().path()).replaceAll("\\", "/");
    assert.equal(report.page_errors.length, 0); assert.ok(!report.handler_failure && !report.route_failure); assert.ok(requests.every((request) => request.bearer_valid && !request.cookie_present)); assert.equal(creates(), 5); assert.deepEqual(await snapshot(), before);
    report.business_requests = requests; report.generation_posts = creates(); report.browser_storage_version = 3; report.static_artifact_sha256 = before; report.static_artifact_unchanged = true;
    check("all five intentional first submissions are unique, use Bearer without cookies, and preserve shipped bytes and DB v3"); report.passed = true;
} catch (error) { report.passed = false; report.failure = error.stack; console.error(error.stack); const page = pages.findLast((page) => !page.isClosed()); if (page) { report.failure_url = page.url(); report.failure_page_text = (await page.locator("body").innerText().catch(() => "")).slice(0, 4000); await shot(page, "failure-state").catch(() => {}); } }
finally {
    if (heldResponse) heldResponse(); for (const page of pages) await page.close().catch(() => {}); report.cleanup.pages_closed = pages.every((page) => page.isClosed());
    for (const context of contexts) await context.close().catch(() => {}); report.cleanup.contexts_closed = contexts.every((context) => !context.pages().length);
    if (browser) { await browser.close(); report.cleanup.browser_closed = !browser.isConnected(); } await new Promise((resolve) => server.close(resolve)); report.cleanup.server_closed = !server.listening;
    report.business_requests = requests; report.generation_posts = creates(); report.completed_at = new Date().toISOString(); const file = path.join(evidence, `batch-${label}.json`); await writeFile(file, JSON.stringify(report, null, 2) + "\n"); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
