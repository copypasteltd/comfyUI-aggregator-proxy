/** Real public HTTP + real GPU acceptance. Own resources always close in finally. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, access as fileAccess } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(root, "infinite-canvas/web/package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const access = JSON.parse(await readFile(path.join(root, ".local/studio-ops/validation-key.json"), "utf8"));
const submitReceipt = path.join(root, ".local/studio-ops/ui-image-submit.json");
try { await fileAccess(submitReceipt); throw new Error("Original UI submission already exists; reconcile it before any new UI generation"); } catch (error) { if (error.code !== "ENOENT") throw error; }
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const evidence = path.join(root, "docs/research/studio-implementation");
const screenshots = path.join(root, `docs/assets/studio/live-${label}`);
const privateFolder = path.join(root, `.local/studio-live-browser/${label}`);
await Promise.all([mkdir(screenshots, { recursive: true }), mkdir(privateFolder, { recursive: true })]);
const report = { started_at: new Date().toISOString(), scope: "deployed native public HTTP, actual browser and actual GPU image; mobile viewport is emulation", checks: [], requests: [], screenshots: [], cleanup: {}, page_errors: [] };
let browser, context, page, projectUrl, currentJob;
let video;
let submissionError;
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
const shot = async (name) => {
    const file = path.join(screenshots, name + ".png");
    await page.screenshot({ path: file, fullPage: true });
    report.screenshots.push({ name, file: path.relative(root, file).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(file)).digest("hex") });
};
try {
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--no-proxy-server"] });
    context = await browser.newContext({ viewport: { width: 1360, height: 900 }, recordVideo: { dir: privateFolder, size: { width: 1360, height: 900 } } });
    page = await context.newPage(); video = page.video();
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    page.on("request", (request) => {
        const url = new URL(request.url());
        if (url.pathname.startsWith("/v1/")) report.requests.push({ method: request.method(), path: url.pathname, cookie_present: Boolean(request.headers().cookie), bearer_present: Boolean(request.headers().authorization) });
    });
    page.on("response", async (response) => {
        const url = new URL(response.url());
        if (url.pathname === "/v1/jobs" && response.request().method() === "POST" && !response.ok()) submissionError = `Actual submit HTTP ${response.status()}`;
        if (url.pathname.startsWith("/v1/jobs") && response.ok() && ["GET", "POST"].includes(response.request().method())) {
            try {
                const job = await response.json();
                if (job.object === "generation.job" && job.id) {
                    currentJob = job;
                    await writeFile(path.join(root, ".local/studio-ops/ui-image-job.json"), JSON.stringify({ projectUrl, job }, null, 2));
                }
            } catch { /* page closing can cancel an unused response read */ }
        }
    });
    await context.route("**/v1/jobs", async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        const body = route.request().postData();
        const committed = await page.evaluate(async (body) => {
            const db = await new Promise((resolve, reject) => { const req = indexedDB.open("comfy-aggregator-studio:journal", 1); req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); });
            try { return await new Promise((resolve, reject) => { const tx = db.transaction("intents", "readonly"); const req = tx.objectStore("intents").getAll(); let values; req.onsuccess = () => { values = req.result; }; tx.oncomplete = () => resolve(values.some((record) => record.body === body)); tx.onabort = () => reject(tx.error); }); } finally { db.close(); }
        }, body);
        assert.equal(committed, true);
        assert.match(route.request().headers()["x-expected-release-digest"], /^[a-f0-9]{64}$/);
        await writeFile(submitReceipt, JSON.stringify({ body, idempotencyKey: route.request().headers()["idempotency-key"], releaseDigest: route.request().headers()["x-expected-release-digest"], projectUrl }, null, 2), { flag: "wx" });
        check("actual POST follows committed immutable browser intent");
        await route.continue();
    });
    await page.goto(`${access.origin}/studio/config`);
    await page.getByPlaceholder("输入管理员提供的 API Key").fill(access.secret);
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "连接并读取模型" }).click();
    await page.getByText("服务已连接", { exact: true }).waitFor();
    assert.equal(await page.getByText("服务端画布契约尚未就绪", { exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => isSecureContext), false);
    check("real public HTTP connects to guarded production catalog");
    await shot("connection");
    await page.getByRole("link", { name: "模型", exact: true }).click();
    await page.locator(".studio-model-card").first().waitFor();
    assert.equal(await page.locator(".studio-model-card").count(), 11);
    assert.equal(await page.locator(".studio-model-warning").count(), 0);
    check("all 11 actual model descriptors, including GLB, validate in the isolated Worker");
    await shot("models");
    await page.getByRole("link", { name: "画布", exact: true }).click();
    await page.getByRole("button", { name: "新建画布", exact: true }).first().click();
    await page.getByRole("button", { name: "新建创作", exact: true }).waitFor();
    projectUrl = page.url();
    await page.getByRole("button", { name: "新建创作", exact: true }).click();
    await page.getByRole("combobox", { name: "生成模型" }).click();
    await page.locator(".ant-select-item-option").filter({ hasText: /lustify/i }).first().click();
    await page.getByRole("combobox", { name: "完整规格" }).click();
    await page.locator(".ant-select-item-option").filter({ hasText: "1024x1024" }).click();
    await page.getByRole("textbox", { name: "创作描述", exact: true }).fill("A single friendly small ivory ceramic toy robot with orange circular ears, full body centered, isolated on a plain pale gray background, studio product photograph, simple geometric shape, clean soft lighting, no text, no logo");
    await shot("composer");
    await page.getByRole("button", { name: "提交 1 项生成任务", exact: true }).click();
    for (;;) {
        if (submissionError) throw new Error(submissionError);
        if (currentJob && ["failed", "cancelled", "expired"].includes(currentJob.status)) throw new Error(`Actual image job ${currentJob.id} ended ${currentJob.status}: ${currentJob.error?.code}`);
        if (currentJob?.status === "succeeded") break;
        console.log(JSON.stringify({ actual_image_status: currentJob?.status || "awaiting submission response", job_id: currentJob?.id }));
        await page.waitForTimeout(10000);
    }
    await shot("completed-task");
    await page.getByRole("button", { name: "载入产物", exact: true }).click();
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor({ timeout: 60000 });
    const dimensions = await page.locator('[data-node-id] img[src^="blob:"]').first().evaluate((image) => ({ width: image.naturalWidth, height: image.naturalHeight }));
    assert.deepEqual(dimensions, { width: 1024, height: 1024 });
    check("actual GPU original validates and attaches as 1024x1024 native canvas image", { job_id: currentJob.id, dimensions });
    await shot("result");
    await page.reload();
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    check("deployed /studio/canvas deep refresh restores verified local original");
    await shot("result-after-refresh");
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth }));
    assert.equal(bounds.width, bounds.documentWidth);
    await shot("mobile-result");
    check("deployed mobile viewport has no horizontal document overflow", bounds);
    assert.ok(report.requests.every((request) => !request.cookie_present && request.bearer_present));
    check("actual business traffic contains Bearer without Admin cookies");
    assert.deepEqual(report.page_errors, []);
    report.passed = true;
} catch (error) {
    report.passed = false; report.failure = String(error?.stack || error);
    console.error(JSON.stringify({ passed: false, error: String(error?.message || error) }));
} finally {
    if (context) await context.storageState({ path: path.join(privateFolder, "browser-state.json"), indexedDB: true }).catch(() => {});
    if (page) await page.close().catch(() => {}); report.cleanup.page_closed = !page || page.isClosed();
    if (context) await context.close().catch(() => {}); report.cleanup.context_closed = true;
    if (browser) await browser.close().catch(() => {}); report.cleanup.browser_closed = !browser || !browser.isConnected();
    if (video) report.private_original_recording = path.relative(root, await video.path()).replaceAll("\\", "/");
    report.finished_at = new Date().toISOString();
    await writeFile(path.join(evidence, `live-browser-${label}.json`), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
