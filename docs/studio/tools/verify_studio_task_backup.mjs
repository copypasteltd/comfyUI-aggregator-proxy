/** Task-journal recovery: native storage and actual production UI, synthetic service only. */
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const web = path.join(root, "infinite-canvas/web");
const dist = path.join(web, "dist");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const temporary = path.join(root, `.local/studio-task-backup/${label}`);
const shots = path.join(root, `docs/assets/studio/task-backup-${label}`);
const evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([temporary, shots, evidence].map((folder) => mkdir(folder, { recursive: true })));
async function snapshot() {
    const files = {};
    async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else files[path.relative(dist, file).replaceAll("\\", "/")] = createHash("sha256").update(await readFile(file)).digest("hex"); } }
    await visit(dist); return files;
}
const before = await snapshot();
const fixture = JSON.parse(await readFile(path.join(web, "tests/fixtures/catalog.json"), "utf8"));
const key = "synthetic-task-backup-only-key";
const requests = [];
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/task-journal-format"; export * from "./src/services/task-journal-recovery"; export { submitIntent, refreshIntent, cancelIntent, saveResult } from "./src/services/aggregator-engine"; export { AggregatorClient } from "./src/services/api/aggregator/client"; export { useStudioStore } from "./src/stores/use-studio-store"; import localforage from "localforage"; export const profiles = localforage.createInstance({name:"comfy-aggregator-studio",storeName:"profiles",driver:localforage.INDEXEDDB});', resolveDir: web }, bundle: true, format: "esm", platform: "browser", define: { "import.meta.env.VITE_STUDIO_MODE": '"managed"' }, tsconfig: path.join(web, "tsconfig.json"), outfile: path.join(temporary, "probe.mjs"), logLevel: "warning" });
const report = { scope: "native Chromium IndexedDB and production dist with synthetic HTTP; injected quota failure, desktop/390px emulation, not GPU/physical phone/live business authorization", started_at: new Date().toISOString(), checks: [], screenshots: [], page_errors: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const server = createServer((req, res) => void (async () => {
    const pathname = new URL(req.url, "http://fixture.invalid").pathname;
    if (pathname.startsWith("/v1/")) {
        requests.push({ method: req.method, path: pathname });
        const json = (status, value, extra = {}) => { res.writeHead(status, { "Content-Type": "application/json", ...extra }); res.end(JSON.stringify(value)); };
        if (req.headers.authorization !== `Bearer ${key}`) return json(401, { error: { code: "invalid_key", message: "Synthetic key required" } });
        if (pathname === "/v1/access-policy") return json(200, { scopes: fixture.scopes, limits: { rpm: { mode: "unlimited", value: null } } }, { "X-Studio-Contract": "1" });
        if (pathname === "/v1/models") return json(200, { object: "list", data: fixture.models });
        if (pathname === "/v1/capabilities") return json(200, { object: "list", data: fixture.capabilities });
        if (pathname === `/v1/jobs/job_${"c".repeat(32)}` && req.method === "GET") return json(200, { id: `job_${"c".repeat(32)}`, object: "generation.job", model: "backup-image", task_type: "text_to_image", status: "succeeded", updated_revision: 9, created_at: 1, completed_at: 2, artifacts: [] });
        if (pathname === "/v1/jobs" && req.method === "GET") return json(200, { data: [], has_more: false, next_cursor: null });
        return json(500, { error: { code: "unexpected_generation", message: "Backup acceptance must not generate" } });
    }
    if (pathname === "/blank") { res.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp }); res.end("<!doctype html><title>Owned journal recovery acceptance</title>"); return; }
    let file;
    if (pathname === "/probe.mjs") file = path.join(temporary, "probe.mjs");
    else if (pathname.startsWith("/studio/")) { file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); if (file !== dist && !file.startsWith(dist + path.sep)) throw new Error("Invalid owned path"); try { if (!(await stat(file)).isFile()) file = path.join(dist, "index.html"); } catch { file = path.join(dist, "index.html"); } }
    else { res.writeHead(404); res.end(); return; }
    const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };
    res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": path.basename(file).startsWith("schema-worker-") ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
})().catch(() => { res.writeHead(500); res.end(); }));
let browser;
const contexts = [], pages = [];
async function context() { const value = await browser.newContext({ viewport: { width: 1440, height: 960 }, locale: "zh-CN", recordVideo: { dir: temporary } }); contexts.push(value); return value; }
async function pageIn(context, url) { const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message)); await page.goto(url); return page; }
async function shot(page, name) { const file = path.join(shots, name + ".png"); await page.screenshot({ path: file, fullPage: true }); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(file)).digest("hex") }); }
const uiTask = (identity, id, known = false) => ({ version: 1, id, identity, projectId: `project-${id}`, nodeId: `node-${id}`, body: ` { "model": "backup-image", "task_type": "text_to_image", "input": {"prompt":"原样保存的合成任务 <script>globalThis.forbidden=1</script>"}, "parameters": {"n":1}, "client_request_id":"${id}" } `, idempotencyKey: `studio-job-${id}`, releaseDigest: "b".repeat(64), stage: known ? "accepted" : "unknown", createdAt: 1, updatedAt: 2, output: { mediaTypes: ["image/png"], maxBytes: 1024 }, ...(known ? { job: { id: `job_${"c".repeat(32)}`, object: "generation.job", model: "backup-image", task_type: "text_to_image", status: "running", updated_revision: 3, created_at: 1, artifacts: [] } } : {}) });
try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://canvas-journal.test:${server.address().port}`;
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--host-resolver-rules=MAP canvas-journal.test 127.0.0.1"] });
    const native = await context();
    const nativePage = await pageIn(native, origin + "/blank");
    const nativeChecks = await nativePage.evaluate(async (key) => {
        const done = [];
        const verify = (value, name) => { if (!value) throw new Error(name); done.push(name); };
        const fails = async (work, text = "") => { try { await work(); } catch (error) { if (error.message.includes(text)) return; throw error; } throw new Error("Expected rejection: " + text); };
        const j = await import("/probe.mjs");
        const client = new j.AggregatorClient(location.origin, key);
        const owner = { profileId: "native-original", origin: client.origin, fingerprint: client.fingerprint };
        const task = (id) => ({ version: 1, id, identity: owner, projectId: `project-${id}`, nodeId: `node-${id}`, body: ` { "model":"backup-image", "task_type":"text_to_image", "input":{"prompt":"原文"}, "parameters":{"n":1}, "client_request_id":"${id}" } `, idempotencyKey: `studio-job-${id}`, releaseDigest: "b".repeat(64), stage: "unknown", createdAt: 1, updatedAt: 2, output: { mediaTypes: ["image/png"], maxBytes: 1024 } });
        const original = { ...task("native-preserved"), activeNodeKey: `${owner.fingerprint}:project-native-preserved:node-native-preserved` };
        const document = { id: "infinite-canvas:canvas_store", version: 1, revision: 7, value: '{"version":0,"state":{"projects":[],"deletedProjects":[]}}', storageKeys: [], legacyValue: null };
        await new Promise((resolve, reject) => { const r = indexedDB.open("comfy-aggregator-studio:journal", 2); r.onupgradeneeded = () => { const s = r.result.createObjectStore("intents", { keyPath: "id" }); s.createIndex("active_node", "activeNodeKey", { unique: true }); for (const name of ["uploads", "results", "budgets", "documents"]) r.result.createObjectStore(name, { keyPath: "id" }); }; r.onerror = () => reject(r.error); r.onsuccess = () => { const db = r.result; const tx = db.transaction([...db.objectStoreNames], "readwrite"); tx.objectStore("intents").add(original); tx.objectStore("documents").add(document); tx.objectStore("results").add({ id: "preserved-result", blob: new Blob(["preserved-original"]) }); tx.objectStore("uploads").add({ id: "preserved-upload", blob: new Blob(["preserved-upload"]) }); tx.objectStore("budgets").add({ id: "preserved-budget", nextAt: 42, pausedUntil: 0 }); tx.oncomplete = () => { db.close(); resolve(); }; tx.onabort = () => { db.close(); reject(tx.error); }; }; });
        verify(JSON.stringify(await j.journalGet("intents", original.id)) === JSON.stringify(original) && JSON.stringify(await j.journalGet("documents", document.id)) === JSON.stringify(document) && await (await j.journalGet("results", "preserved-result")).blob.text() === "preserved-original" && await (await j.journalGet("uploads", "preserved-upload")).blob.text() === "preserved-upload" && (await j.journalGet("budgets", "preserved-budget")).nextAt === 42, "v2 to v3 upgrade preserves exact intent/document/blob/upload/budget without rebuilding stores");
        const refused = await new Promise((resolve) => { const r = indexedDB.open("comfy-aggregator-studio:journal", 2); r.onerror = () => resolve(r.error?.name === "VersionError"); r.onsuccess = () => { r.result.close(); resolve(false); }; });
        verify(refused, "older v2 client cannot open imported records and bypass recovery-only behavior");
        const unknown = task("native-unknown");
        const known = { ...task("native-known"), stage: "accepted", job: { id: `job_${"c".repeat(32)}`, object: "generation.job", model: "backup-image", task_type: "text_to_image", status: "running", updated_revision: 3, created_at: 1, artifacts: [] } };
        const source = j.createTaskJournal([unknown, known], owner);
        const imported = await j.restoreTaskJournal(source, owner, () => true);
        const restored = await j.journalGet("intents", unknown.id);
        verify(imported.added === 2 && restored.body === unknown.body && restored.idempotencyKey === unknown.idempotencyKey && restored.recovered.version === 1 && restored.pollPaused && !restored.canvas && restored.activeNodeKey === `${owner.fingerprint}:${unknown.projectId}:${unknown.nodeId}`, "atomic import preserves original request bytes, identities, active node and paused recovery marker");
        await j.updateIntent(known.id, (value) => ({ ...value, stage: "terminal", job: { ...value.job, status: "succeeded", updated_revision: 9 } }));
        const again = await j.restoreTaskJournal(source, owner, () => true);
        verify(again.added === 0 && again.kept === 2 && (await j.journalGet("intents", known.id)).job.updated_revision === 9, "repeat import keeps existing newer terminal state and never downgrades it");
        await fails(() => j.updateIntent(unknown.id, (value) => ({ ...value, recovered: undefined })));
        verify(Boolean((await j.journalGet("intents", unknown.id)).recovered), "ordinary state update cannot strip immutable recovery marker");
        j.useStudioStore.setState({ client, activeProfileId: "native-current" });
        await fails(() => j.submitIntent(unknown.id), "已阻止补发生成");
        verify((await j.journalGet("intents", unknown.id)).stage === "unknown", "engine refuses no-job backup resubmission before changing stage or sending POST");
        await fails(() => j.cancelIntent(known.id), "先查询原任务");
        await fails(() => j.saveResult(known.id, `content_${"d".repeat(32)}`), "先查询原任务");
        verify(!(await j.journalGet("intents", known.id)).cancelRequested, "unverified backup cannot cancel or read artifacts before an authenticated original-job query");
        const conflict = { ...unknown, body: unknown.body.replace("原文", "changed") };
        await fails(() => j.restoreTaskJournal(j.createTaskJournal([task("would-be-partial"), conflict], owner), owner, () => true), "原提交冲突");
        verify(!await j.journalGet("intents", "would-be-partial") && (await j.journalGet("intents", unknown.id)).body === unknown.body, "later original-body conflict rolls back earlier add in the same transaction");
        const active = { ...task("active-conflict"), projectId: original.projectId, nodeId: original.nodeId };
        await fails(() => j.restoreTaskJournal(j.createTaskJournal([active], owner), owner, () => true), "活动任务");
        verify(!await j.journalGet("intents", active.id), "existing active node blocks a different restored intent atomically");
        await fails(() => j.restoreTaskJournal(source, { ...owner, fingerprint: "0".repeat(64) }, () => true), "原API Key");
        await fails(() => j.restoreTaskJournal(j.createTaskJournal([task("identity-switched")], owner), owner, () => false), "连接已改变");
        verify(!await j.journalGet("intents", "identity-switched"), "wrong identity and connection switch refuse all writes");
        await j.journalPut("intents", { id: "unknown-local-version", version: 99 });
        await fails(() => j.restoreTaskJournal(source, owner, () => true), "未知版本");
        verify((await j.journalGet("intents", "unknown-local-version")).version === 99, "unknown local task version is preserved and blocks import");
        await j.journalDelete("intents", "unknown-local-version");
        const add = IDBObjectStore.prototype.add;
        let additions = 0;
        IDBObjectStore.prototype.add = function (...args) { if (this.name === "intents" && ++additions === 2) throw new DOMException("Injected task backup quota failure", "QuotaExceededError"); return add.apply(this, args); };
        try { await fails(() => j.restoreTaskJournal(j.createTaskJournal([task("quota-first"), task("quota-second")], owner), owner, () => true), "quota failure"); }
        finally { IDBObjectStore.prototype.add = add; }
        verify(!await j.journalGet("intents", "quota-first") && !await j.journalGet("intents", "quota-second") && JSON.stringify(await j.journalGet("documents", document.id)) === JSON.stringify(document), "second-write QuotaExceeded abort preserves first-write absence and old document");
        verify(!globalThis.isSecureContext && !globalThis.crypto?.subtle, "backup hashing and recovery work on plain HTTP without WebCrypto");
        return done;
    }, key);
    nativeChecks.forEach((name) => check(name));
    assert.equal(requests.length, 0);
    check("native restore and blocked resubmission issued no business request");
    const verified = await nativePage.evaluate(async () => {
        const j = await import("/probe.mjs");
        const original = await j.journalGet("intents", "native-known");
        await j.journalPut("intents", { ...original, job: { ...original.job, status: "succeeded", updated_revision: 999999 } });
        const updated = await j.refreshIntent(original.id);
        return { revision: updated.job.updated_revision, verified: updated.recoveryVerified };
    });
    assert.deepEqual(verified, { revision: 9, verified: true });
    check("first explicit server query overrides an untrusted backup's inflated revision and confirms the remote snapshot");
    assert.equal(requests.length, 1);
    const other = await pageIn(native, origin + "/blank");
    const racedSource = await nativePage.evaluate(async (key) => { const j = await import("/probe.mjs"); const client = new j.AggregatorClient(location.origin, key); const identity = { profileId: "race", origin: client.origin, fingerprint: client.fingerprint }; const id = "two-page-race"; return { identity, source: j.createTaskJournal([{ version: 1, id, identity, projectId: id, nodeId: id, body: JSON.stringify({ model: "backup-image", task_type: "text_to_image", input: {}, parameters: { n: 1 }, client_request_id: id }), idempotencyKey: `studio-job-${id}`, releaseDigest: "b".repeat(64), stage: "unknown", createdAt: 1, updatedAt: 1, output: { mediaTypes: ["image/png"], maxBytes: 1024 } }], identity) }; }, key);
    const race = await Promise.all([nativePage, other].map((page) => page.evaluate(async ({ identity, source }) => { const j = await import("/probe.mjs"); return j.restoreTaskJournal(source, identity, () => true); }, racedSource)));
    assert.deepEqual(race.map((item) => item.added).sort(), [0, 1]);
    check("two actual pages serialize concurrent import: one add and one existing record");
    await native.close();

    const uiStart = requests.length;
    const ui = await context();
    const page = await pageIn(ui, origin + "/blank");
    const identity = await page.evaluate(async (key) => { const j = await import("/probe.mjs"); const client = new j.AggregatorClient(location.origin, key); const profile = { id: "ui-original-profile", origin: client.origin, fingerprint: client.fingerprint, name: "任务备份合成验收", savedKey: key }; await j.profiles.setItem("connections", { version: 1, profiles: [profile], activeProfileId: profile.id }); return { profileId: profile.id, origin: profile.origin, fingerprint: profile.fingerprint }; }, key);
    const tasks = [uiTask(identity, "ui-unknown"), uiTask(identity, "ui-known", true)];
    await page.evaluate(async (tasks) => { const j = await import("/probe.mjs"); for (const task of tasks) await j.journalPut("intents", task); }, tasks);
    await page.goto(origin + "/studio/tasks");
    await page.getByText("服务已连接", { exact: true }).waitFor({ state: "detached" });
    await page.getByRole("button", { name: "导入任务清单", exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('button[disabled] .lucide-upload'));
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出任务清单", exact: true }).click();
    const download = await downloadPromise;
    const exportedFile = path.join(temporary, "downloaded-tasks.json"); await download.saveAs(exportedFile);
    const source = await readFile(exportedFile, "utf8");
    assert.ok(!source.includes(key));
    assert.equal(JSON.parse(JSON.parse(source).payload).tasks.find((task) => task.id === tasks[0].id).body, tasks[0].body);
    check("actual task-page export downloads exact original body and omits saved connection Key", { bytes: Buffer.byteLength(source), sha256: createHash("sha256").update(source).digest("hex") });
    await page.evaluate(async () => { const j = await import("/probe.mjs"); await j.journalDelete("intents", "ui-unknown"); await j.journalDelete("intents", "ui-known"); });
    await page.reload(); await page.getByRole("button", { name: "导入任务清单", exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('button[disabled] .lucide-upload'));
    const requestCount = requests.length;
    await page.getByLabel("选择任务清单", { exact: true }).setInputFiles(exportedFile);
    const dialog = page.getByRole("dialog"); await dialog.getByText("确认恢复任务清单", { exact: true }).waitFor();
    const contrast = await dialog.locator(".ant-alert").evaluate((element) => {
        const luminance = (color) => color.match(/[\d.]+/g).slice(0, 3).map(Number).map((n) => n / 255).map((n) => n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4).reduce((sum, n, i) => sum + n * [0.2126, 0.7152, 0.0722][i], 0);
        const style = getComputedStyle(element), description = getComputedStyle(element.querySelector(".ant-alert-description"));
        const a = luminance(style.backgroundColor), b = luminance(description.color);
        return { background: style.backgroundColor, foreground: description.color, ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) };
    });
    assert.ok(contrast.ratio >= 4.5);
    check("Lime import explanation has readable text contrast in the actual modal", contrast);
    assert.equal(await page.evaluate(async () => { const j = await import("/probe.mjs"); return (await j.journalList("intents")).length; }), 0);
    await shot(page, "desktop-import-preview");
    check("real file selection previews both records before any database write or business request");
    assert.equal(requests.length, requestCount);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const confirm = dialog.getByRole("button", { name: "仅导入记录", exact: true });
    const bounds = await confirm.boundingBox(); assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390);
    await shot(page, "mobile-import-preview");
    await confirm.click(); await dialog.waitFor({ state: "hidden" });
    await page.getByText("备份记录待核对", { exact: true }).first().waitFor();
    assert.equal(await page.getByText("备份记录待核对", { exact: true }).count(), 2);
    assert.equal(await page.getByRole("button", { name: "请求取消", exact: true }).count(), 0);
    assert.equal(requests.length, requestCount);
    assert.equal(await page.getByRole("button", { name: "核对原提交", exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => globalThis.forbidden), undefined);
    await shot(page, "mobile-restored-records");
    check("390px confirmed import restores two records without query/POST, no-job submit control or script execution");
    await page.reload(); await page.getByText("备份记录待核对", { exact: true }).first().waitFor();
    const beforeQuery = requests.slice(uiStart).filter((item) => item.path.startsWith("/v1/jobs/")).length;
    assert.equal(beforeQuery, 0);
    await page.getByRole("button", { name: "查询原任务", exact: true }).click();
    await page.getByText("已完成", { exact: true }).waitFor();
    assert.equal(requests.slice(uiStart).filter((item) => item.path.startsWith("/v1/jobs/")).length, 1);
    check("browser refresh retains recovery state; one explicit query reads only the original job ID");
    await page.setViewportSize({ width: 1440, height: 960 });
    await shot(page, "desktop-known-job-queried");
    const malformed = path.join(temporary, "bad-checksum.json");
    const broken = JSON.parse(source); broken.payload += " "; await writeFile(malformed, JSON.stringify(broken));
    await page.getByLabel("选择任务清单", { exact: true }).setInputFiles(malformed);
    await page.getByText("任务清单SHA-256不符，未写入任何记录", { exact: true }).waitFor();
    assert.equal(await page.evaluate(async () => { const j = await import("/probe.mjs"); return (await j.journalList("intents")).length; }), 2);
    await shot(page, "desktop-invalid-backup");
    check("actual malformed-file UI reports checksum failure and keeps both stored records");
    const quotaFile = path.join(temporary, "quota-import.json");
    const quotaSource = await page.evaluate(async ({ identity, task }) => { const j = await import("/probe.mjs"); return j.createTaskJournal([task], identity); }, { identity, task: uiTask(identity, "ui-quota") });
    await writeFile(quotaFile, quotaSource);
    await page.evaluate(() => { const add = IDBObjectStore.prototype.add; globalThis.__restoreAdd = add; IDBObjectStore.prototype.add = function (...args) { if (this.name === "intents") throw new DOMException("The quota has been exceeded.", "QuotaExceededError"); return add.apply(this, args); }; });
    await page.getByLabel("选择任务清单", { exact: true }).setInputFiles(quotaFile);
    await dialog.getByRole("button", { name: "仅导入记录", exact: true }).click();
    await dialog.getByText("本机存储空间不足，任务清单未导入；请先另存重要作品，释放空间后重新导入。", { exact: true }).waitFor();
    assert.equal(await page.evaluate(async () => { const j = await import("/probe.mjs"); return (await j.journalList("intents")).length; }), 2);
    await shot(page, "desktop-import-quota-failure");
    await page.evaluate(() => { IDBObjectStore.prototype.add = globalThis.__restoreAdd; delete globalThis.__restoreAdd; });
    await dialog.getByRole("button", { name: /^取\s*消$/ }).click(); await dialog.waitFor({ state: "hidden" });
    check("actual import quota failure keeps preview/error and existing records, with no success or POST");
    await page.getByRole("link", { name: "已连接", exact: true }).click();
    await page.getByRole("button", { name: "退出连接", exact: true }).click();
    await page.getByRole("link", { name: "任务", exact: true }).click();
    const offlineCount = requests.length;
    const offlineDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出任务清单", exact: true }).click();
    const offlineFile = path.join(temporary, "offline-tasks.json"); await (await offlineDownload).saveAs(offlineFile);
    assert.equal(JSON.parse(JSON.parse(await readFile(offlineFile, "utf8")).payload).tasks.length, 2);
    assert.equal(requests.length, offlineCount);
    assert.ok(!await page.getByRole("button", { name: "导入任务清单", exact: true }).isEnabled());
    check("disconnected saved identity exports its journal without a Key or business request; import stays disabled");
    assert.equal(requests.filter((item) => item.method !== "GET").length, 0);
    await ui.close();
    report.private_recording = path.relative(root, await page.video().path()).replaceAll("\\", "/");
    report.business_requests = requests;
    assert.equal(report.page_errors.length, 0); assert.deepEqual(await snapshot(), before);
    report.dist_files = Object.keys(before).length; report.static_artifact_sha256 = before; report.static_artifact_unchanged = true; report.browser_storage_version = 4;
    check("production static bytes unchanged, all owned pages free of uncaught errors and all business writes absent");
    report.passed = true;
} catch (error) { report.passed = false; report.failure = error.stack; console.error(error.stack); }
finally {
    for (const page of pages) await page.close().catch(() => {});
    report.cleanup.pages_closed = pages.every((page) => page.isClosed());
    for (const value of contexts) await value.close().catch(() => {});
    report.cleanup.contexts_closed = contexts.every((value) => value.pages().length === 0);
    if (browser) { await browser.close(); report.cleanup.browser_closed = !browser.isConnected(); }
    await new Promise((resolve) => server.close(resolve)); report.cleanup.server_closed = !server.listening;
    report.completed_at = new Date().toISOString();
    const file = path.join(evidence, `task-backup-${label}.json`); await writeFile(file, JSON.stringify(report, null, 2) + "\n"); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
