/** Native IndexedDB + production UI. Synthetic HTTP only; never contacts a GPU. */
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
const temporary = path.join(root, `.local/studio-submission-lookup/${label}`);
const shots = path.join(root, `docs/assets/studio/submission-lookup-${label}`);
const evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([temporary, shots, evidence].map((folder) => mkdir(folder, { recursive: true })));
const report = { started_at: new Date().toISOString(), scope: "Native Chromium IDB transactions and production UI with synthetic HTTP; desktop/390px emulation, not GPU, physical phone or live business authorization", checks: [], screenshots: [], page_errors: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
async function snapshot() {
    const files = {};
    async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else files[path.relative(dist, file).replaceAll("\\", "/")] = createHash("sha256").update(await readFile(file)).digest("hex"); } }
    await visit(dist); return files;
}
const before = await snapshot();
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/task-journal-format"; export * from "./src/services/task-journal-recovery"; export { submitIntent, lookupIntent } from "./src/services/aggregator-engine"; export { AggregatorClient } from "./src/services/api/aggregator/client"; export { useStudioStore } from "./src/stores/use-studio-store"; import localforage from "localforage"; export const profiles = localforage.createInstance({name:"comfy-aggregator-studio",storeName:"profiles",driver:localforage.INDEXEDDB});', resolveDir: web }, bundle: true, format: "esm", platform: "browser", define: { "import.meta.env.VITE_STUDIO_MODE": '"managed"' }, tsconfig: path.join(web, "tsconfig.json"), outfile: path.join(temporary, "probe.mjs"), logLevel: "warning" });
const key = "synthetic-read-only-lookup-key", requests = [], originals = new Map();
let sequence = 0, heldResponse, heldSignal;
const held = new Promise((resolve) => { heldSignal = resolve; });
const jobFor = (request) => ({ id: `job_${(++sequence).toString(16).padStart(32, "0")}`, object: "generation.job", model: request.model, task_type: request.task_type, status: "queued", updated_revision: 1, created_at: 1, artifacts: [] });
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const server = createServer((req, res) => void (async () => {
    const pathname = new URL(req.url, "http://fixture.invalid").pathname;
    const json = (status, value, headers = {}) => { res.writeHead(status, { "Content-Type": "application/json", ...headers }); res.end(JSON.stringify(value)); };
    if (pathname.startsWith("/v1/")) {
        requests.push({ method: req.method, path: pathname, bearer_valid: req.headers.authorization === `Bearer ${key}`, cookie_present: Boolean(req.headers.cookie) });
        if (req.headers.authorization !== `Bearer ${key}`) return json(401, { error: { code: "invalid_key", message: "Synthetic key required" } });
        if (pathname === "/v1/access-policy") return json(200, { scopes: ["jobs:read", "jobs:create", "images:create"], limits: { rpm: { mode: "unlimited", value: null } } }, { "X-Studio-Contract": "1", "X-Studio-Submission-Lookup": "1" });
        if (["/v1/models", "/v1/capabilities", "/v1/jobs"].includes(pathname) && req.method === "GET") return json(200, { object: "list", data: [], has_more: false, next_cursor: null });
        if (/^\/v1\/jobs\/job_[a-f0-9]{32}$/.test(pathname) && req.method === "GET") {
            const original = [...originals.values()].find((item) => pathname.endsWith(item.job.id));
            return original ? json(200, original.job) : json(404, { error: { code: "not_found", message: "Synthetic job missing" } });
        }
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const body = Buffer.concat(chunks).toString("utf8"), request = JSON.parse(body);
        const token = req.headers["idempotency-key"], digest = req.headers["x-expected-release-digest"];
        if (pathname === "/v1/jobs" && req.method === "POST") {
            assert.ok(!originals.has(token), "Duplicate generation POST is forbidden in this fixture");
            const original = { body, digest, job: jobFor(request) }; originals.set(token, original);
            if (request.client_request_id === "lost-response") return res.destroy();
            if (request.client_request_id === "two-page") { heldResponse = () => json(202, original.job); heldSignal(); return; }
            return json(202, original.job);
        }
        if (pathname === "/v1/jobs/lookup" && req.method === "POST") {
            const original = originals.get(token);
            if (request.client_request_id === "mismatch") return json(409, { error: { code: "submission_mismatch", message: "Synthetic frozen request mismatch" } });
            if (request.client_request_id === "rate-limited") return json(429, { error: { code: "rate_limited", message: "Synthetic rate limit" } }, { "Retry-After": "2" });
            if (request.client_request_id === "bad-response") return json(200, { object: "generation.submission_lookup", outcome: "found", job: null });
            if (request.client_request_id === "wrong-model") return json(200, { object: "generation.submission_lookup", outcome: "found", job: { ...jobFor(request), model: "different-model" } });
            if (!original) return json(200, { object: "generation.submission_lookup", outcome: "not_observed", job: null });
            assert.equal(body, original.body); assert.equal(digest, original.digest);
            return json(200, { object: "generation.submission_lookup", outcome: "found", job: original.job });
        }
        return json(500, { error: { code: "unexpected_write", message: "No upload, cancel or other write expected" } });
    }
    if (pathname === "/blank") { res.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp }); res.end("<!doctype html><title>Owned read-only reconciliation acceptance</title>"); return; }
    let file;
    if (pathname === "/probe.mjs") file = path.join(temporary, "probe.mjs");
    else if (pathname.startsWith("/studio/")) { file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); if (file !== dist && !file.startsWith(dist + path.sep)) throw new Error("Invalid owned path"); try { if (!(await stat(file)).isFile()) file = path.join(dist, "index.html"); } catch { file = path.join(dist, "index.html"); } }
    else { res.writeHead(404); res.end(); return; }
    const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };
    res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": path.basename(file).startsWith("schema-worker-") ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
})().catch((error) => { report.handler_failure = error.message; if (!res.headersSent) res.writeHead(500); res.end(); }));
let browser; const contexts = [], pages = [];
async function pageIn(context, origin) { const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message)); await page.goto(origin + "/blank"); return page; }
async function setup(page, advertised = true, selectedKey = key) {
    return page.evaluate(async ({ selectedKey, advertised }) => { const j = await import("/probe.mjs"); const client = new j.AggregatorClient(location.origin, selectedKey); j.useStudioStore.setState({ client, activeProfileId: "native", catalog: { models: [], policy: { scopes: ["jobs:read"], limits: {} }, releaseGuard: true, submissionLookup: advertised } }); return { profileId: "native", origin: client.origin, fingerprint: client.fingerprint }; }, { selectedKey, advertised });
}
async function seed(page, id, stage = "unknown", imported = false) {
    return page.evaluate(async ({ id, stage, imported }) => { const j = await import("/probe.mjs"); const client = j.useStudioStore.getState().client; const identity = { profileId: "native", origin: client.origin, fingerprint: client.fingerprint }; const intent = { version: 1, id, identity, projectId: id, nodeId: id, body: ` { "model":"lookup-image", "task_type":"text_to_image", "input":{"prompt":"原样保留，素材与任务不补发"}, "parameters":{}, "client_request_id":"${id}" } `, idempotencyKey: `studio-job-${id}`, releaseDigest: "b".repeat(64), stage, activeNodeKey: `${identity.fingerprint}:${id}:${id}`, output: { mediaTypes: ["image/png"], maxBytes: 1024 }, createdAt: 1, updatedAt: 1 }; if (imported) await j.restoreTaskJournal(j.createTaskJournal([intent], identity), identity, () => true); else await j.journalPut("intents", intent); return await j.journalGet("intents", id); }, { id, stage, imported });
}
const createCount = () => requests.filter((item) => item.path === "/v1/jobs" && item.method === "POST").length;
async function shot(page, name) { const file = path.join(shots, `${name}.png`); await page.screenshot({ path: file, fullPage: true }); const bytes = await readFile(file); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), viewport: page.viewportSize() }); }
try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true });
    const native = await browser.newContext(); contexts.push(native);
    const page = await pageIn(native, origin); await setup(page);
    const original = await seed(page, "lost-response", "prepared");
    const lost = await page.evaluate(async () => { const j = await import("/probe.mjs"); try { await j.submitIntent("lost-response"); } catch {} return j.journalGet("intents", "lost-response"); });
    assert.equal(lost.stage, "unknown"); assert.equal(createCount(), 1); assert.equal(lost.body, original.body);
    check("one accepted generation POST with a lost response remains a durable unknown intent");
    await page.reload(); await setup(page);
    const recovered = await page.evaluate(async () => { const j = await import("/probe.mjs"); return j.lookupIntent("lost-response"); });
    assert.equal(recovered.job.id, originals.get(original.idempotencyKey).job.id); assert.equal(recovered.body, original.body); assert.equal(recovered.idempotencyKey, original.idempotencyKey); assert.equal(createCount(), 1);
    check("restart then explicit lookup restores the original ID with exact bytes/key/digest and no replay");

    for (const id of ["not-observed", "mismatch", "bad-response", "wrong-model", "rate-limited"]) {
        const beforeRequests = requests.length, beforeCreates = createCount();
        const before = await seed(page, id);
        const after = await page.evaluate(async (id) => { const j = await import("/probe.mjs"); try { await j.lookupIntent(id); } catch {} return j.journalGet("intents", id); }, id);
        assert.equal(after.body, before.body); assert.equal(after.idempotencyKey, before.idempotencyKey); assert.equal(after.releaseDigest, before.releaseDigest); assert.equal(after.activeNodeKey, before.activeNodeKey); assert.equal(after.stage, "unknown"); assert.ok(!after.job && after.pollPaused && after.error);
        assert.equal(createCount(), beforeCreates); assert.equal(requests.length - beforeRequests, 1);
        if (id === "not-observed") assert.match(after.error, /不能证明/);
        check(`${id}: preserves immutable journal and unknown state with one lookup and no fallback`);
    }
    const foreign = await seed(page, "original-key-only");
    for (const [advertised, selectedKey] of [[false, key], [true, "different-synthetic-key"]]) {
        await setup(page, advertised, selectedKey); const before = requests.length;
        const message = await page.evaluate(async () => { const j = await import("/probe.mjs"); try { await j.lookupIntent("original-key-only"); } catch (error) { return error.message; } });
        assert.ok(message); assert.equal(requests.length, before);
        check(advertised ? "different Key is rejected locally without a request" : "older service without advertisement never falls back to create");
    }
    await setup(page);
    const imported = await seed(page, "imported-unknown", "prepared", true);
    const importRequests = requests.length;
    const blocked = await page.evaluate(async () => { const j = await import("/probe.mjs"); try { await j.submitIntent("imported-unknown"); } catch (error) { return error.message; } });
    assert.match(blocked, /阻止补发/); assert.equal(requests.length, importRequests);
    check("importing a prepared no-ID journal cannot turn recovery into a generation submission");
    const importedJob = jobFor(JSON.parse(imported.body)); originals.set(imported.idempotencyKey, { body: imported.body, digest: imported.releaseDigest, job: importedJob });
    const verified = await page.evaluate(async () => { const j = await import("/probe.mjs"); return j.lookupIntent("imported-unknown"); });
    assert.equal(verified.job.id, importedJob.id); assert.equal(verified.recoveryVerified, true); assert.deepEqual(verified.recovered, imported.recovered); assert.equal(verified.body, imported.body);
    check("explicit imported-journal lookup verifies the original job while preserving immutable recovery provenance");

    await seed(page, "two-page", "prepared");
    const other = await pageIn(native, origin); await setup(other);
    const beforeRace = createCount();
    const first = page.evaluate(async () => { const j = await import("/probe.mjs"); return j.submitIntent("two-page"); });
    await held;
    const second = await other.evaluate(async () => { const j = await import("/probe.mjs"); return j.submitIntent("two-page"); });
    assert.ok(second.job); assert.equal(createCount() - beforeRace, 1); heldResponse(); heldResponse = undefined;
    assert.equal((await first).job.id, second.job.id);
    check("two actual pages share an atomic first-send claim: second page only looks up the held original response");
    await native.close();

    const ui = await browser.newContext({ viewport: { width: 1440, height: 960 }, locale: "zh-CN", recordVideo: { dir: temporary } }); contexts.push(ui);
    const uiPage = await pageIn(ui, origin); await setup(uiPage);
    const uiIntent = await seed(uiPage, "ui-readonly", "unknown", true);
    await uiPage.evaluate(async (key) => { const j = await import("/probe.mjs"); const client = j.useStudioStore.getState().client; await j.profiles.setItem("connections", { version: 1, profiles: [{ id: "native", origin: client.origin, fingerprint: client.fingerprint, name: "只读核对合成验收", savedKey: key }], activeProfileId: "native" }); }, key);
    await uiPage.goto(origin + "/studio/tasks");
    const lookup = uiPage.getByRole("button", { name: "只读核对原提交", exact: true }); await lookup.waitFor();
    assert.equal(await uiPage.getByRole("button", { name: "提交已保存请求", exact: true }).count(), 0);
    await shot(uiPage, "desktop-original-submission");
    const uiCreates = createCount(); await lookup.click();
    await uiPage.getByText(/本次未查到已提交记录/).waitFor();
    assert.equal(createCount(), uiCreates); await shot(uiPage, "desktop-not-observed");
    check("production task UI offers only read-only recovery and explains not-observed without resubmitting");
    await uiPage.setViewportSize({ width: 390, height: 844 });
    assert.ok(await uiPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await shot(uiPage, "mobile-not-observed");
    const uiJob = jobFor(JSON.parse(uiIntent.body)); originals.set(uiIntent.idempotencyKey, { body: uiIntent.body, digest: uiIntent.releaseDigest, job: uiJob });
    await lookup.click(); await uiPage.getByText(uiJob.id, { exact: true }).waitFor();
    await uiPage.getByRole("button", { name: "查询原任务", exact: true }).waitFor();
    assert.equal(await lookup.count(), 0); assert.equal(createCount(), uiCreates);
    await shot(uiPage, "mobile-original-job-restored");
    check("390px UI restores the original ID and switches to original-job query without horizontal overflow or generation POST");
    await ui.close(); report.private_recording = path.relative(root, await uiPage.video().path()).replaceAll("\\", "/");
    assert.equal(report.page_errors.length, 0); assert.ok(!report.handler_failure);
    assert.ok(requests.every((item) => item.bearer_valid && !item.cookie_present));
    assert.ok(requests.filter((item) => item.method !== "GET").every((item) => ["/v1/jobs", "/v1/jobs/lookup"].includes(item.path)));
    assert.equal(createCount(), 2); assert.deepEqual(await snapshot(), before);
    report.business_requests = requests; report.generation_posts = createCount(); report.static_artifact_sha256 = before; report.static_artifact_unchanged = true; report.browser_storage_version = 3;
    check("all owned tests preserve production bytes, use Bearer without cookies, perform exactly two intentional first submissions and no upload/cancel");
    report.passed = true;
} catch (error) { report.passed = false; report.failure = error.stack; console.error(error.stack); }
finally {
    if (heldResponse) heldResponse();
    for (const page of pages) await page.close().catch(() => {});
    report.cleanup.pages_closed = pages.every((page) => page.isClosed());
    for (const context of contexts) await context.close().catch(() => {});
    report.cleanup.contexts_closed = contexts.every((context) => context.pages().length === 0);
    if (browser) { await browser.close(); report.cleanup.browser_closed = !browser.isConnected(); }
    await new Promise((resolve) => server.close(resolve)); report.cleanup.server_closed = !server.listening;
    report.completed_at = new Date().toISOString();
    const file = path.join(evidence, `submission-lookup-${label}.json`); await writeFile(file, JSON.stringify(report, null, 2) + "\n"); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
