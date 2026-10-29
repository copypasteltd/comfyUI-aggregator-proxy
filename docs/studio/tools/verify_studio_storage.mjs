/** Native IndexedDB acceptance, synthetic local records only; no GPU or production mutations. */
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const web = path.join(root, "infinite-canvas/web");
const dist = path.join(web, "dist");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const temporary = path.join(root, `.local/studio-storage/${label}`);
const shots = path.join(root, `docs/assets/studio/storage-${label}`);
const evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([temporary, shots, evidence].map((folder) => mkdir(folder, { recursive: true })));
async function snapshot() {
    const entries = {};
    async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else entries[path.relative(dist, file).replaceAll("\\", "/")] = createHash("sha256").update(await readFile(file)).digest("hex"); } }
    await visit(dist); return entries;
}
const before = await snapshot();
const fixture = JSON.parse(await readFile(path.join(web, "tests/fixtures/catalog.json"), "utf8"));
const key = "synthetic-storage-acceptance-key";
let postCount = 0;
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-persistence"; import localforage from "localforage"; export const legacy = localforage.createInstance({name:"infinite-canvas",storeName:"app_state"});', resolveDir: web }, bundle: true, format: "esm", platform: "browser", define: { "import.meta.env.VITE_STUDIO_MODE": '"managed"' }, tsconfig: path.join(web, "tsconfig.json"), outfile: path.join(temporary, "storage.mjs"), logLevel: "warning" });
const report = { scope: "production dist with native Chromium IndexedDB; synthetic records and injected quota failure, not actual disk exhaustion, GPU, physical phone or multi-device capacity", started_at: new Date().toISOString(), checks: [], screenshots: [], page_errors: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
let browser;
const contexts = [];
const pages = [];
const server = createServer((req, res) => void (async () => {
    const pathname = new URL(req.url, "http://fixture.invalid").pathname;
    if (pathname.startsWith("/v1/")) {
        if (req.method === "POST") postCount++;
        const json = (status, value, extra = {}) => { res.writeHead(status, { "Content-Type": "application/json", ...extra }); res.end(JSON.stringify(value)); };
        if (req.headers.authorization !== `Bearer ${key}`) return json(401, { error: { code: "invalid_key", message: "Synthetic key required" } });
        if (pathname === "/v1/access-policy") return json(200, { scopes: fixture.scopes, limits: { rpm: { mode: "unlimited", value: null } } }, { "X-Studio-Contract": "1" });
        if (pathname === "/v1/models") return json(200, { object: "list", data: fixture.models });
        if (pathname === "/v1/capabilities") return json(200, { object: "list", data: fixture.capabilities });
        if (pathname === "/v1/jobs" && req.method === "GET") return json(200, { data: [], has_more: false, next_cursor: null });
        return json(500, { error: { code: "unexpected_generation", message: "No generation should reach this fixture" } });
    }
    if (pathname === "/blank") { res.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp }); res.end("<!doctype html><title>Isolated storage acceptance</title>"); return; }
    let file;
    if (pathname === "/probe.mjs") file = path.join(temporary, "storage.mjs");
    else if (pathname.startsWith("/studio/")) { file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); if (file !== dist && !file.startsWith(dist + path.sep)) throw new Error("Invalid owned path"); try { if (!(await stat(file)).isFile()) file = path.join(dist, "index.html"); } catch { file = path.join(dist, "index.html"); } }
    else { res.writeHead(404); res.end(); return; }
    const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };
    res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": path.basename(file).startsWith("schema-worker-") ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
})().catch(() => { res.writeHead(500); res.end(); }));
async function pageIn(context, url) { const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message)); await page.goto(url); return page; }
async function context() { const value = await browser.newContext({ viewport: { width: 1440, height: 960 }, locale: "zh-CN", recordVideo: { dir: temporary } }); contexts.push(value); return value; }
async function shot(page, name) { const file = path.join(shots, name + ".png"); await page.screenshot({ path: file, fullPage: true }); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(file)).digest("hex") }); }
try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://canvas-storage.test:${server.address().port}`;
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--host-resolver-rules=MAP canvas-storage.test 127.0.0.1"] });
    const first = await context();
    const page = await pageIn(first, origin + "/blank");
    const native = await page.evaluate(async () => {
        const j = await import("/probe.mjs");
        const names = j.documentNames;
        const done = [];
        const verify = (value, name) => { if (!value) throw new Error(name); done.push(name); };
        const fails = async (work, text) => { try { await work(); } catch (error) { if (error.message.includes(text)) return; throw error; } throw new Error("Expected rejection: " + text); };
        const identity = { profileId: "synthetic", origin: "http://synthetic.invalid", fingerprint: "f".repeat(64) };
        const id = `studio:${identity.fingerprint}:content_synthetic`;
        const blob = new Blob(["synthetic bytes"], { type: "image/png" });
        const entry = { version: 1, id, identity, intentId: "intent", jobId: "job", metadata: { id: "content_synthetic", bytes: blob.size, media_type: blob.type, sha256: "a".repeat(64), expires_at: 1, metadata: {} }, blob, savedAt: 1 };
        const frozen = { version: 1, id: "preserved-intent", identity, projectId: "unopened", nodeId: "node", body: '{"model":"synthetic","prompt":"unchanged original"}', idempotencyKey: "preserved-key", releaseDigest: "b".repeat(64), stage: "prepared", activeNodeKey: "preserved-active-node", createdAt: 1, updatedAt: 1, output: { mediaTypes: ["image/png"], maxBytes: 100 } };
        const doc = (projects) => JSON.stringify({ version: 0, state: { projects, deletedProjects: [] } });
        const project = { id: "unopened", title: "未打开的测试画布", nodes: [{ id: "source", type: "image", metadata: { storageKey: id } }] };
        const original = doc([project]);
        await new Promise((resolve, reject) => { const r = indexedDB.open("comfy-aggregator-studio:journal", 1); r.onupgradeneeded = () => { const intents = r.result.createObjectStore("intents", { keyPath: "id" }); intents.createIndex("active_node", "activeNodeKey", { unique: true }); for (const name of ["uploads", "results", "budgets"]) r.result.createObjectStore(name, { keyPath: "id" }); }; r.onerror = () => reject(r.error); r.onsuccess = () => { const db = r.result; const tx = db.transaction(["intents", "uploads", "results", "budgets"], "readwrite"); tx.objectStore("intents").put(frozen); tx.objectStore("results").put(entry); tx.objectStore("uploads").put({ id: "preserved-upload", blob }); tx.objectStore("budgets").put({ id: "preserved-budget", nextAt: 42, pausedUntil: 0 }); tx.oncomplete = () => { db.close(); resolve(); }; tx.onabort = () => { db.close(); reject(tx.error); }; }; });
        const documents = await j.initializeDocuments({ [names[0]]: original, [names[1]]: JSON.stringify({ version: 0, state: { assets: [] } }) });
        verify(documents.find((d) => d.id === names[0]).legacyValue === original && await (await j.journalGet("results", id)).blob.text() === "synthetic bytes" && (await j.journalGet("uploads", "preserved-upload")).blob.size === blob.size && (await j.journalGet("budgets", "preserved-budget")).nextAt === 42, "v1 upgrade preserves exact legacy JSON, original blob, uploads and budgets");
        const oldVersionRejected = await new Promise((resolve) => { const r = indexedDB.open("comfy-aggregator-studio:journal", 1); r.onerror = () => resolve(r.error?.name === "VersionError"); r.onsuccess = () => { r.result.close(); resolve(false); }; });
        verify(oldVersionRejected, "v1 client is refused after v2 upgrade instead of downgrading data");
        await fails(() => j.prepareIntent({ ...frozen, id: "competing-new-intent" }), "");
        verify(JSON.stringify(await j.journalGet("intents", frozen.id)) === JSON.stringify(frozen), "upgrade preserves exact frozen intent and active-node unique index");
        await fails(() => j.deleteUnreferencedResult(id, identity), "已保存");
        verify(Boolean(await j.journalGet("results", id)), "unopened project protects its original without Zustand hydration");
        const save = async (name, value) => j.saveDocument(name, value, (await j.journalGet("documents", name)).revision);
        await save(names[0], doc([{ ...project, nodes: [] }]));
        await save(names[1], JSON.stringify({ version: 0, state: { assets: [{ id: "asset", data: { storageKey: id } }] } }));
        await fails(() => j.deleteUnreferencedResult(id, identity), "已保存");
        verify(Boolean(await j.journalGet("results", id)), "saved asset protects original while canvas has no media reference");
        await save(names[1], JSON.stringify({ version: 0, state: { assets: [] } }));
        const intent = { version: 1, id: "intent", identity, projectId: project.id, canvas: { node: {}, sourceIds: [] }, job: { artifacts: [{ id: entry.metadata.id }] } };
        await j.journalPut("intents", intent);
        await fails(() => j.deleteUnreferencedResult(id, identity), "任务节点");
        verify(Boolean(await j.journalGet("results", id)), "reconstructable task protects original before node reference save");
        await j.journalPut("intents", { ...intent, canvas: { ...intent.canvas, detached: true } });
        await j.journalPut("intents", { ...intent, version: 99 });
        await fails(() => j.deleteUnreferencedResult(id, identity), "不受支持");
        verify(Boolean(await j.journalGet("results", id)), "unknown task version refuses result deletion and preserves record");
        await j.journalPut("intents", { ...intent, canvas: { ...intent.canvas, detached: true } });
        await fails(() => j.deleteUnreferencedResult(id, { ...identity, fingerprint: "other" }), "归属");
        await j.deleteUnreferencedResult(id, identity);
        verify(!await j.journalGet("results", id), "wrong identity rejected; explicitly detached and unreferenced original deletable");
        await j.journalPut("results", entry);
        let revision = (await j.journalGet("documents", names[0])).revision;
        let race = await Promise.allSettled([j.saveDocument(names[0], original, revision), j.deleteUnreferencedResult(id, identity)]);
        verify(race[0].status === "fulfilled" && race[1].status === "rejected" && Boolean(await j.journalGet("results", id)), "reference-first native transactions reject concurrent deletion");
        await save(names[0], doc([{ ...project, nodes: [] }]));
        revision = (await j.journalGet("documents", names[0])).revision;
        race = await Promise.allSettled([j.deleteUnreferencedResult(id, identity), j.saveDocument(names[0], original, revision)]);
        verify(race[0].status === "fulfilled" && race[1].status === "rejected" && (await j.journalGet("documents", names[0])).revision === revision, "delete-first native transactions reject new dangling reference and retain prior document");
        await save(names[0], doc([]));
        const saved = await j.journalGet("documents", names[0]);
        await fails(() => j.saveDocument(names[0], "{broken", saved.revision), "无法解析");
        await fails(() => j.saveDocument(names[0], JSON.stringify({ version: 99, state: { projects: [] } }), saved.revision), "不受支持");
        verify(JSON.stringify(await j.journalGet("documents", names[0])) === JSON.stringify(saved), "malformed or unknown document versions preserve exact previous record");
        await j.journalPut("results", entry);
        await j.journalPut("intents", intent);
        await j.deleteUnreferencedResult(id, identity);
        verify(!await j.journalGet("results", id), "deleted whole project does not permanently pin detached canvas history");
        return done;
    });
    native.forEach((name) => check(name));
    const secondPage = await pageIn(first, origin + "/blank");
    const stale = await secondPage.evaluate(async () => { const j = await import("/probe.mjs"); return (await j.journalGet("documents", j.documentNames[0])).revision; });
    await page.evaluate(async () => { const j = await import("/probe.mjs"); const d = await j.journalGet("documents", j.documentNames[0]); await j.saveDocument(d.id, JSON.stringify({ version: 0, state: { projects: [{ id: "page-A", nodes: [] }], deletedProjects: [] } }), d.revision); });
    const conflict = await secondPage.evaluate(async (revision) => { const j = await import("/probe.mjs"); try { await j.saveDocument(j.documentNames[0], JSON.stringify({ version: 0, state: { projects: [], deletedProjects: [] } }), revision); return false; } catch (e) { return e.message.includes("另一页面") && JSON.parse((await j.journalGet("documents", j.documentNames[0])).value).state.projects[0].id === "page-A"; } }, stale);
    assert.equal(conflict, true); check("two actual pages reject stale revision without overwriting committed page-A document");
    await first.close();

    const ui = await context();
    const uiPage = await pageIn(ui, origin + "/studio/config");
    await uiPage.getByPlaceholder("输入管理员提供的 API Key").fill(key);
    await uiPage.getByRole("checkbox").check();
    await uiPage.getByRole("button", { name: "连接并读取模型" }).click();
    await uiPage.getByText("服务已连接", { exact: true }).waitFor();
    await uiPage.getByRole("link", { name: "画布", exact: true }).click();
    await uiPage.getByRole("button", { name: "新建画布", exact: true }).first().waitFor();
    await uiPage.evaluate(() => { const put = IDBObjectStore.prototype.put; IDBObjectStore.prototype.put = function (...args) { if (this.name === "documents" && window.__quotaFailure) throw new DOMException("Injected quota failure", "QuotaExceededError"); return put.apply(this, args); }; window.__quotaFailure = true; });
    await uiPage.getByRole("button", { name: "新建画布", exact: true }).first().click();
    await uiPage.getByText("本机编辑尚未保存", { exact: true }).waitFor();
    await uiPage.getByRole("button", { name: "重试保存", exact: true }).waitFor();
    await shot(uiPage, "desktop-save-failed");
    assert.equal(await uiPage.evaluate(async () => { const j = await import("/probe.mjs"); return JSON.parse((await j.journalGet("documents", j.documentNames[0])).value).state.projects.length; }), 0);
    check("actual UI displays injected quota failure and keeps old committed project document");
    await uiPage.getByRole("button", { name: "新建创作", exact: true }).click();
    await uiPage.getByRole("combobox", { name: "生成模型" }).click();
    await uiPage.locator(".ant-select-item-option").filter({ hasText: /lustify/i }).first().click();
    await uiPage.getByRole("textbox", { name: "创作描述", exact: true }).fill("Synthetic blocked submission");
    await uiPage.getByRole("button", { name: "提交 1 项生成任务", exact: true }).click();
    await uiPage.locator(".ant-drawer .ant-alert").filter({ hasText: "本机存储空间不足" }).waitFor();
    assert.equal(postCount, 0);
    assert.equal(await uiPage.evaluate(async () => (await (await import("/probe.mjs")).journalList("intents")).length), 0);
    check("failed canvas save blocks new generation before intent creation and HTTP POST");
    await uiPage.locator(".ant-drawer-close").click();
    await uiPage.locator(".ant-drawer-open").waitFor({ state: "detached" });
    await uiPage.getByRole("button", { name: "提交 1 项生成任务", exact: true }).waitFor({ state: "hidden" });
    await uiPage.setViewportSize({ width: 390, height: 844 });
    assert.ok(await uiPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await shot(uiPage, "mobile-save-failed");
    const retry = await uiPage.getByRole("button", { name: "重试保存", exact: true }).boundingBox();
    const header = await uiPage.locator(".studio-header").boundingBox();
    assert.ok(retry && header && retry.y >= header.y + header.height && retry.x >= 0 && retry.x + retry.width <= 390);
    check("save error and retry remain visible below navigation at 390px emulation");
    await uiPage.evaluate(() => { window.__quotaFailure = false; });
    await uiPage.getByRole("button", { name: "重试保存", exact: true }).click();
    await uiPage.getByText("本机编辑尚未保存", { exact: true }).waitFor({ state: "detached" });
    const projectUrl = uiPage.url();
    await uiPage.reload();
    await uiPage.getByRole("button", { name: "新建创作", exact: true }).waitFor();
    assert.equal(uiPage.url(), projectUrl);
    assert.equal(await uiPage.evaluate(async () => { const j = await import("/probe.mjs"); return JSON.parse((await j.journalGet("documents", j.documentNames[0])).value).state.projects.length; }), 1);
    await shot(uiPage, "mobile-saved-after-retry");
    check("explicit retry commits retained in-memory edit; actual canvas survives reload");
    const stalePage = await pageIn(ui, origin + "/studio/canvas");
    await stalePage.getByRole("button", { name: "新建画布", exact: true }).first().waitFor();
    await uiPage.getByRole("link", { name: "画布", exact: true }).click();
    await uiPage.getByRole("button", { name: "新建画布", exact: true }).first().click();
    await uiPage.waitForFunction(async () => { const j = await import("/probe.mjs"); return JSON.parse((await j.journalGet("documents", j.documentNames[0])).value).state.projects.length === 2; });
    const committedProject = uiPage.url().split("/").at(-1);
    await stalePage.getByRole("button", { name: "新建画布", exact: true }).first().click();
    await stalePage.getByText("本机编辑尚未保存", { exact: true }).waitFor();
    assert.equal(await stalePage.getByRole("button", { name: "重试保存", exact: true }).count(), 0);
    const projects = await stalePage.evaluate(async () => { const j = await import("/probe.mjs"); return JSON.parse((await j.journalGet("documents", j.documentNames[0])).value).state.projects.map((p) => p.id); });
    assert.equal(projects.length, 2); assert.ok(projects.includes(committedProject)); assert.ok(!projects.includes(stalePage.url().split("/").at(-1)));
    await shot(stalePage, "desktop-cross-tab-conflict");
    check("actual two-tab UI exposes revision conflict, retains page-A commit and avoids blind retry");
    await ui.close();
    report.private_recording = path.relative(root, await uiPage.video().path()).replaceAll("\\", "/");

    for (const invalid of ["malformed", "conflicting-copies", "changed-legacy"]) {
        const isolated = await context();
        const testPage = await pageIn(isolated, origin + "/blank");
        const original = await testPage.evaluate(async (scenario) => {
            const j = await import("/probe.mjs"); const name = j.documentNames[0];
            const value = scenario === "malformed" ? "{bad legacy" : JSON.stringify({ version: 0, state: { projects: [], deletedProjects: [] } });
            await j.legacy.setItem(name, value);
            if (scenario === "conflicting-copies") localStorage.setItem(name, "{different copy}");
            if (scenario === "changed-legacy") { await j.readStudioDocument(name); await j.legacy.setItem(name, JSON.stringify({ version: 0, state: { projects: [{ id: "old-tab" }], deletedProjects: [] } })); }
            return await j.legacy.getItem(name);
        }, invalid);
        await testPage.goto(origin + "/studio/canvas");
        await testPage.getByText("本机编辑尚未保存", { exact: true }).waitFor();
        assert.equal(await testPage.getByRole("button", { name: "重试保存", exact: true }).count(), 0);
        assert.equal(await testPage.evaluate(async () => { const j = await import("/probe.mjs"); return j.legacy.getItem(j.documentNames[0]); }), original);
        check(`${invalid} legacy copy refuses overwrite and retains original bytes`);
        await isolated.close();
    }
    assert.equal(report.page_errors.length, 0);
    assert.deepEqual(await snapshot(), before);
    report.dist_files = Object.keys(before).length; report.dist_sha256 = createHash("sha256").update(JSON.stringify(before)).digest("hex"); report.static_artifact_sha256 = before;
    check("production dist unchanged and all owned pages free of uncaught errors");
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
    const file = path.join(evidence, `storage-${label}.json`); await writeFile(file, JSON.stringify(report, null, 2) + "\n"); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
