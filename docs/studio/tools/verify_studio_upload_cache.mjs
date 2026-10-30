/** Native three-database storage/cleanup and actual shipped UI; all records/API responses synthetic. */
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url)), web = path.join(root, "infinite-canvas/web"), dist = path.join(web, "dist");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const temporary = path.join(root, `.local/studio-upload-cache/${label}`), shots = path.join(root, `docs/assets/studio/upload-cache-${label}`), evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([temporary, shots, evidence].map((folder) => mkdir(folder, { recursive: true })));
const hash = (data) => createHash("sha256").update(data).digest("hex");
async function snapshot() { const files = {}; async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else files[path.relative(dist, file).replaceAll("\\", "/")] = hash(await readFile(file)); } } await visit(dist); return files; }
const before = await snapshot(), fixture = JSON.parse(await readFile(path.join(web, "tests/fixtures/catalog.json"), "utf8"));
const key = "synthetic-cache-acceptance-key", otherKey = "synthetic-cache-other-identity-key";
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-upload-cache"; export * from "./src/services/local-storage-usage"; export * from "./src/services/api/aggregator/client"; export {uploadReference} from "./src/services/aggregator-engine"; export {useStudioStore} from "./src/stores/use-studio-store"; import localforage from "localforage"; export const profiles=localforage.createInstance({name:"comfy-aggregator-studio",storeName:"profiles"}); export const legacy=localforage.createInstance({name:"infinite-canvas",storeName:"app_state"}); export const images=localforage.createInstance({name:"infinite-canvas",storeName:"image_files"});', resolveDir: web }, bundle: true, format: "esm", platform: "browser", define: { "import.meta.env.VITE_STUDIO_MODE": '"managed"' }, tsconfig: path.join(web, "tsconfig.json"), outfile: path.join(temporary, "probe.mjs"), logLevel: "warning" });
const report = { scope: "Actual native IndexedDB transactions and production UI under synthetic HTTP, including three-database estimates, cleanup/identity races and late upload response; no GPU or production writes", checks: [], screenshots: [], page_errors: [], cleanup: {}, browser_storage_version: 3 };
const requests = [], heldUploads = [];
let uploadStarted;
const uploadStartedPromise = new Promise((resolve) => { uploadStarted = resolve; });
const asset = { id: "asset-cache-original", status: "ready", bytes: 8, sha256: hash("original"), media_type: "image/png", width: 1, height: 1, expires_at: 1 };
let browser; const contexts = [], pages = [];
const server = createServer((req, res) => void (async () => {
    const pathname = new URL(req.url, "http://fixture.invalid").pathname;
    if (pathname.startsWith("/v1/")) {
        requests.push({ method: req.method, path: pathname });
        const json = (status, value, extra = {}) => { res.writeHead(status, { "Content-Type": "application/json", ...extra }); res.end(JSON.stringify(value)); };
        if (![key, otherKey].some((value) => req.headers.authorization === `Bearer ${value}`)) return json(401, { error: { code: "invalid_key", message: "Synthetic identity required" } });
        if (pathname === "/v1/access-policy") return json(200, { scopes: fixture.scopes, limits: {} }, { "X-Studio-Contract": "1" });
        if (pathname === "/v1/models") return json(200, { object: "list", data: fixture.models });
        if (pathname === "/v1/capabilities") return json(200, { object: "list", data: fixture.capabilities });
        if (pathname === "/v1/jobs" && req.method === "GET") return json(200, { data: [], has_more: false, next_cursor: null });
        if (pathname === "/v1/assets" && req.method === "POST") { req.resume(); heldUploads.push(() => json(200, asset)); uploadStarted(); return; }
        return json(500, { error: { code: "unexpected", message: "This fixture must not mutate cloud tasks" } });
    }
    if (pathname === "/blank") { res.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp }); res.end("<!doctype html><title>Owned cache acceptance</title>"); return; }
    let file;
    if (pathname === "/probe.mjs") file = path.join(temporary, "probe.mjs");
    else if (pathname.startsWith("/studio/")) { file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); if (file !== dist && !file.startsWith(dist + path.sep)) throw new Error("Unexpected path"); try { if (!(await stat(file)).isFile()) file = path.join(dist, "index.html"); } catch { file = path.join(dist, "index.html"); } }
    else { res.writeHead(404); res.end(); return; }
    const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };
    res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": path.basename(file).startsWith("schema-worker-") ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
})().catch((error) => { report.handler_failure = error.message; if (!res.headersSent) res.writeHead(500); res.end(); }));
const check = (name, detail = {}) => report.checks.push({ name, passed: true, detail });
async function shot(page, name) { const file = path.join(shots, name + ".png"); await page.screenshot({ path: file, fullPage: true, animations: "disabled" }); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), sha256: hash(await readFile(file)) }); }
async function newPage(context, url) { const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message)); await page.goto(url); return page; }
try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `cache-storage.test:${server.address().port}`;
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--host-resolver-rules=MAP cache-storage.test 127.0.0.1"] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", recordVideo: { dir: temporary } }); contexts.push(context);
    const page = await newPage(context, `http://${origin}/blank`);
    const result = await page.evaluate(async ({ key, otherKey, asset }) => {
        const j = await import("/probe.mjs"), done = [], verify = (condition, name) => { if (!condition) throw new Error(name); done.push(name); };
        const fails = async (run, expected) => { try { await run(); } catch (error) { if (error.message.includes(expected)) return; throw error; } throw new Error("Expected refusal: " + expected); };
        const client = new j.AggregatorClient(location.origin, key), other = new j.AggregatorClient(location.origin, otherKey);
        const identity = { origin: client.origin, fingerprint: client.fingerprint, profileId: "cache-native" };
        const base = { version: 1, id: "cache-eligible", identity, name: "已确认的参考.png", blob: new Blob(["original"], { type: "image/png" }), sha256: asset.sha256, idempotencyKey: "synthetic-upload", createdAt: 1, asset };
        const task = (id, input, stage = "prepared") => ({ version: 1, id, identity, projectId: "cache-project", nodeId: id, body: JSON.stringify({ model: "synthetic-image", task_type: "image_to_image", input, parameters: {} }), idempotencyKey: "intent-" + id, releaseDigest: "c".repeat(64), stage, output: { mediaTypes: ["image/png"], maxBytes: 1024 }, createdAt: 1, updatedAt: 1 });
        await j.initializeDocuments({ "infinite-canvas:canvas_store": null, "infinite-canvas:asset_store": null });
        await j.images.setItem("image:meter", new Blob([new Uint8Array(2048)]));
        await j.journalPut("results", { version: 1, id: "meter", identity, intentId: "meter", metadata: { id: "meter", bytes: 1048576 }, blob: new Blob([new Uint8Array(1048576)]), savedAt: 1 });
        const databasesBefore = (await indexedDB.databases()).map((value) => value.name).sort();
        const absent = await j.readLocalStorageUsage(["owned-missing-meter"]);
        verify(absent.databases[0].version === 0 && JSON.stringify((await indexedDB.databases()).map((value) => value.name).sort()) === JSON.stringify(databasesBefore), "read-only meter leaves an absent database absent");
        await j.profiles.setItem("connections", { version: 1, profiles: [{ id: identity.profileId, origin: client.origin, fingerprint: client.fingerprint, savedKey: key, name: "本机空间合成验收" }], activeProfileId: identity.profileId });
        const metered = await j.readLocalStorageUsage(["infinite-canvas", "comfy-aggregator-studio:journal", "comfy-aggregator-studio"]);
        verify(metered.contentBytes >= 1048576 + 2048 && metered.databases.length === 3 && metered.databases.find((value) => value.name.endsWith(":journal")).stores.find((value) => value.name === "results").bytes >= 1048576, "three-database totals include nested original Blob bytes");
        const storageDescriptor = Object.getOwnPropertyDescriptor(navigator, "storage");
        try {
            Object.defineProperty(navigator, "storage", { configurable: true, value: { estimate: async () => ({ usage: 1048576, quota: 134217728 }) } });
            const available = await j.readLocalStorageUsage(["infinite-canvas"]);
            verify(available.siteEstimateAvailable && available.usage === 1048576 && available.quota === 134217728, "available browser estimates remain distinct from content totals");
            Object.defineProperty(navigator, "storage", { configurable: true, value: { estimate: async () => { throw new Error("estimate unavailable"); } } });
            const unavailable = await j.readLocalStorageUsage(["infinite-canvas"]);
            verify(!unavailable.siteEstimateAvailable && unavailable.quota === null, "failed browser estimates are explicitly unavailable without inventing a quota");
        } finally { if (storageDescriptor) Object.defineProperty(navigator, "storage", storageDescriptor); else delete navigator.storage; }
        const rawTransaction = IDBDatabase.prototype.transaction;
        try { IDBDatabase.prototype.transaction = function (...args) { const tx = rawTransaction.apply(this, args); if (this.name === "infinite-canvas") queueMicrotask(() => tx.abort()); return tx; }; await fails(() => j.readLocalStorageUsage(["infinite-canvas"]), ""); }
        finally { IDBDatabase.prototype.transaction = rawTransaction; }
        verify(true, "aborted meter rejects instead of reporting success");
        await j.journalPut("uploads", base);
        await j.journalPut("uploads", { ...base, id: "other", name: "OTHER-IDENTITY-PRIVATE.png", identity: { ...identity, fingerprint: other.fingerprint } });
        verify((await j.readUploadCache(identity)).length === 1, "cache directory is partitioned by source and Key fingerprint");
        const selection = async () => (await j.readUploadCache(identity)).find((value) => value.id === base.id);
        const remove = async () => j.deleteCachedUpload(await selection(), identity, () => true);
        for (const stage of ["prepared", "submitting", "unknown", "accepted", "terminal"]) {
            await j.journalPut("intents", task("protect", { references: [{ image: { asset_id: asset.id } }] }, stage));
            await fails(remove, "仍引用"); await j.journalDelete("intents", "protect");
        }
        verify(true, "prepared, submitting, unknown, accepted and terminal nested references all protect caches");
        for (const input of [{ source_image: { asset_id: asset.id } }, { mask: { asset_id: asset.id } }]) { await j.journalPut("intents", task("roles", input)); await fails(remove, "仍引用"); await j.journalDelete("intents", "roles"); }
        verify(true, "source-image and mask roles protect the original cache");
        for (const record of [{ ...task("invalid", {}), body: "{" }, { ...task("invalid", {}), version: 2 }, { ...task("invalid", {}), identity: undefined }]) { await j.journalPut("intents", record); await fails(remove, "保留"); await j.journalDelete("intents", "invalid"); }
        verify(true, "corrupt bodies, unsupported versions and unowned task records fail closed");
        const selected = await selection();
        await fails(() => j.deleteCachedUpload(selected, { ...identity, fingerprint: other.fingerprint }, () => true), "原连接");
        await fails(() => j.deleteCachedUpload(selected, identity, () => false), "切换");
        verify(Boolean(await j.journalGet("uploads", base.id)), "wrong identity or identity changes refuse destructive work inside the transaction");
        await j.journalPut("uploads", { ...base, name: "changed.png" }); await fails(() => j.deleteCachedUpload(selected, identity, () => true), "已变化"); await j.journalPut("uploads", base);
        verify(true, "confirmation snapshot refuses changed upload records");
        const beforeDelete = JSON.stringify(await j.journalList("documents"));
        const staleSelection = await selection(); await j.prepareIntent(task("new-reference", { source_image: { asset_id: asset.id } })); await fails(() => j.deleteCachedUpload(staleSelection, identity, () => true), "仍引用"); await j.journalDelete("intents", "new-reference");
        verify(true, "newly committed references defeat an earlier cleanup selection");
        const rawDelete = IDBObjectStore.prototype.delete;
        try { IDBObjectStore.prototype.delete = function (...args) { const request = rawDelete.apply(this, args); if (this.name === "uploads") this.transaction.abort(); return request; }; await fails(remove, ""); }
        finally { IDBObjectStore.prototype.delete = rawDelete; }
        verify(Boolean(await j.journalGet("uploads", base.id)), "transaction abort retains cache bytes");
        await remove();
        await fails(() => j.updateUpload(base, (value) => ({ ...value, asset })), "已清理");
        await j.prepareIntent(task("after-cleanup", { source_image: { asset_id: asset.id } }));
        verify(!(await j.journalGet("uploads", base.id)) && Boolean(await j.journalGet("intents", "after-cleanup")) && JSON.stringify(await j.journalList("documents")) === beforeDelete && Boolean(await j.journalGet("results", "meter")), "cleanup-first ordering removes only the confirmed cache; late updates cannot resurrect it and existing data stays intact");
        await j.journalDelete("intents", "after-cleanup"); await j.journalPut("uploads", base);
        const updated = await j.updateUpload(base, (value) => value.asset ? value : { ...value, error: "late failure" });
        verify(updated.asset.id === asset.id && !updated.error, "late upload failure cannot erase an acknowledged outcome");
        await j.journalPut("uploads", { ...base, id: "pending", name: "上传结果未知.png", asset: undefined, error: "lost response" });
        await j.journalPut("uploads", { ...base, id: "mismatch", name: "回执不匹配.png", asset: { ...asset, bytes: 7 } });
        await j.journalPut("uploads", { ...base, id: "protected", name: "任务中的参考.png", asset: { ...asset, id: "protected-asset" } });
        await j.journalPut("intents", task("ui-protected", { source_image: { asset_id: "protected-asset" } }, "terminal"));
        verify((await j.readUploadCache(identity)).filter((value) => value.blockedReason).length === 3, "pending/unknown, mismatched and task-owned records remain visible and protected");
        return { done, identity, baseId: base.id };
    }, { key, otherKey, asset });
    result.done.forEach((name) => check(name));
    await page.evaluate(async ({ key, asset, identity }) => {
        const j = await import("/probe.mjs"), client = new j.AggregatorClient(location.origin, key);
        j.useStudioStore.setState({ client, activeProfileId: identity.profileId });
        await j.journalPut("uploads", { version: 1, id: "late-network", identity, name: "late.png", blob: new Blob(["original"], { type: "image/png" }), sha256: asset.sha256, idempotencyKey: "late-upload", createdAt: 1 });
        globalThis.lateUploadResult = j.uploadReference("late-network").then(() => "unexpected-success", (error) => error.message);
    }, { key, asset, identity: result.identity });
    await uploadStartedPromise;
    await page.evaluate(async ({ identity, asset }) => {
        const j = await import("/probe.mjs"), value = await j.journalGet("uploads", "late-network");
        await j.updateUpload(value, (current) => ({ ...current, asset }));
        const selected = (await j.readUploadCache(identity)).find((entry) => entry.id === "late-network");
        await j.deleteCachedUpload(selected, identity, () => true);
    }, { identity: result.identity, asset });
    heldUploads.shift()();
    assert.match(await page.evaluate(() => globalThis.lateUploadResult), /已清理/);
    assert.equal(await page.evaluate(async () => Boolean(await (await import("/probe.mjs")).journalGet("uploads", "late-network"))), false);
    check("an actual delayed synthetic upload response cannot recreate a cache removed after another confirmed acknowledgement");
    // Actual UI starts from the independently persisted profile, not a test-injected UI store.
    const ui = await newPage(context, `http://${origin}/studio/storage`);
    await ui.getByRole("heading", { name: "给创作留出空间", exact: true }).waitFor();
    const eligible = ui.locator("article").filter({ has: ui.getByText("已确认的参考.png", { exact: true }) });
    await eligible.getByRole("button", { name: "清理缓存", exact: true }).waitFor();
    assert.ok(await eligible.getByRole("button", { name: "清理缓存", exact: true }).isEnabled());
    assert.equal(await ui.getByText("OTHER-IDENTITY-PRIVATE.png", { exact: true }).count(), 0);
    for (const name of ["上传结果未知.png", "回执不匹配.png", "任务中的参考.png"]) assert.ok(await ui.locator("article").filter({ has: ui.getByText(name, { exact: true }) }).getByRole("button", { name: "清理缓存", exact: true }).isDisabled());
    assert.ok(!(await ui.locator("body").innerText()).includes(key));
    check("production page hydrates its own profile, shows current-identity rows and disables protected records without exposing credentials");
    await shot(ui, "desktop-storage");
    await eligible.scrollIntoViewIfNeeded(); await shot(ui, "desktop-cache-controls");
    const cacheButton = eligible.getByRole("button", { name: "清理缓存", exact: true });
    await cacheButton.hover();
    await ui.waitForFunction(() => { const button = [...document.querySelectorAll("article button")].find((value) => value.textContent.includes("清理缓存") && !value.disabled); return button && getComputedStyle(button).color === "rgb(12, 12, 12)"; });
    const colors = await cacheButton.evaluate((element) => { const style = getComputedStyle(element); return { foreground: style.color, background: style.backgroundColor, border: style.borderColor }; });
    const luminance = (color) => { const rgb = color.match(/[\d.]+/g).slice(0, 3).map(Number).map((value) => value / 255).map((value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4); return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722; };
    const ratio = (Math.max(luminance(colors.foreground), luminance(colors.background)) + .05) / (Math.min(luminance(colors.foreground), luminance(colors.background)) + .05);
    assert.ok(ratio >= 4.5); report.button_hover = { ...colors, contrast: ratio };
    await cacheButton.focus(); assert.ok(await cacheButton.evaluate((element) => document.activeElement === element));
    check("shared Lime default-button hover has dark text and readable measured contrast with keyboard focus", report.button_hover);
    await eligible.getByRole("button", { name: "清理缓存", exact: true }).click(); await ui.getByRole("button", { name: /^保\s*留$/ }).click();
    assert.ok(await page.evaluate(async (id) => Boolean(await (await import("/probe.mjs")).journalGet("uploads", id)), result.baseId));
    check("actual confirmation can retain the selected original cache");
    await eligible.getByRole("button", { name: "清理缓存", exact: true }).click();
    await page.evaluate(async (identity) => { const j = await import("/probe.mjs"); await j.journalPut("intents", { version: 1, id: "ui-race", identity, body: JSON.stringify({ input: { source_image: { asset_id: "asset-cache-original" } } }) }); }, result.identity);
    await ui.locator(".ant-popconfirm").getByRole("button", { name: "清理缓存", exact: true }).click();
    await ui.getByText("原任务仍引用此参考图，已保留缓存", { exact: true }).first().waitFor();
    assert.ok(await page.evaluate(async (id) => Boolean(await (await import("/probe.mjs")).journalGet("uploads", id)), result.baseId));
    check("a second real page adding a reference while confirmation is open prevents the UI deletion");
    await page.evaluate(async () => { const j = await import("/probe.mjs"); await j.journalDelete("intents", "ui-race"); });
    await ui.getByRole("button", { name: "刷新占用", exact: true }).click();
    await eligible.getByRole("button", { name: "清理缓存", exact: true }).click();
    await ui.locator(".ant-popconfirm").getByRole("button", { name: "清理缓存", exact: true }).focus(); await ui.keyboard.press("Enter");
    await ui.getByText("本机参考缓存已清理，云端源图和任务未改变。", { exact: true }).waitFor();
    await eligible.waitFor({ state: "detached" });
    check("keyboard-confirmed cleanup persists and refreshes current usage without cloud deletion");
    await ui.reload(); await ui.getByText("上传结果未知.png", { exact: true }).waitFor(); assert.equal(await ui.getByText("已确认的参考.png", { exact: true }).count(), 0);
    assert.ok(await ui.getByText("当前浏览器未提供", { exact: true }).count());
    check("reload preserves cleanup and plain HTTP displays unavailable quota explicitly");
    await ui.setViewportSize({ width: 390, height: 844 });
    assert.ok(await ui.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); await shot(ui, "mobile-storage");
    await ui.getByRole("button", { name: "刷新占用", exact: true }).focus(); assert.equal(await ui.getByRole("button", { name: "刷新占用", exact: true }).evaluate((element) => document.activeElement === element), true);
    check("390px responsive storage and keyboard focus fit without horizontal overflow");
    await page.evaluate(async ({ identity, asset }) => { const j = await import("/probe.mjs"), pending = await j.journalGet("uploads", "pending"); await j.journalPut("uploads", { ...pending, id: "mobile-confirm", name: "移动端清理参考.png", error: undefined, identity, asset: { ...asset, id: "mobile-cache-asset" } }); }, { identity: result.identity, asset });
    await ui.getByRole("button", { name: "刷新占用", exact: true }).click();
    const mobile = ui.locator("article").filter({ has: ui.getByText("移动端清理参考.png", { exact: true }) });
    await mobile.getByRole("button", { name: "清理缓存", exact: true }).click();
    const popup = ui.locator(".ant-popconfirm"); await popup.waitFor();
    await shot(ui, "mobile-cache-confirmation");
    const bounds = await popup.boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    await popup.getByRole("button", { name: /^保\s*留$/ }).click();
    assert.ok(await ui.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    check("390px cache confirmation fits inside the viewport and retain preserves the original");
    await ui.setViewportSize({ width: 1440, height: 1000 });
    await ui.getByRole("link", { name: "已连接", exact: true }).click();
    await ui.getByLabel("业务 API Key", { exact: true }).fill(otherKey); await ui.getByRole("button", { name: "连接并读取模型", exact: true }).click(); await ui.getByRole("heading", { name: "服务已连接", exact: true }).waitFor();
    await ui.getByRole("link", { name: "查看本机存储空间", exact: true }).click(); await ui.getByText("OTHER-IDENTITY-PRIVATE.png", { exact: true }).waitFor();
    assert.equal(await ui.getByText("上传结果未知.png", { exact: true }).count(), 0);
    check("actual connection switch replaces the cache directory and hides previous-Key rows");
    await ui.setViewportSize({ width: 390, height: 844 }); await shot(ui, "mobile-new-identity");
    const estimated = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: "zh-CN" }); contexts.push(estimated);
    await estimated.addInitScript(() => Object.defineProperty(navigator, "storage", { configurable: true, value: { estimate: async () => ({ usage: 1048576, quota: 134217728 }) } }));
    const estimatedPage = await newPage(estimated, `http://${origin}/studio/storage`);
    await estimatedPage.getByRole("heading", { name: "站点空间使用 0.78%", exact: true }).waitFor();
    await estimatedPage.locator(".ant-progress").waitFor(); assert.ok(await estimatedPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    check("actual progress UI handles a synthetic available browser quota without treating record bytes as site usage");
    assert.equal(requests.filter((value) => value.method !== "GET" && !(value.method === "POST" && value.path === "/v1/assets")).length, 0);
    assert.equal(requests.filter((value) => value.method === "POST" && value.path === "/v1/assets").length, 1);
    assert.equal(report.page_errors.length, 0); assert.ok(!report.handler_failure); assert.deepEqual(await snapshot(), before);
    report.static_artifact_sha256 = before; report.static_artifact_unchanged = true; report.generation_posts = 0; report.production_mutations = 0; report.synthetic_upload_posts = 1; report.remote_delete_requests = 0;
    report.video = path.relative(root, await ui.video().path()).replaceAll("\\", "/");
    report.private_recording = report.video;
    check("all checks use unchanged production files, one synthetic delayed upload, no remote deletion/generation and no uncaught page errors"); report.passed = true;
} catch (error) { report.passed = false; report.failure = error.stack; console.error(error.stack); }
finally {
    for (const release of heldUploads) release();
    for (const page of pages) await page.close().catch(() => {}); report.cleanup.pages_closed = pages.every((page) => page.isClosed());
    for (const context of contexts) await context.close().catch(() => {}); report.cleanup.contexts_closed = contexts.every((context) => !context.pages().length);
    if (browser) { await browser.close(); report.cleanup.browser_closed = !browser.isConnected(); }
    await new Promise((resolve) => server.close(resolve)); report.cleanup.server_closed = !server.listening;
    report.completed_at = new Date().toISOString(); const target = path.join(evidence, `upload-cache-${label}.json`); await writeFile(target, JSON.stringify(report, null, 2) + "\n", { flag: "wx" }); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, target), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
