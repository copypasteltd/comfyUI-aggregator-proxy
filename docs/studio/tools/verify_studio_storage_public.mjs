/** Public storage route/CSP/static verification in a fresh owned browser; no Key or server mutation. */
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url)), web = path.join(root, "infinite-canvas/web"), dist = path.join(web, "dist");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const origin = "http://43.165.167.179:39443", release = "studio-20261030.135.1", label = new Date().toISOString().replaceAll(/[:.]/g, "-");
const shots = path.join(root, `docs/assets/studio/public-storage-space-${label}`), evidence = path.join(root, "docs/research/studio-implementation");
await mkdir(shots, { recursive: true });
const hash = (value) => createHash("sha256").update(value).digest("hex");
const expected = {}; async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else expected[path.relative(dist, file).replaceAll("\\", "/")] = hash(await readFile(file)); } } await visit(dist);
const report = { passed: false, scope: "Actual public HTTP storage route in a fresh unauthenticated owned browser: CSP, unknown estimate, navigation, mobile and received static hashes; native synthetic tests separately cover authenticated cache removal. No business Key/GPU/server mutation.", static_release: release, checks: [], screenshots: [], page_errors: [], blocked_mutations: [], received_static_sha256: {}, cleanup: {} };
let browser, context; const pages = [], pending = [], business = [];
const check = (name) => report.checks.push({ name, passed: true });
async function shot(page, name) { const file = path.join(shots, name + ".png"); await page.screenshot({ path: file, fullPage: true, animations: "disabled" }); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), sha256: hash(await readFile(file)) }); }
try {
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true });
    context = await browser.newContext({ viewport: { width: 1440, height: 960 }, locale: "zh-CN" });
    await context.route("**/*", async (route) => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin !== origin || !["GET", "HEAD"].includes(request.method())) { report.blocked_mutations.push({ method: request.method(), origin: url.origin, path: url.pathname }); await route.abort(); return; }
        if (url.pathname.startsWith("/v1/")) business.push({ method: request.method(), path: url.pathname });
        await route.continue();
    });
    const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message));
    page.on("response", (response) => { const url = new URL(response.url()); if (url.origin === origin && url.pathname.startsWith("/studio/")) pending.push((async () => { const body = await response.body(), name = url.pathname.slice(8); const file = Object.hasOwn(expected, name) ? name : "index.html"; assert.equal(response.status(), 200); assert.equal(hash(body), expected[file]); report.received_static_sha256[file] = hash(body); })().catch((error) => { report.response_failure = error.message; })); });
    const response = await page.goto(origin + "/studio/storage"); assert.equal(response.status(), 200);
    report.csp = response.headers()["content-security-policy"];
    await page.getByRole("heading", { name: "给创作留出空间", exact: true }).waitFor();
    await page.getByRole("button", { name: "刷新占用", exact: true }).waitFor();
    await page.getByText("当前浏览器未提供", { exact: true }).first().waitFor();
    await page.evaluate(() => document.fonts.ready);
    assert.ok(report.csp.includes("script-src 'self'") && report.csp.includes("connect-src http: https: blob:"));
    await shot(page, "public-storage-desktop"); check("public storage deep link loads actual shipped route and policy, showing unavailable browser estimates explicitly");
    await page.getByRole("link", { name: "连接原服务与 API Key 后管理对应缓存", exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await page.locator("article").count(), 0); assert.equal(await page.getByRole("button", { name: "清理缓存", exact: true }).count(), 0);
    check("unauthenticated public storage page exposes no identity-owned cache and performs no business query");
    await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole("heading", { name: "给创作留出空间", exact: true }).scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); await shot(page, "public-storage-mobile");
    await page.getByRole("link", { name: "备份任务清单", exact: true }).click(); await page.getByRole("heading", { name: "创作继续，任务也在继续", exact: true }).waitFor();
    await page.goto(origin + "/studio/storage"); await page.getByRole("heading", { name: "给创作留出空间", exact: true }).waitFor();
    check("390px route and task-backup navigation work on actual public HTTP");
    await page.reload(); await page.getByText("当前浏览器未提供", { exact: true }).first().waitFor(); await Promise.all(pending);
    assert.equal(report.page_errors.length, 0); assert.equal(report.blocked_mutations.length, 0); assert.equal(business.length, 0);
    assert.ok(!report.response_failure);
    assert.ok(Object.keys(report.received_static_sha256).some((name) => /storage-.*\.js$/.test(name)));
    report.business_requests = 0; report.generation_posts = 0; report.static_artifact_match = true;
    check("reload preserves route behavior and all received assets match current dist with zero business requests or uncaught errors"); report.passed = true;
} catch (error) { report.failure = error.stack; console.error(error.stack); }
finally {
    for (const page of pages) await page.close().catch(() => {}); report.cleanup.pages_closed = pages.every((page) => page.isClosed());
    if (context) { await context.close(); report.cleanup.context_closed = !context.pages().length; }
    if (browser) { await browser.close(); report.cleanup.browser_closed = !browser.isConnected(); }
    report.completed_at = new Date().toISOString(); const target = path.join(evidence, `public-storage-space-${label}.json`); await writeFile(target, JSON.stringify(report, null, 2) + "\n", { flag: "wx" }); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, target), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
