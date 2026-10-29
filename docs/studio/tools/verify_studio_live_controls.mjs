/** Read-only tour of the final live UI using this task's own persistent profile. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(root, "infinite-canvas/web/package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const source = JSON.parse(await readFile(path.join(root, ".local/studio-ops/static1313-browser-ready.json"), "utf8"));
assert.ok(path.resolve(source.profile).startsWith(path.join(root, ".local/studio-persistent") + path.sep));
const label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const pictures = path.join(root, `docs/assets/studio/controls-${label}`);
const privateFolder = path.join(root, `.local/studio-controls/${label}`);
await Promise.all([mkdir(pictures, { recursive: true }), mkdir(privateFolder, { recursive: true })]);
const report = { started_at: new Date().toISOString(), scope: "actual final public HTTP UI, real generated image and own persistent browser profile; desktop/mobile/landscape emulation, no additional generation or physical-phone acceptance", checks: [], screenshots: [], page_errors: [], requests: [], cleanup: {} };
let context, page, video;
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
const shot = async (name) => { await page.waitForTimeout(700); const file = path.join(pictures, name + ".png"); await page.screenshot({ path: file, fullPage: true }); report.screenshots.push({ name, file: path.relative(root, file).replaceAll("\\", "/"), sha256: createHash("sha256").update(await readFile(file)).digest("hex") }); };
async function scale(value) {
    const slider = page.getByRole("slider", { name: "放大/缩小画布", exact: true });
    await slider.focus(); await page.keyboard.press("Home");
    for (let index = 5; index < value; index++) await page.keyboard.press("ArrowRight");
}
async function controls() {
    const zoom = await page.locator(".studio-canvas-zoom").boundingBox();
    const tools = await page.locator("[data-canvas-bottom-toolbar]").boundingBox();
    assert.ok(zoom.y + zoom.height <= tools.y);
    await page.locator("[data-canvas-bottom-toolbar] button").first().click({ trial: true });
    await page.getByRole("button", { name: "重置视图", exact: true }).click();
    return { zoom, tools };
}
async function focusImage(selectFromPanel = false) {
    const toggle = page.locator(".studio-canvas-topbar button").first();
    if (selectFromPanel) {
        if (await toggle.getAttribute("aria-label") !== "收起面板") await toggle.click();
        await page.locator(".studio-canvas-sidepanel").getByTitle("定位到节点", { exact: true }).first().click();
        await page.waitForTimeout(700);
        await toggle.click();
        await page.locator(".studio-canvas-sidepanel").waitFor({ state: "detached" });
    }
    await page.getByRole("button", { name: "居中聚焦", exact: true }).click();
    await page.mouse.move(8, 160);
    await page.waitForTimeout(700);
    const box = await page.locator('[data-node-id] img[src^="blob:"]').first().boundingBox();
    const viewport = page.viewportSize();
    const top = await page.locator(".studio-canvas-topbar").boundingBox();
    const dock = await page.locator("[data-canvas-bottom-toolbar]").boundingBox();
    const toolbar = page.locator("[data-node-toolbar]").first();
    const actions = await toolbar.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= viewport.width);
    assert.ok(box.y >= top.y + top.height && box.y + box.height <= dock.y);
    assert.ok(actions.y >= top.y + top.height);
    if (viewport.width === 390) {
        assert.ok(actions.x >= 0 && actions.x + actions.width <= viewport.width);
        await toolbar.getByRole("button").first().click({ trial: true });
        await toolbar.getByRole("button").last().click({ trial: true });
        await toolbar.getByRole("button").first().scrollIntoViewIfNeeded();
    }
    return box;
}
try {
    context = await chromium.launchPersistentContext(source.profile, { executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--no-proxy-server"], viewport: { width: 1360, height: 900 }, recordVideo: { dir: privateFolder, size: { width: 1360, height: 900 } } });
    page = context.pages()[0] || await context.newPage(); video = page.video();
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    await context.route("**/v1/**", async (route) => { const request = route.request(); const item = { method: request.method(), path: new URL(request.url()).pathname, cookie_present: Boolean(request.headers().cookie), bearer_present: Boolean(request.headers().authorization) }; report.requests.push(item); if (request.method() !== "GET") { report.unexpected_mutation = item; return route.abort(); } return route.continue(); });
    await page.goto(source.project_url);
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    check("final static package restores the existing local canvas");
    await controls(); await scale(65); await focusImage(true); await shot("desktop-canvas");
    await page.setViewportSize({ width: 390, height: 844 });
    const toggle = page.locator(".studio-canvas-topbar button").first();
    if (await toggle.getAttribute("aria-label") === "收起面板") { await toggle.click(); await page.locator(".studio-canvas-sidepanel").waitFor({ state: "detached" }); }
    const mobile = await controls(); await scale(45);
    mobile.focused_image = await focusImage();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
    await shot("mobile-canvas");
    check("mobile reset/zoom and creation tools are independently clickable", mobile);
    await page.getByRole("button", { name: "新建创作", exact: true }).click();
    await page.getByRole("combobox", { name: "生成模型", exact: true }).click();
    await page.getByRole("combobox", { name: "生成模型", exact: true }).fill("5070");
    await page.locator(".ant-select-item-option").filter({ hasText: /双参考图生视频/ }).click();
    assert.equal(await page.locator("fieldset.studio-reference-slot").count(), 2);
    const drawer = page.locator(".studio-composer");
    await shot("mobile-multi-reference-composer");
    await drawer.locator(".ant-drawer-close").click();
    check("mobile multi-reference form exposes two distinct required reference slots");
    await page.setViewportSize({ width: 844, height: 390 });
    const landscape = await controls(); await scale(45); landscape.focused_image = await focusImage(); await shot("landscape-canvas");
    check("landscape reset/zoom and tools remain independently clickable", landscape);
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.getByRole("link", { name: "模型", exact: true }).click();
    await page.locator(".studio-model-card").first().waitFor();
    assert.equal(await page.locator(".studio-model-card").count(), 11);
    await shot("models");
    assert.deepEqual(report.page_errors, []); assert.equal(report.unexpected_mutation, undefined);
    assert.ok(report.requests.every((request) => request.bearer_present && !request.cookie_present));
    report.passed = true;
} catch (error) {
    report.passed = false; report.failure = String(error?.stack || error); console.error(JSON.stringify({ passed: false, error: error.message }));
    if (page && !page.isClosed()) await shot("failure").catch(() => {});
} finally {
    if (page && !page.isClosed()) await page.close().catch(() => {});
    if (context) await context.close().catch(() => {});
    report.cleanup = { page_closed: !page || page.isClosed(), context_and_browser_closed: true };
    if (video) report.private_recording = path.relative(root, await video.path()).replaceAll("\\", "/");
    report.finished_at = new Date().toISOString();
    await writeFile(path.join(root, `docs/research/studio-implementation/live-controls-${label}.json`), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
