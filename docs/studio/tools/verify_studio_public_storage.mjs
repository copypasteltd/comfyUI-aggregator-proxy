/** Preserve an owned browser profile across a real public static upgrade. No jobs or keys. */
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile, access } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const mode = process.argv[2];
if (!["before", "snapshot", "after"].includes(mode)) throw new Error("Choose before, snapshot or after");
const require = createRequire(path.join(root, "infinite-canvas/web/package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const owned = path.join(root, ".local/studio-public-storage-1317b");
const seedFile = path.join(owned, "seed.json");
const committedFile = path.join(owned, "committed-before.json");
if (mode === "before") { try { await access(seedFile); throw new Error("Owned seed already exists; do not replace it"); } catch (error) { if (error.code !== "ENOENT") throw error; } }
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const shots = path.join(root, `docs/assets/studio/public-storage-${label}`);
await Promise.all([mkdir(owned, { recursive: true }), mkdir(shots, { recursive: true })]);
const origin = "http://43.165.167.179:39443";
const report = { scope: "actual public HTTP and owned persistent Chromium profile; synthetic preexisting documents/local media, no API Key, generation or physical phone", phase: mode, checks: [], screenshots: [], page_errors: [], blocked_mutations: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
let context;
const pages = [];
try {
    context = await chromium.launchPersistentContext(path.join(owned, "profile"), { executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--no-proxy-server"], viewport: { width: 1440, height: 960 }, locale: "zh-CN", recordVideo: { dir: path.join(owned, "video") } });
    for (const existing of context.pages()) { pages.push(existing); await existing.close(); }
    await context.route("**/v1/**", (route) => { if (route.request().method() !== "GET" && route.request().method() !== "HEAD") { report.blocked_mutations.push(route.request().method()); return route.abort(); } return route.continue(); });
    const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message));
    await page.goto(origin + (mode === "snapshot" ? "/ready" : "/studio/canvas"));
    if (mode !== "snapshot") await page.getByRole("button", { name: "新建画布", exact: true }).first().waitFor();
    if (mode === "snapshot") {
        const seed = JSON.parse(await readFile(seedFile, "utf8"));
        const committed = await page.evaluate(async () => {
            const db = await new Promise((resolve, reject) => { const r = indexedDB.open("infinite-canvas"); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
            try { return await new Promise((resolve, reject) => { const tx = db.transaction("app_state", "readonly"); const output = {}; for (const [name, field] of [["infinite-canvas:canvas_store", "canvas"], ["infinite-canvas:asset_store", "assets"]]) { const r = tx.objectStore("app_state").get(name); r.onsuccess = () => output[field] = r.result; } tx.oncomplete = () => resolve(output); tx.onabort = () => reject(tx.error); }); } finally { db.close(); }
        });
        assert.equal(JSON.parse(committed.canvas).state.projects[0].id, seed.id);
        await writeFile(committedFile, JSON.stringify({ ...seed, ...committed }, null, 2), { flag: "wx" });
        check("exact legacy documents captured after old browser closes, without running app or changing records");
    } else if (mode === "before") {
        const manifest = JSON.parse(await readFile(path.join(root, ".local/studio-cloud-build/studio-20261030.131.6.json"), "utf8"));
        const index = await (await page.request.get(origin + "/studio/")).body();
        assert.equal(createHash("sha256").update(index).digest("hex"), manifest.files.find((file) => file.path === "studio/index.html").sha256);
        const png = await readFile(path.join(root, "docs/media/studio-131/image.png"));
        // Leave the old application before writing the isolated fixture, so its
        // existing debounce cannot replace this seed with the earlier empty state.
        await page.goto(origin + "/ready");
        const seed = await page.evaluate(async ({ bytes, now }) => {
            const blob = new Blob([Uint8Array.from(bytes)], { type: "image/png" });
            const key = "image:public-upgrade-1317";
            const id = "public-upgrade-1317";
            const project = { id, title: "升级前保存的测试画布", createdAt: now, updatedAt: now, nodes: [{ id: "source", type: "image", title: "升级保留原件", position: { x: 200, y: 160 }, width: 320, height: 320, metadata: { storageKey: key, content: "blob:legacy-fixture", naturalWidth: 1024, naturalHeight: 1024, bytes: blob.size, mimeType: blob.type, status: "success" } }], connections: [], chatSessions: [], activeChatId: null, backgroundMode: "lines", showImageInfo: false, viewport: { x: 0, y: 0, k: 1 } };
            const canvas = JSON.stringify({ version: 0, state: { projects: [project], deletedProjects: [] } });
            const assets = JSON.stringify({ version: 0, state: { assets: [] } });
            const write = async (database, stores, values) => {
                const db = await new Promise((resolve, reject) => { const r = indexedDB.open(database); r.onupgradeneeded = () => { for (const name of stores) if (!r.result.objectStoreNames.contains(name)) { const store = r.result.createObjectStore(name, database.includes("journal") ? { keyPath: "id" } : undefined); if (name === "intents") store.createIndex("active_node", "activeNodeKey", { unique: true }); } }; r.onerror = () => reject(r.error); r.onsuccess = () => resolve(r.result); });
                try { await new Promise((resolve, reject) => { const tx = db.transaction(stores, "readwrite"); for (const [store, value, key] of values) { if (key === undefined) tx.objectStore(store).put(value); else tx.objectStore(store).put(value, key); } tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); }); return db.version; } finally { db.close(); }
            };
            const primary = await new Promise((resolve, reject) => { const r = indexedDB.open("infinite-canvas"); r.onsuccess = () => { const db = r.result; const version = db.version; const stores = [...db.objectStoreNames]; db.close(); resolve({ version, stores }); }; r.onerror = () => reject(r.error); });
            if (!primary.stores.includes("image_files")) {
                await new Promise((resolve, reject) => { const r = indexedDB.open("infinite-canvas", primary.version + 1); r.onupgradeneeded = () => r.result.createObjectStore("image_files"); r.onsuccess = () => { r.result.close(); resolve(); }; r.onerror = () => reject(r.error); });
            }
            await write("infinite-canvas", ["app_state", "image_files"], [["app_state", canvas, "infinite-canvas:canvas_store"], ["app_state", assets, "infinite-canvas:asset_store"], ["image_files", blob, key]]);
            const journalVersion = await write("comfy-aggregator-studio:journal", ["intents", "uploads", "results", "budgets"], [["results", { id: "synthetic-preserved-record", version: 1, blob, metadata: { media_type: blob.type, bytes: blob.size }, savedAt: 1 }], ["budgets", { id: "synthetic-preserved-budget", nextAt: 42, pausedUntil: 0 }]]);
            return { id, key, canvas, assets, journalVersion, bytes: blob.size };
        }, { bytes: [...png], now: new Date().toISOString() });
        assert.equal(seed.journalVersion, 1);
        await writeFile(seedFile, JSON.stringify({ ...seed, sha256: createHash("sha256").update(png).digest("hex") }, null, 2), { flag: "wx" });
        await page.goto(origin + "/studio/canvas/" + seed.id);
        await page.locator('[data-node-id="source"] img[src^="blob:"]').waitFor();
        check("actual 131.6 UI renders preexisting synthetic canvas and local PNG before upgrade");
        check("v1 task stores and byte-preservation fixture committed before browser shutdown");
    } else {
        const seed = JSON.parse(await readFile(committedFile, "utf8"));
        const manifest = JSON.parse(await readFile(path.join(root, ".local/studio-cloud-build/studio-20261030.131.7.json"), "utf8"));
        const index = await (await page.request.get(origin + "/studio/")).body();
        assert.equal(createHash("sha256").update(index).digest("hex"), manifest.files.find((file) => file.path === "studio/index.html").sha256);
        const read = await page.evaluate(async ({ key }) => {
            const db = await new Promise((resolve, reject) => { const r = indexedDB.open("comfy-aggregator-studio:journal"); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
            try { return await new Promise((resolve, reject) => { const tx = db.transaction(["documents", "results", "budgets"], "readonly"); const output = { version: db.version }; const docs = tx.objectStore("documents").getAll(); docs.onsuccess = () => output.documents = docs.result; const original = tx.objectStore("results").get("synthetic-preserved-record"); original.onsuccess = () => output.blob = original.result?.blob; const budget = tx.objectStore("budgets").get("synthetic-preserved-budget"); budget.onsuccess = () => output.budget = budget.result; tx.oncomplete = async () => resolve({ ...output, blob: [...new Uint8Array(await output.blob.arrayBuffer())] }); tx.onabort = () => reject(tx.error); }); } finally { db.close(); }
        }, { key: seed.key });
        assert.equal(read.version, 2); assert.equal(read.budget.nextAt, 42);
        for (const [name, legacyValue] of [["infinite-canvas:canvas_store", seed.canvas], ["infinite-canvas:asset_store", seed.assets]]) assert.equal(read.documents.find((d) => d.id === name).legacyValue, legacyValue);
        assert.equal(createHash("sha256").update(Buffer.from(read.blob)).digest("hex"), seed.sha256);
        check("actual deployed v2 upgrade preserves legacy JSON, task-store original SHA and request budget");
        await page.goto(origin + "/studio/canvas/" + seed.id);
        const image = page.locator('[data-node-id="source"] img[src^="blob:"]'); await image.waitFor();
        await page.waitForFunction(() => { const img = document.querySelector('[data-node-id="source"] img'); return img?.complete && img.naturalWidth > 0 && img.naturalHeight > 0; });
        const decoded = await image.evaluate((img) => ({ width: img.naturalWidth, height: img.naturalHeight }));
        assert.equal(decoded.width, decoded.height);
        const media = await page.evaluate(async (key) => {
            const db = await new Promise((resolve, reject) => { const r = indexedDB.open("infinite-canvas"); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
            try { const blob = await new Promise((resolve, reject) => { const tx = db.transaction("image_files", "readonly"); const r = tx.objectStore("image_files").get(key); let blob; r.onsuccess = () => blob = r.result; tx.oncomplete = () => resolve(blob); tx.onabort = () => reject(tx.error); }); return [...new Uint8Array(await blob.arrayBuffer())]; } finally { db.close(); }
        }, seed.key);
        assert.equal(createHash("sha256").update(Buffer.from(media)).digest("hex"), seed.sha256);
        assert.equal(await page.getByText("本机编辑尚未保存", { exact: true }).count(), 0);
        check("deployed upgraded canvas decodes its preview and retains exact local PNG after owned browser restart", { preview_dimensions: decoded, original_sha256: seed.sha256 });
        await page.reload(); await image.waitFor(); check("upgraded public project deep link survives another reload");
    }
    const file = path.join(shots, mode + ".png"); await page.screenshot({ path: file, fullPage: true }); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(file)).digest("hex") });
    assert.equal(report.page_errors.length, 0); assert.equal(report.blocked_mutations.length, 0); report.passed = true;
} catch (error) {
    report.passed = false; report.failure = error.stack; console.error(error.stack);
    const page = pages.at(-1);
    if (page && !page.isClosed()) {
        report.diagnostics = await page.evaluate(() => ({ url: location.href, images: [...document.querySelectorAll('[data-node-id] img')].map((img) => ({ src: img.src, complete: img.complete, width: img.naturalWidth, height: img.naturalHeight })), body: document.body.innerText.slice(0, 1000) })).catch(() => undefined);
        const file = path.join(shots, "failure.png"); await page.screenshot({ path: file, fullPage: true }).catch(() => {});
        report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), diagnostic_failure: true });
    }
}
finally {
    for (const page of pages) await page.close().catch(() => {});
    report.cleanup.pages_closed = pages.every((page) => page.isClosed());
    if (context) { await context.close(); report.cleanup.context_closed = context.pages().length === 0; }
    report.completed_at = new Date().toISOString();
    const file = path.join(root, `docs/research/studio-implementation/public-storage-${mode}-${label}.json`); await writeFile(file, JSON.stringify(report, null, 2) + "\n"); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
