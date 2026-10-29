/** Verify real artifacts through server history, local library, players and downloads. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(root, "infinite-canvas/web/package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const connection = JSON.parse(await readFile(path.join(root, ".local/studio-ops/validation-key.json"), "utf8"));
const state = JSON.parse(await readFile(path.join(root, ".local/studio-ops/live-media-state.json"), "utf8"));
const operations = Object.entries(state.operations);
assert.equal(operations.length, 8);
assert.ok(operations.every(([, value]) => value.job.status === "succeeded" && value.original?.bytes_sha256_verified));
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const privateFolder = path.join(root, `.local/studio-library/${label}`);
const pictures = path.join(root, `docs/assets/studio/library-${label}`);
await Promise.all([mkdir(privateFolder, { recursive: true }), mkdir(pictures, { recursive: true })]);
const report = { started_at: new Date().toISOString(), scope: "actual public HTTP and original generated files through browser history/library/player/download; desktop Chromium and emulated mobile, not physical-phone acceptance", checks: [], artifacts: [], screenshots: [], requests: [], page_errors: [], cleanup: {} };
let browser, context, page, video, cdp, browserCdp;
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
const shot = async (name) => { const file = path.join(pictures, name + ".png"); await page.screenshot({ path: file, fullPage: true }); report.screenshots.push({ name, file: path.relative(root, file).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(file)).digest("hex") }); };
const checksum = async (file) => { const hash = createHash("sha256"); let size = 0; for await (const chunk of createReadStream(file)) { hash.update(chunk); size += chunk.length; } return { sha256: hash.digest("hex"), bytes: size }; };
async function memoryCounters() {
    const info = await browserCdp.send("SystemInfo.getProcessInfo");
    const ids = info.processInfo.map((item) => item.id);
    assert.ok(ids.length > 0 && ids.every((id) => Number.isSafeInteger(id) && id > 0));
    const script = `Get-Process -Id ${ids.join(",")} -ErrorAction SilentlyContinue | Select-Object Id,WorkingSet64,PrivateMemorySize64 | ConvertTo-Json -Compress`;
    const output = execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], { encoding: "utf8", windowsHide: true }).trim();
    const rows = JSON.parse(output); const processes = Array.isArray(rows) ? rows : [rows];
    return { processes, summed_working_set_bytes: processes.reduce((sum, row) => sum + row.WorkingSet64, 0), summed_private_bytes: processes.reduce((sum, row) => sum + row.PrivateMemorySize64, 0) };
}
try {
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--no-proxy-server"], downloadsPath: path.join(privateFolder, "downloads") });
    browserCdp = await browser.newBrowserCDPSession();
    context = await browser.newContext({ viewport: { width: 1360, height: 900 }, acceptDownloads: true, recordVideo: { dir: privateFolder, size: { width: 1360, height: 900 } } });
    page = await context.newPage(); video = page.video(); cdp = await context.newCDPSession(page);
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    await context.route("**/v1/**", async (route) => { const request = route.request(); const item = { method: request.method(), path: new URL(request.url()).pathname, cookie_present: Boolean(request.headers().cookie), bearer_present: Boolean(request.headers().authorization) }; report.requests.push(item); if (request.method() !== "GET") { report.unexpected_mutation = item; return route.abort(); } return route.continue(); });
    await page.goto(`${connection.origin}/studio/config`);
    await page.getByPlaceholder("输入管理员提供的 API Key").fill(connection.secret);
    await page.getByRole("button", { name: "连接并读取模型" }).click();
    await page.getByText("服务已连接", { exact: true }).waitFor();
    report.heap_before = await cdp.send("Runtime.getHeapUsage");
    report.process_memory_before = await memoryCounters();
    for (const [mode, operation] of operations) {
        const metadata = operation.original.metadata;
        await page.getByRole("link", { name: "任务", exact: true }).click();
        await page.getByRole("button", { name: "读取服务端历史", exact: true }).click();
        const task = page.locator(".studio-task-card").filter({ has: page.getByText(operation.job_id, { exact: true }) });
        await task.getByRole("button", { name: "查询任务与产物", exact: true }).click();
        await task.getByRole("button", { name: "查看产物信息", exact: true }).click();
        await task.getByRole("button", { name: "校验并载入本机", exact: true }).click();
        await page.locator(".ant-popconfirm").getByRole("button", { name: /确\s*定|^OK$/ }).click();
        await page.waitForFunction(async (contentId) => {
            const db = await new Promise((resolve, reject) => { const request = indexedDB.open("comfy-aggregator-studio:journal"); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
            try { return await new Promise((resolve, reject) => { const tx = db.transaction("results", "readonly"); const request = tx.objectStore("results").getAllKeys(); let keys; request.onsuccess = () => { keys = request.result; }; tx.oncomplete = () => resolve(keys.some((key) => String(key).endsWith(":" + contentId))); tx.onabort = () => reject(tx.error); }); } finally { db.close(); }
        }, metadata.id, { timeout: 180000 });
        await page.getByRole("link", { name: "素材", exact: true }).click();
        const card = page.locator(".studio-model-card").filter({ has: page.getByRole("heading", { name: metadata.id, exact: true }) });
        await card.waitFor();
        const artifact = { mode, job_id: operation.job_id, content_id: metadata.id, media_type: metadata.media_type, bytes: metadata.bytes, sha256: metadata.sha256, browser_playback: null };
        if (metadata.media_type === "model/gltf-binary") {
            assert.equal(await card.getByRole("button", { name: /预\s*览/ }).count(), 0);
            await shot("glb-delivery-card");
        } else {
            await card.getByRole("button", { name: /预\s*览/ }).click();
            const drawer = page.locator(".ant-drawer").filter({ hasText: "本机原件预览" });
            if (metadata.media_type.startsWith("image/")) {
                const image = drawer.getByAltText("生成原件");
                await image.waitFor();
                await image.evaluate((node) => node.decode());
                const size = await image.evaluate((node) => ({ width: node.naturalWidth, height: node.naturalHeight }));
                assert.equal(size.width, metadata.metadata.width); assert.equal(size.height, metadata.metadata.height);
                artifact.browser_playback = size;
            } else {
                const media = drawer.locator("video,audio");
                const observed = await media.evaluate(async (node) => {
                    if (node.readyState < 2) await new Promise((resolve, reject) => { node.addEventListener("loadeddata", resolve, { once: true }); node.addEventListener("error", () => reject(new Error("Original codec decode failed")), { once: true }); });
                    node.muted = true; await node.play();
                    await new Promise((resolve, reject) => {
                        const timer = setTimeout(() => { clearInterval(poll); reject(new Error(`Playback did not advance: ready=${node.readyState}, paused=${node.paused}, error=${node.error?.code ?? null}`)); }, 15000);
                        const poll = setInterval(() => { if (node.currentTime > 0) { clearTimeout(timer); clearInterval(poll); resolve(); } }, 100);
                    });
                    const value = { duration_ms: Math.round(node.duration * 1000), current_time: node.currentTime, ready_state: node.readyState, error: node.error?.code ?? null, width: node.videoWidth ?? null, height: node.videoHeight ?? null };
                    node.pause(); return value;
                });
                artifact.browser_playback = observed;
                report.in_progress = artifact;
                assert.equal(observed.error, null); assert.ok(observed.current_time > 0);
                artifact.duration_metadata_delta_ms = observed.duration_ms - metadata.metadata.duration_milliseconds;
                if (metadata.media_type.startsWith("audio/")) {
                    // The existing native audio validator permits 100ms between
                    // decoder progress timestamps and the file duration. Record
                    // that difference; independently require browser/file agreement.
                    artifact.existing_audio_validator_tolerance_ms = 100;
                    assert.ok(Math.abs(artifact.duration_metadata_delta_ms) <= 100);
                    assert.ok(Math.abs(observed.duration_ms - operation.original.independent_probe.duration_milliseconds) <= 2);
                } else {
                    assert.ok(Math.abs(artifact.duration_metadata_delta_ms) <= 2);
                }
            }
            await shot(mode + "-preview");
            await drawer.locator(".ant-drawer-close").click();
        }
        const downloadPromise = page.waitForEvent("download");
        await card.getByRole("button", { name: "另存原件", exact: true }).click();
        const download = await downloadPromise;
        const actual = await checksum(await download.path());
        assert.equal(actual.sha256, metadata.sha256); assert.equal(actual.bytes, metadata.bytes);
        if (metadata.media_type === "model/gltf-binary") assert.ok(download.suggestedFilename().endsWith(".glb"));
        artifact.download_verified = true; artifact.download_name = download.suggestedFilename(); report.artifacts.push(artifact);
        delete report.in_progress;
        artifact.process_memory_after_download = await memoryCounters();
        check("actual history/library/preview and original download: " + mode, { bytes: actual.bytes });
    }
    await shot("desktop-library");
    report.heap_after = await cdp.send("Runtime.getHeapUsage");
    report.process_memory_after = await memoryCounters();
    report.memory_observation_scope = "JS heap/embedder and Windows working-set/private-byte counters for this browser's CDP-reported PIDs. Samples after each download, not peak memory, a leak test, or physical-phone capacity acceptance; summed working sets can double-count shared pages.";
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
    await shot("mobile-library");
    check("all eight real originals are indexed in the desktop and mobile local library");
    assert.deepEqual(report.page_errors, []); assert.equal(report.unexpected_mutation, undefined);
    assert.ok(report.requests.every((request) => request.bearer_present && !request.cookie_present));
    report.passed = true;
} catch (error) {
    report.passed = false; report.failure = String(error?.stack || error); console.error(JSON.stringify({ passed: false, error: error.message }));
    if (page && !page.isClosed()) await shot("failure").catch(() => {});
} finally {
    if (cdp) await cdp.detach().catch(() => {});
    if (browserCdp) await browserCdp.detach().catch(() => {});
    if (page && !page.isClosed()) await page.close().catch(() => {});
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: true, browser_closed: !browser || !browser.isConnected() };
    if (video) report.private_recording = path.relative(root, await video.path()).replaceAll("\\", "/");
    report.finished_at = new Date().toISOString();
    await writeFile(path.join(root, `docs/research/studio-implementation/live-library-${label}.json`), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
