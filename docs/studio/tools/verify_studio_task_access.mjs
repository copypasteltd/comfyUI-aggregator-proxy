/** Owned native-browser acceptance for explicit Key rotation and immutable known-job access. */
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
const label = new Date().toISOString().replaceAll(/[:.]/g, "-"), temporary = path.join(root, `.local/studio-task-access/${label}`), shots = path.join(root, `docs/assets/studio/task-access-${label}`), evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([temporary, shots, evidence].map((folder) => mkdir(folder, { recursive: true })));
const hash = (data) => createHash("sha256").update(data).digest("hex");
async function snapshot() { const result = {}; async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else result[path.relative(dist, file).replaceAll("\\", "/")] = hash(await readFile(file)); } } await visit(dist); return result; }
const before = await snapshot(), fixture = JSON.parse(await readFile(path.join(web, "tests/fixtures/catalog.json"), "utf8"));
const keys = { old: "synthetic-original-access-key", current: "synthetic-rotated-access-key", third: "synthetic-third-access-key", wrong: "synthetic-no-access-key" };
const contentId = `content_${"a".repeat(32)}`, model = fixture.models[0].id;
let png;
const metadata = { id: contentId, bytes: 0, sha256: "", media_type: "image/png", expires_at: 4102444800, metadata: { width: 512, height: 512 } };
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-task-access"; export * from "./src/services/task-journal-format"; export * from "./src/services/task-journal-recovery"; export * from "./src/services/studio-documents"; export * from "./src/services/aggregator-engine"; export { AggregatorClient } from "./src/services/api/aggregator/client"; export { useStudioStore } from "./src/stores/use-studio-store"; import localforage from "localforage"; export const profiles=localforage.createInstance({name:"comfy-aggregator-studio",storeName:"profiles",driver:localforage.INDEXEDDB});', resolveDir: web }, bundle: true, format: "esm", platform: "browser", define: { "import.meta.env.VITE_STUDIO_MODE": '"managed"' }, tsconfig: path.join(web, "tsconfig.json"), outfile: path.join(temporary, "probe.mjs"), logLevel: "warning" });
const report = { scope: "Native IndexedDB and shipped production UI with synthetic Key rotation/API, immutable task and backup proofs, v1/v2/v3 preservation and stale-page protection; not real GPU, phone or production permission acceptance", checks: [], screenshots: [], page_errors: [], cleanup: {} };
let browser, mode = "normal"; const contexts = [], pages = [], requests = [], held = [];
const server = createServer((req, res) => void (async () => {
    const url = new URL(req.url, "http://owned.local"), pathname = url.pathname;
    const json = (status, body, headers = {}) => { res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers }); res.end(JSON.stringify(body)); };
    if (pathname === "/fixture/control") { mode = url.searchParams.get("mode") || "normal"; json(200, {}); return; }
    if (pathname === "/fixture/held") { json(200, { count: held.length }); return; }
    if (pathname === "/fixture/release") { for (const release of held.splice(0)) release(); mode = "normal"; json(200, {}); return; }
    if (pathname.startsWith("/v1/")) {
        const key = Object.entries(keys).find(([, value]) => req.headers.authorization === `Bearer ${value}`)?.[0];
        requests.push({ method: req.method, path: pathname, identity: key || "missing", cookie: Boolean(req.headers.cookie) });
        if (!key || key === "old") { json(401, { error: { code: "invalid_api_key", message: "Synthetic old Key is revoked" } }); return; }
        if (pathname === "/v1/access-policy") { json(200, { scopes: fixture.scopes, limits: {} }, { "X-Studio-Contract": "1" }); return; }
        if (pathname === "/v1/models") { json(200, { object: "list", data: fixture.models }); return; }
        if (pathname === "/v1/capabilities") { json(200, { object: "list", data: fixture.capabilities }); return; }
        if (pathname === "/v1/jobs" && req.method === "GET") { json(200, { data: [], has_more: false, next_cursor: null }); return; }
        if (/^\/v1\/jobs\/job_[a-f0-9]{32}$/.test(pathname) && req.method === "GET") {
            if (key === "wrong") { json(404, { error: { code: "not_found", message: "No access to the original task" } }); return; }
            if (mode === "denied") { json(403, { error: { code: "forbidden", message: "Synthetic read scope denied" } }); return; }
            const id = pathname.split("/").at(-1), status = id.endsWith("2") ? "queued" : "succeeded";
            const job = { id, object: "generation.job", model, task_type: "text_to_image", status, updated_revision: 2, created_at: 1, ...(status === "succeeded" ? { completed_at: 2, artifacts: [{ id: contentId }] } : {}) };
            if (mode === "wrong-id") job.id = `job_${"f".repeat(32)}`;
            if (mode === "wrong-model") job.model = "unrelated-model";
            if (mode === "wrong-type") job.task_type = "text_to_video";
            if (mode === "hold") held.push(() => json(200, job)); else json(200, job);
            return;
        }
        if (pathname.endsWith("/cancel") && req.method === "POST") { req.resume(); json(200, { id: pathname.split("/").at(-2), object: "generation.job", model, task_type: "text_to_image", status: "cancel_requested", updated_revision: 3, created_at: 1 }); return; }
        if (pathname === `/v1/content/${contentId}`) { if (key === "wrong") json(404, { error: { code: "not_found", message: "No content access" } }); else json(200, metadata); return; }
        if (pathname === `/v1/content/${contentId}/download`) { res.writeHead(200, { "Content-Type": "image/png", "Content-Length": png.length }); res.end(png); return; }
        req.resume(); json(500, { error: { code: "unexpected", message: "No generation or upload is allowed in this fixture" } }); return;
    }
    if (pathname === "/blank") { res.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp }); res.end("<!doctype html><title>Owned rotation acceptance</title>"); return; }
    let file;
    if (pathname === "/probe.mjs") file = path.join(temporary, "probe.mjs");
    else if (pathname.startsWith("/studio/")) { file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); if (file !== dist && !file.startsWith(dist + path.sep)) throw new Error("Unexpected path"); try { if (!(await stat(file)).isFile()) file = path.join(dist, "index.html"); } catch { file = path.join(dist, "index.html"); } }
    else { res.writeHead(404); res.end(); return; }
    const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };
    res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": path.basename(file).startsWith("schema-worker-") ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
})().catch((error) => { report.handler_failure = error.message; if (!res.headersSent) res.writeHead(500); res.end(); }));
const check = (name, detail = {}) => report.checks.push({ name, passed: true, detail });
async function newContext() { const value = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", recordVideo: { dir: temporary } }); contexts.push(value); return value; }
async function newPage(context, origin) { const value = await context.newPage(); pages.push(value); value.on("pageerror", (error) => report.page_errors.push(error.message)); await value.goto(`${origin}/blank`); return value; }
async function shot(page, name) { const file = path.join(shots, name + ".png"); await page.screenshot({ path: file, fullPage: true, animations: "disabled" }); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), sha256: hash(await readFile(file)) }); }
try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://task-access.test:${server.address().port}`;
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--host-resolver-rules=MAP task-access.test 127.0.0.1"] });
    const page = await newPage(await newContext(), origin);
    png = Buffer.from(await page.evaluate(() => { const canvas = document.createElement("canvas"); canvas.width = canvas.height = 512; const context = canvas.getContext("2d"); context.fillStyle = "#d2f53c"; context.fillRect(0, 0, 512, 512); context.fillStyle = "#0c0c0c"; context.font = "bold 30px sans-serif"; context.fillText("KEY ROTATION", 136, 240); context.font = "18px sans-serif"; context.fillText("Synthetic QA / no GPU", 155, 280); return canvas.toDataURL("image/png").split(",")[1]; }), "base64");
    metadata.bytes = png.length; metadata.sha256 = hash(png);
    const native = await page.evaluate(async ({ keys, model, metadata }) => {
        const j = await import("/probe.mjs"), done = [], verify = (value, name) => { if (!value) throw new Error(name); done.push(name); };
        const fails = async (run) => { try { await run(); } catch { return; } throw new Error("Expected refusal"); };
        const waitHeld = async () => { for (let attempt = 0; attempt < 1000; attempt++) { if ((await (await fetch("/fixture/held")).json()).count) return; await new Promise((resolve) => setTimeout(resolve, 5)); } throw new Error("Synthetic response hold did not begin"); };
        const current = new j.AggregatorClient(location.origin, keys.current), old = new j.AggregatorClient(location.origin, keys.old), third = new j.AggregatorClient(location.origin, keys.third), wrong = new j.AggregatorClient(location.origin, keys.wrong);
        const owner = { profileId: "original-profile", origin: old.origin, fingerprint: old.fingerprint }, identity = { profileId: "current-profile", origin: current.origin, fingerprint: current.fingerprint };
        const use = (client) => j.useStudioStore.setState({ client, activeProfileId: client === current ? identity.profileId : "other", catalog: { models: [], policy: { scopes: ["jobs:read"], limits: {} }, releaseGuard: true } });
        const task = (id, digit = "1") => ({ version: 1, id, identity: { ...owner }, projectId: "rotation-project", nodeId: id, body: JSON.stringify({ model, task_type: "text_to_image", input: { prompt: "原提示词，不因Key变化重发" }, parameters: { n: 1 }, client_request_id: id }), idempotencyKey: `studio-job-${id}`, releaseDigest: "b".repeat(64), stage: "accepted", createdAt: 1, updatedAt: 1, output: { mediaTypes: ["image/png"], maxBytes: 1048576 }, job: { id: `job_${digit.padStart(32, "0")}`, object: "generation.job", model, task_type: "text_to_image", status: digit === "2" ? "queued" : "succeeded", updated_revision: 1, created_at: 1, ...(digit === "2" ? {} : { artifacts: [{ id: metadata.id }] }) } });
        const known = task("native-known"); known.stage = "terminal"; known.canvas = { node: { id: known.nodeId, title: "原画布任务", type: "studio:task", position: { x: 200, y: 160 }, width: 340, height: 260, metadata: {} }, sourceIds: [] };
        await j.journalPut("intents", known); use(current);
        verify((await j.localTasks()).length === 0, "new identity cannot see or operate old task before explicit proof");
        const linked = await j.associateKnownTask(known);
        verify(linked.readAccess.length === 1 && linked.readAccess[0].identity.fingerprint === current.fingerprint && j.submissionSnapshot(linked) === j.submissionSnapshot(known), "successful GET atomically binds new Key while preserving all original submission bytes");
        await j.associateKnownTask(known);
        verify((await j.journalGet("intents", known.id)).readAccess.length === 1 && (await j.localTasks()).length === 1, "duplicate association is idempotent and task becomes visible to the new identity");
        await j.refreshIntent(known.id);
        const result = await j.saveResult(known.id, metadata.id);
        verify(result.identity.fingerprint === current.fingerprint && result.jobId === known.job.id && result.blob.size === metadata.bytes && !(await j.journalGet("results", `studio:${old.fingerprint}:${metadata.id}`)), "new Key reads original content into its own verified local result without moving old caches");
        await j.initializeDocuments({ "infinite-canvas:canvas_store": JSON.stringify({ version: 0, state: { projects: [{ id: known.projectId, nodes: [], connections: [] }] } }), "infinite-canvas:asset_store": null });
        await fails(() => j.deleteUnreferencedResult(result.id, identity));
        verify(Boolean(await j.journalGet("results", result.id)), "rotated-identity local result is protected by the original reconstructable canvas task");
        const active = task("native-active", "2"); await j.journalPut("intents", active); await j.associateKnownTask(active); const cancelled = await j.cancelIntent(active.id);
        verify(cancelled.job.status === "cancel_requested" && cancelled.identity.fingerprint === old.fingerprint, "explicit cancellation uses new credentials and retains original task identity");
        for (const stage of ["prepared", "submitting", "unknown"]) { const value = task(`native-${stage}`); delete value.job; value.stage = stage; await j.journalPut("intents", value); await fails(() => j.associateKnownTask(value)); await fails(() => j.submitIntent(value.id)); await fails(() => j.lookupIntent(value.id)); }
        verify(true, "prepared and unknown submissions cannot be adopted, looked up or generated with a changed Key");
        const foreign = task("foreign"); foreign.identity.origin = "http://other.invalid"; await fails(() => j.associateKnownTask(foreign));
        verify(true, "cross-origin records are refused before requesting the current Key");
        const denied = task("native-denied"); denied.stage = "terminal"; await j.journalPut("intents", denied); use(wrong); await fails(() => j.associateKnownTask(denied)); use(current);
        for (const mode of ["denied", "wrong-id", "wrong-model", "wrong-type"]) { await fetch(`/fixture/control?mode=${mode}`); await fails(() => j.associateKnownTask(denied)); }
        await fetch("/fixture/control");
        verify(!(await j.journalGet("intents", denied.id)).readAccess, "404/403 and wrong ID/model/type leave no permission association");
        const held = task("native-held"); held.stage = "terminal"; await j.journalPut("intents", held); await fetch("/fixture/control?mode=hold"); const pending = j.associateKnownTask(held).then(() => false, () => true); await waitHeld(); use(third); await fetch("/fixture/release"); verify(await pending && !(await j.journalGet("intents", held.id)).readAccess, "connection change while GET is pending prevents a stale permission write"); use(current);
        await fetch("/fixture/control?mode=hold"); const changed = j.associateKnownTask(held).then(() => false, () => true); await waitHeld(); await j.journalPut("intents", { ...held, body: held.body + " " }); await fetch("/fixture/release"); verify(await changed && !(await j.journalGet("intents", held.id)).readAccess, "changed original record while GET is pending prevents association"); await j.journalPut("intents", held);
        const put = IDBObjectStore.prototype.put; IDBObjectStore.prototype.put = function (value, ...rest) { if (this.name === "intents" && value.id === held.id && value.readAccess) { this.transaction.abort(); throw new DOMException("Synthetic quota", "QuotaExceededError"); } return put.call(this, value, ...rest); };
        try { await fails(() => j.associateKnownTask(held)); } finally { IDBObjectStore.prototype.put = put; }
        verify(!(await j.journalGet("intents", held.id)).readAccess, "aborted permission transaction leaves original state and association untouched");
        const source = await j.exportTaskJournal(owner); const archive = j.readTaskJournalFromOrigin(source, current.origin);
        verify(archive.identity.fingerprint === old.fingerprint && archive.tasks.every((value) => !value.readAccess) && archive.tasks.find((value) => value.id === known.id).body === known.body, "original-identity backups omit access proofs and preserve original body/Key partition");
        await fails(() => j.restoreOriginalTaskJournal(source, "http://other.invalid", () => true));
        verify(true, "old-identity restore refuses another service before writing");
        const profile = { id: "stored-original", name: "原连接", origin: old.origin, fingerprint: old.fingerprint, savedKey: keys.old }; j.useStudioStore.setState({ profiles: [profile], activeProfileId: profile.id }); await j.useStudioStore.getState().connect(location.origin, keys.current, false, "新连接", profile.id);
        const state = j.useStudioStore.getState(), previous = state.profiles.find((value) => value.id === profile.id);
        verify(state.activeProfileId !== profile.id && previous.fingerprint === old.fingerprint && !previous.savedKey && !state.profiles.find((value) => value.id === state.activeProfileId).savedKey, "connecting a new secret preserves original profile identity, removes its old secret and does not remember the new Key by default");
        use(current);
        const db = await new Promise((resolve, reject) => { const request = indexedDB.open("comfy-aggregator-studio:journal"); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); }); const version = db.version; db.close();
        await fails(() => new Promise((resolve, reject) => { const request = indexedDB.open("comfy-aggregator-studio:journal", 3); request.onsuccess = () => { request.result.close(); resolve(); }; request.onerror = () => reject(request.error); }));
        verify(version === 4, "database v4 blocks older v3 clients from deleting unrecognized rotated-identity references");
        globalThis.rotationFixture = { known, owner, identity, source };
        return { done, source, version, known };
    }, { keys, model, metadata });
    native.done.forEach((name) => check(name)); report.browser_storage_version = native.version;
    // A second real page shares the database but has an independent connection.
    const other = await newPage(contexts[0], origin);
    await other.evaluate(async (keys) => { const j = await import("/probe.mjs"); const client = new j.AggregatorClient(location.origin, keys.third); j.useStudioStore.setState({ client, activeProfileId: "third-profile" }); }, keys);
    mode = "hold";
    const race = Promise.all([other, page].map((value) => value.evaluate(async () => { const j = await import("/probe.mjs"), original = await j.journalGet("intents", "native-known"); await j.associateKnownTask(original); })));
    await page.waitForFunction(async () => (await (await fetch("/fixture/held")).json()).count === 2); for (const release of held.splice(0)) release(); mode = "normal"; await race;
    const bindings = await page.evaluate(async () => { const j = await import("/probe.mjs"); return (await j.journalGet("intents", "native-known")).readAccess.length; }); assert.equal(bindings, 2); check("two real pages preserve independent explicit Key associations in one latest-state transaction");
    await page.evaluate(async () => { const j = await import("/probe.mjs"), original = await j.journalGet("intents", "native-known"); await j.removeTaskAccess(original); });
    assert.deepEqual(await other.evaluate(async () => { const j = await import("/probe.mjs"), task = await j.journalGet("intents", "native-known"); return { links: task.readAccess.length, visible: (await j.localTasks()).some((value) => value.id === task.id) }; }), { links: 1, visible: true }); check("removing one Key association preserves another page's access and the original record");
    // Fresh browser restore proves archived association data is never a permission grant.
    const restored = await newPage(await newContext(), origin);
    const restore = await restored.evaluate(async ({ keys, source }) => { const j = await import("/probe.mjs"), client = new j.AggregatorClient(location.origin, keys.current); j.useStudioStore.setState({ client, activeProfileId: "restored-current" }); const before = await j.localTasks(); const result = await j.restoreOriginalTaskJournal(source, client.origin, () => true); const hidden = await j.localTasks(); const old = await j.journalGet("intents", "native-known"); await j.associateKnownTask(old); const visible = await j.localTasks(); return { before: before.length, hidden: hidden.length, added: result.added, visible: visible.length, verified: visible[0].recoveryVerified, originalFingerprint: visible[0].identity.fingerprint, newFingerprint: client.fingerprint }; }, { keys, source: native.source });
    assert.equal(restore.before, 0); assert.equal(restore.hidden, 0); assert.equal(restore.visible, 1); assert.ok(restore.verified && restore.originalFingerprint !== restore.newFingerprint); check("fresh browser restores old identity without network permission, then independently rechecks the known job");
    for (const version of [1, 2, 3]) {
        const migration = await newPage(await newContext(), origin);
        const result = await migration.evaluate(async ({ version }) => {
            const db = await new Promise((resolve, reject) => { const request = indexedDB.open("comfy-aggregator-studio:journal", version); request.onupgradeneeded = () => { const intents = request.result.createObjectStore("intents", { keyPath: "id" }); intents.createIndex("active_node", "activeNodeKey", { unique: true }); for (const name of ["uploads", "results", "budgets", ...(version >= 2 ? ["documents"] : [])]) request.result.createObjectStore(name, { keyPath: "id" }); }; request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
            await new Promise((resolve, reject) => { const tx = db.transaction(["intents", "uploads", "results", "budgets"], "readwrite"); tx.objectStore("intents").put({ version: 1, id: "preserved", body: "original body", identity: { origin: location.origin, fingerprint: "a".repeat(64) } }); tx.objectStore("uploads").put({ id: "upload", blob: new Blob(["original upload"]) }); tx.objectStore("results").put({ id: "result", blob: new Blob(["original result"]) }); tx.objectStore("budgets").put({ id: "budget", nextAt: 12345 }); tx.oncomplete = () => resolve(); tx.onabort = () => reject(tx.error); }); db.close();
            const j = await import("/probe.mjs"), task = await j.journalGet("intents", "preserved"), upload = await j.journalGet("uploads", "upload"), result = await j.journalGet("results", "result"), budget = await j.journalGet("budgets", "budget");
            const current = await new Promise((resolve, reject) => { const request = indexedDB.open("comfy-aggregator-studio:journal"); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); }); const actual = current.version, activeIndex = current.transaction("intents").objectStore("intents").indexNames.contains("active_node"), documents = current.objectStoreNames.contains("documents"); current.close();
            return { actual, activeIndex, documents, body: task.body, upload: await upload.blob.text(), result: await result.blob.text(), budget: budget.nextAt };
        }, { version });
        assert.deepEqual(result, { actual: 4, activeIndex: true, documents: true, body: "original body", upload: "original upload", result: "original result", budget: 12345 }); check(`native v${version} to v4 preserves task bytes, upload/result blobs, budgets and unique node index`);
    }
    const uiContext = await newContext(), ui = await newPage(uiContext, origin);
    await ui.evaluate(async ({ keys, source, known }) => {
        const j = await import("/probe.mjs"), client = new j.AggregatorClient(location.origin, keys.current);
        await j.restoreOriginalTaskJournal(source, client.origin, () => true);
        await j.journalPut("intents", known); // Same-browser rotation retains its original canvas seed; the separate backup proof above deliberately has none.
        const now = new Date().toISOString(), project = { id: "rotation-project", title: "换Key后的原画布", createdAt: now, updatedAt: now, nodes: [], connections: [], chatSessions: [], activeChatId: null, backgroundMode: "lines", showImageInfo: false, viewport: { x: 100, y: 100, k: 1 } };
        await j.initializeDocuments({ "infinite-canvas:canvas_store": null, "infinite-canvas:asset_store": null });
        await j.saveDocument("infinite-canvas:canvas_store", JSON.stringify({ version: 0, state: { projects: [project], deletedProjects: [] } }), 1);
        await j.profiles.setItem("connections", { version: 1, activeProfileId: "ui-current", profiles: [{ id: "ui-current", name: "新Key接管验收", origin: client.origin, fingerprint: client.fingerprint, savedKey: keys.current }] });
    }, { keys, source: native.source, known: native.known });
    mode = "normal"; await ui.goto(`${origin}/studio/task-access`); await ui.getByRole("heading", { name: "接管已知任务", exact: true }).waitFor();
    const card = ui.getByRole("article", { name: "接管任务 native-known", exact: true }); await card.getByRole("button", { name: "核对并接管", exact: true }).waitFor();
    const beforeUI = requests.filter((item) => /\/v1\/jobs\/job_/.test(item.path)).length;
    await shot(ui, "desktop-before-association"); await card.getByRole("button", { name: "核对并接管", exact: true }).click(); await ui.getByRole("button", { name: /^取\s*消$/ }).last().click(); assert.equal(requests.filter((item) => /\/v1\/jobs\/job_/.test(item.path)).length, beforeUI); check("production UI lists old records and cancelling confirmation sends no business request");
    await card.getByRole("button", { name: "核对并接管", exact: true }).click(); await ui.getByRole("button", { name: /^确\s*定$/ }).last().focus(); await ui.keyboard.press("Enter"); await card.getByText("已核对并关联", { exact: true }).waitFor(); check("keyboard confirmation in shipped UI establishes a known-job association after server GET"); await shot(ui, "desktop-associated");
    await ui.getByRole("link", { name: "返回任务", exact: true }).click(); await ui.getByText("原提示词，不因Key变化重发", { exact: true }).waitFor(); check("associated original task appears on the production Tasks page with its original prompt"); await shot(ui, "desktop-original-task");
    await ui.goto(`${origin}/studio/canvas/rotation-project`);
    await ui.locator('[data-node-id="native-known"]').getByRole("button", { name: "载入产物", exact: true }).click();
    const originalImage = ui.locator('[data-node-id="native-known"] img[src^="blob:"]'); await originalImage.waitFor();
    await ui.waitForFunction(() => document.querySelector('[data-node-id="native-known"] img')?.naturalWidth === 512);
    const savedIdentity = await ui.evaluate(async () => { const j = await import("/probe.mjs"), result = (await j.journalList("results"))[0], task = await j.journalGet("intents", "native-known"); return { result: result.identity.fingerprint, original: task.identity.fingerprint, associated: task.readAccess[0].identity.fingerprint, node: task.canvas.node.id }; });
    assert.equal(savedIdentity.result, savedIdentity.associated); assert.notEqual(savedIdentity.result, savedIdentity.original); assert.equal(savedIdentity.node, "native-known");
    check("shipped canvas reconstructs the original node and downloads verified content under the associated new identity"); await shot(ui, "desktop-original-canvas");
    await ui.reload(); await originalImage.waitFor(); check("associated canvas and local image survive a production page reload with the original submission preserved");
    await ui.goto(`${origin}/studio/task-access`); await card.getByText("已核对并关联", { exact: true }).waitFor(); check("production page reload retains the association without importing authority from a backup");
    await ui.setViewportSize({ width: 390, height: 844 }); await card.getByRole("button", { name: "重新核对权限", exact: true }).click(); const popup = ui.locator(".ant-popover-container").last(); await popup.waitFor(); const bounds = await popup.boundingBox(); assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 391); await shot(ui, "mobile-confirmation"); await ui.getByRole("button", { name: /^取\s*消$/ }).last().click(); check("390px confirmation fits the viewport and retains original records on cancel");
    await ui.getByLabel("选择旧身份任务清单", { exact: true }).setInputFiles({ name: "original-task-journal.json", mimeType: "application/json", buffer: Buffer.from(native.source) }); await ui.getByRole("dialog", { name: "恢复原身份记录" }).waitFor(); await shot(ui, "mobile-original-backup-preview"); await ui.getByRole("button", { name: "仅恢复原记录", exact: true }).click(); await ui.getByRole("dialog", { name: "恢复原身份记录" }).waitFor({ state: "hidden" }); check("old-identity backup preview and explicit restore work in the shipped mobile UI without new generation");
    assert.ok(!(await ui.locator("body").innerText()).includes(keys.current)); check("production UI omits plaintext Keys and distinguishes unknown submissions from adoptable jobs");
    report.private_recording = path.relative(root, await ui.video().path()).replaceAll("\\", "/");
    report.business_requests = requests; report.generation_posts = requests.filter((item) => item.method === "POST" && ["/v1/jobs", "/v1/jobs/lookup", "/v1/assets"].includes(item.path)).length;
    assert.equal(report.generation_posts, 0); assert.ok(requests.every((item) => !item.cookie)); assert.ok(requests.filter((item) => item.path.endsWith("/cancel")).every((item) => item.identity === "current"));
    report.synthetic_cancellations = requests.filter((item) => item.path.endsWith("/cancel")).length; report.production_mutations = 0;
    assert.deepEqual(await snapshot(), before); report.static_artifact_sha256 = before; report.static_artifact_unchanged = true;
    report.passed = !report.handler_failure && report.page_errors.length === 0;
} catch (error) {
    report.passed = false; report.failure = error.stack;
    const last = pages.at(-1);
    if (last && !last.isClosed()) {
        report.failure_page = await last.locator("body").innerText().then((value) => value.slice(0, 1600)).catch(() => "unavailable");
        await shot(last, "failed-owned-ui").catch(() => {});
    }
}
finally {
    for (const release of held.splice(0)) release();
    for (const page of pages) try { await page.close(); } catch {}
    for (const context of contexts) try { await context.close(); } catch {}
    try { await browser?.close(); report.cleanup.browser_closed = true; } catch { report.cleanup.browser_closed = false; }
    await new Promise((resolve) => server.close(resolve)); report.cleanup.server_closed = true;
    report.cleanup.contexts_closed = contexts.every((context) => context.pages().length === 0);
    report.at = new Date().toISOString(); report.private_recordings = path.relative(root, temporary).replaceAll("\\", "/");
    await writeFile(path.join(evidence, `task-access-${label}.json`), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: `docs/research/studio-implementation/task-access-${label}.json`, failure: report.failure, cleanup: report.cleanup }));
if (!report.passed) process.exitCode = 1;
