/** Actual HTTP and original generated file, persistent browser restart and UI upgrade.
 * This harness permits only GET business requests and closes its own browser.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(root, "infinite-canvas/web/package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const privateRoot = path.join(root, ".local/studio-ops");
const connection = JSON.parse(await readFile(path.join(privateRoot, "validation-key.json"), "utf8"));
const mediaState = JSON.parse(await readFile(path.join(privateRoot, "live-media-state.json"), "utf8"));
const original = mediaState.operations.image.original;
assert.ok(original?.bytes_sha256_verified);
const imageFile = path.join(root, ".local/studio-live-media", original.file);
assert.equal(createHash("sha256").update(await readFile(imageFile)).digest("hex"), original.metadata.sha256);
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const profile = path.join(root, `.local/studio-persistent/${label}`);
const screenshotRoot = path.join(root, `docs/assets/studio/upgrade-${label}`);
const recordingRoot = path.join(root, `.local/studio-upgrade/${label}`);
await Promise.all([mkdir(screenshotRoot, { recursive: true }), mkdir(recordingRoot, { recursive: true })]);
const ready = path.join(privateRoot, "static1313-browser-ready.json");
const deployed = path.join(privateRoot, "static1313-deployed.json");
try { await access(ready); throw new Error("Inspect existing upgrade validation before restarting"); } catch (error) { if (error.code !== "ENOENT") throw error; }
const report = { started_at: new Date().toISOString(), scope: "actual public HTTP and generated image imported through normal file input, persistent browser profile across actual static upgrade and browser restart; no additional generation or physical phone", source_job: mediaState.operations.image.job_id, original_sha256: original.metadata.sha256, checks: [], screenshots: [], requests: [], page_errors: [], private_recordings: [], cleanup: {} };
let context, page, video, projectUrl;
const options = { executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, viewport: { width: 1360, height: 900 }, args: ["--no-proxy-server"], recordVideo: { dir: recordingRoot, size: { width: 1360, height: 900 } } };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
async function launch() {
    context = await chromium.launchPersistentContext(profile, options);
    page = context.pages()[0] || await context.newPage(); video = page.video();
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    await context.route("**/v1/**", async (route) => {
        const request = route.request();
        const item = { method: request.method(), path: new URL(request.url()).pathname, cookie_present: Boolean(request.headers().cookie), bearer_present: Boolean(request.headers().authorization) };
        report.requests.push(item);
        if (request.method() !== "GET") { report.unexpected_mutation = item; return route.abort(); }
        return route.continue();
    });
}
async function close() {
    if (page && !page.isClosed()) await page.close();
    if (context) await context.close();
    if (video) report.private_recordings.push(path.relative(root, await video.path()).replaceAll("\\", "/"));
    context = undefined; page = undefined; video = undefined;
}
async function shot(name) {
    await page.waitForTimeout(600);
    const file = path.join(screenshotRoot, name + ".png");
    await page.screenshot({ path: file, fullPage: true });
    report.screenshots.push({ name, file: path.relative(root, file).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(file)).digest("hex") });
}
async function verifyImage() {
    const image = page.locator('[data-node-id] img[src^="blob:"]').first();
    await image.waitFor();
    const dimensions = await image.evaluate((node) => ({ width: node.naturalWidth, height: node.naturalHeight }));
    // Ordinary imported nodes intentionally render a 768px preview. Verify the
    // separate persisted original bytes rather than mistaking it for data loss.
    assert.deepEqual(dimensions, { width: 768, height: 768 });
    const originalBytes = await page.evaluate(async () => {
        const db = await new Promise((resolve, reject) => { const request = indexedDB.open("infinite-canvas"); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        try {
            const blobs = await new Promise((resolve, reject) => { const tx = db.transaction("image_files", "readonly"); const request = tx.objectStore("image_files").getAll(); let values; request.onsuccess = () => { values = request.result; }; tx.oncomplete = () => resolve(values); tx.onabort = () => reject(tx.error); });
            if (blobs.length !== 1 || !(blobs[0] instanceof Blob)) throw new Error("Expected one persisted original Blob");
            return await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsDataURL(blobs[0]); });
        } finally { db.close(); }
    });
    assert.equal(createHash("sha256").update(Buffer.from(originalBytes.split(",")[1], "base64")).digest("hex"), original.metadata.sha256);
}
try {
    await launch();
    await page.goto(`${connection.origin}/studio/config`);
    await page.getByPlaceholder("输入管理员提供的 API Key").fill(connection.secret);
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "连接并读取模型" }).click();
    await page.getByText("服务已连接", { exact: true }).waitFor();
    await page.getByRole("link", { name: "画布", exact: true }).click();
    await page.getByRole("button", { name: "新建画布", exact: true }).first().click();
    await page.getByRole("button", { name: "新建创作", exact: true }).waitFor();
    projectUrl = page.url();
    await page.locator('input[type="file"][accept^="image/*,video/*"]').setInputFiles(imageFile);
    await verifyImage();
    await shot("before-upgrade");
    check("existing public UI imports independently verified generated original into a real local canvas");
    await writeFile(ready, JSON.stringify({ project_url: projectUrl, profile, at: new Date().toISOString() }), { flag: "wx" });
    console.log(JSON.stringify({ waiting_for: "verified static1313 deployment receipt" }));
    for (;;) {
        try { await access(deployed); break; } catch { await page.waitForTimeout(1000); }
    }
    assert.equal(JSON.parse(await readFile(deployed, "utf8")).passed, true);
    await page.reload(); await verifyImage();
    check("actual 1312-to-1313 upgrade preserves canvas and original without resetting storage");
    await shot("desktop-after-upgrade");
    await close(); await launch();
    await page.goto(projectUrl); await verifyImage();
    check("restarting the owned persistent browser preserves canvas, connection and image");
    await page.setViewportSize({ width: 390, height: 844 });
    const toggle = page.locator(".studio-canvas-topbar button").first();
    if (await toggle.getAttribute("aria-label") === "展开面板") await toggle.click();
    await page.waitForFunction(() => {
        const panel = document.querySelector(".studio-canvas-sidepanel")?.getBoundingClientRect();
        const toolbar = document.querySelector(".studio-canvas-topbar")?.getBoundingClientRect();
        return panel && toolbar && panel.y >= toolbar.bottom;
    });
    await page.getByRole("link", { name: "模型", exact: true }).click({ trial: true });
    await shot("mobile-panel-open");
    await toggle.click();
    await page.locator(".studio-canvas-sidepanel").waitFor({ state: "detached" });
    await page.getByRole("button", { name: "新建创作", exact: true }).click({ trial: true });
    await shot("mobile-panel-closed");
    check("390px panel and navigation remain usable after real upgrade and browser restart");
    await page.setViewportSize({ width: 844, height: 390 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 844);
    await page.getByRole("button", { name: "新建创作", exact: true }).click({ trial: true });
    await shot("landscape-canvas");
    check("landscape creation controls are reachable with no document overflow");
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.getByRole("link", { name: "模型", exact: true }).click();
    await page.locator(".studio-model-card").first().waitFor();
    assert.equal(await page.locator(".studio-model-card").count(), 11);
    await shot("models");
    await page.getByRole("link", { name: "任务", exact: true }).click();
    await page.getByRole("button", { name: "读取服务端历史", exact: true }).click();
    await page.getByText(report.source_job, { exact: true }).waitFor();
    await shot("server-history");
    check("fresh profile reads original server history without resubmitting generation");
    assert.deepEqual(report.page_errors, []);
    assert.equal(report.unexpected_mutation, undefined);
    assert.ok(report.requests.every((request) => request.bearer_present && !request.cookie_present));
    report.passed = true;
} catch (error) {
    report.passed = false; report.failure = String(error?.stack || error);
    console.error(JSON.stringify({ passed: false, error: error.message }));
    if (page && !page.isClosed()) await shot("failure").catch(() => {});
} finally {
    await close().catch(() => {});
    report.cleanup = { own_page_closed: true, own_context_and_browser_closed: !context };
    report.finished_at = new Date().toISOString();
    await writeFile(path.join(root, `docs/research/studio-implementation/live-upgrade-${label}.json`), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
