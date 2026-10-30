/** Exercise a static site and a different-origin existing-protocol fixture. Never submits GPU work. */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, stat, copyFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const reportFile = path.resolve(root, process.argv[2] || "");
const evidenceVersion = process.argv[3] || "138";
assert(/^\d+$/.test(evidenceVersion));
const bindAddress = process.argv.find((value) => value.startsWith('--bind-address='))?.slice(15) || '127.0.0.1';
assert(Object.values(os.networkInterfaces()).flat().some((item) => item.family === 'IPv4' && item.address === bindAddress), 'Use an existing native interface');
if (webEngine !== 'chromium') assert.notEqual(bindAddress, '127.0.0.1', 'Cross-engine ordinary-HTTP checks need a native non-loopback address');
const build = JSON.parse(await readFile(reportFile, "utf8"));
assert(build.passed && build.source_before === build.source_after);
const clean = build.clean_install_reused ? JSON.parse(await readFile(path.resolve(root, build.clean_install_reused.file), "utf8")) : build;
const dist = path.resolve(root, clean.private_logs, "repository/web/dist");
assert(dist.startsWith(path.join(root, ".local") + path.sep));
const label = webEngine + '-' + new Date().toISOString().replaceAll(/[:.]/g, "-");
const media = path.join(root, "docs/media/studio-" + evidenceVersion, ...(Number(evidenceVersion) >= 141 ? ["catalog-" + label] : [])), privateDir = path.join(root, ".local/studio" + evidenceVersion + "/browser-" + label);
await mkdir(media, { recursive: true }); await mkdir(privateDir, { recursive: true });
const fixture = JSON.parse(await readFile(path.join(root, "infinite-canvas/web/tests/fixtures/catalog.json"), "utf8"));
const require = createRequire(path.join(root, "infinite-canvas/web/package.json"));
const sha = (value) => createHash("sha256").update(value).digest("hex");
for (const [file, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), digest);
const report = { passed: false, at: new Date().toISOString(), build_report: path.relative(root, reportFile).replaceAll("\\", "/"), scope: "Two native HTTP origins on one Windows host and synthetic existing-API protocol; not a production API/GPU, cross-machine or physical touch test", checks: [], api_requests: [], screenshots: [], cleanup: {} };
const check = (name) => { report.checks.push(name); console.log(JSON.stringify({ passed: name })); };
const json = (response, status, body, headers = {}) => { response.writeHead(status, { "Content-Type": "application/json", ...headers }); response.end(JSON.stringify(body)); };
let browser, web, api;
const contexts = [], pages = [], videos = [];
let apiOrigin, webOrigin;
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
try {
    api = createServer((req, res) => {
        const pathname = new URL(req.url, "http://fixture.invalid").pathname;
        res.setHeader("Access-Control-Allow-Origin", "*");
        res.setHeader("Access-Control-Allow-Headers", req.headers["access-control-request-headers"] || "Authorization,Content-Type");
        res.setHeader("Access-Control-Allow-Methods", "GET,OPTIONS");
        res.setHeader("Access-Control-Expose-Headers", "X-Studio-Contract,X-Studio-Submission-Lookup");
        report.api_requests.push({ method: req.method, path: pathname, origin_matches_canvas: req.headers.origin === webOrigin, bearer_present: !!req.headers.authorization, cookie_present: !!req.headers.cookie });
        if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
        if (req.method !== "GET" || req.headers.authorization !== "Bearer synthetic-independent-key") { json(res, 401, { error: { code: "fixture_auth", message: "Fixture only" } }); return; }
        if (pathname === "/v1/access-policy") return json(res, 200, { scopes: fixture.scopes, limits: { rpm: { mode: "unlimited", value: null } } }, { "X-Studio-Contract": "1", "X-Studio-Submission-Lookup": "1" });
        if (pathname === "/v1/models") return json(res, 200, { data: fixture.models });
        if (pathname === "/v1/capabilities") return json(res, 200, { data: fixture.capabilities });
        json(res, 404, {});
    });
    await new Promise((resolve) => api.listen(0, bindAddress, resolve));
    apiOrigin = 'http://' + (bindAddress === '127.0.0.1' ? 'api-existing.test' : bindAddress) + ':' + api.address().port;
    web = createServer(async (req, res) => {
        try {
            const pathname = decodeURIComponent(new URL(req.url, "http://static.invalid").pathname);
            if (!pathname.startsWith("/studio/")) { res.writeHead(404); res.end(); return; }
            let target = path.resolve(dist, pathname.slice("/studio/".length));
            assert(target.startsWith(dist + path.sep) || target === dist);
            try { if (!(await stat(target)).isFile()) target = path.join(dist, "index.html"); }
            catch { if (pathname.startsWith("/studio/assets/")) { res.writeHead(404); res.end(); return; } target = path.join(dist, "index.html"); }
            const mime = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" }[path.extname(target)] || "application/octet-stream";
            const worker = path.basename(target).startsWith("schema-worker-");
            res.writeHead(200, { "Content-Type": mime, "Content-Security-Policy": worker ? "default-src 'self'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp, "X-Content-Type-Options": "nosniff" });
            res.end(await readFile(target));
        } catch { res.writeHead(500); res.end(); }
    });
    await new Promise((resolve) => web.listen(0, bindAddress, resolve));
    webOrigin = 'http://' + (bindAddress === '127.0.0.1' ? 'canvas-independent.test' : bindAddress) + ':' + web.address().port;
    assert.notEqual(webOrigin, apiOrigin);
    report.origins = { canvas: webOrigin, synthetic_existing_api: apiOrigin, native_non_loopback: bindAddress !== '127.0.0.1' };
    browser = await launchStudioWebBrowser({ root, temporary: privateDir, report, args: ["--host-resolver-rules=MAP canvas-independent.test 127.0.0.1,MAP api-existing.test 127.0.0.1", "--no-proxy-server"] });
    for (const mobile of [false, true]) {
        const name = mobile ? "390" : "desktop";
        const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, recordVideo: { dir: privateDir, size: { width: 960, height: 720 } } });
        contexts.push(context);
        await context.addCookies([{ name: 'synthetic-admin-cookie', value: 'must-not-send', url: apiOrigin }]);
        const page = await context.newPage(); pages.push(page); videos.push({ name, video: page.video() });
        const errors = []; page.on("pageerror", (error) => errors.push(error.message));
        const forbidden = [];
        page.on("request", (request) => {
            const url = new URL(request.url());
            if (!["http:", "https:"].includes(url.protocol)) return;
            if (url.origin === apiOrigin) {
                if (!url.pathname.startsWith("/v1/")) forbidden.push(url.pathname);
            } else if (url.origin !== webOrigin || /^\/(v1|admin|internal|api)(\/|$)/.test(url.pathname)) forbidden.push(url.origin + url.pathname);
        });
        await page.goto(webOrigin + "/studio/config");
        assert.equal(await page.evaluate(() => isSecureContext), false);
        await page.getByRole("heading", { name: "连接创作能力" }).waitFor();
        assert.equal(await page.getByPlaceholder("http://服务器:端口").inputValue(), "http://43.165.167.179:39443");
        assert.equal(report.api_requests.filter((request) => request.method === "GET").length, mobile ? 3 : 0);
        check(name + ":explicit-api-default-no-eager-api-call");
        await page.getByPlaceholder("http://服务器:端口").fill(apiOrigin);
        await page.getByPlaceholder("例如：我的 ComfyUI 服务").fill("独立画布协议验证");
        await page.getByPlaceholder("输入管理员提供的 API Key").fill("synthetic-independent-key");
        await page.getByRole("button", { name: "连接并读取模型" }).click();
        await page.getByRole("button", { name: "刷新目录" }).waitFor();
        await page.getByRole("link", { name: "打开管理后台" }).waitFor();
        assert.equal(await page.getByRole("link", { name: "打开管理后台" }).getAttribute("href"), apiOrigin + "/admin/");
        assert.equal(await page.getByPlaceholder("输入管理员提供的 API Key").inputValue(), "");
        check(name + ":cross-origin-catalog-business-key-only-admin-is-link");
        const connectionShot = path.join(media, "independent-connection-" + name + ".png");
        await page.screenshot({ path: connectionShot, fullPage: true });
        report.screenshots.push({ file: path.relative(root, connectionShot).replaceAll("\\", "/"), sha256: sha(await readFile(connectionShot)) });
        await page.locator(".ant-select-clear").click({ force: true });
        assert.equal(await page.getByPlaceholder("http://服务器:端口").inputValue(), "http://43.165.167.179:39443");
        assert.notEqual(await page.getByPlaceholder("http://服务器:端口").inputValue(), webOrigin);
        check(name + ":new-connection-does-not-inherit-static-origin");
        await page.locator('a[href="/studio/models"]').first().click();
        await page.locator("main h1").waitFor();
        await page.getByText(fixture.models[0].id, { exact: true }).first().waitFor();
        for (const model of fixture.models) assert((await page.locator("main").innerText()).includes(model.id));
        check(name + ":published-models-and-real-schema-worker");
        const shot = path.join(media, "independent-models-" + name + ".png");
        await page.screenshot({ path: shot, fullPage: true });
        report.screenshots.push({ file: path.relative(root, shot).replaceAll("\\", "/"), sha256: sha(await readFile(shot)) });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
        assert.deepEqual(errors, []); assert.deepEqual(forbidden, []);
        check(name + ":no-admin-worker-comfy-agent-requests-no-page-errors");
        await page.close(); await context.close();
    }
    const gets = report.api_requests.filter((request) => request.method === "GET");
    assert(gets.length >= 6 && gets.every((request) => request.bearer_present && !request.cookie_present && request.origin_matches_canvas));
    assert(report.api_requests.every((request) => request.path.startsWith("/v1/") && ["GET", "OPTIONS"].includes(request.method)));
    check("all-business-requests-go-directly-to-configured-api-without-cookies");
    report.passed = true;
} catch (error) { report.failure = error.stack; }
finally {
    await Promise.allSettled(pages.map((page) => page.close()));
    await Promise.allSettled(contexts.map((context) => context.close()));
    if (browser) await browser.close();
    for (const server of [web, api]) if (server) await new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); });
    for (const { name, video } of videos) if (video) {
        try { const source = await video.path(); const target = path.join(media, "independent-" + name + ".webm"); await copyFile(source, target); } catch { /* Failed video is not acceptance. */ }
    }
    report.cleanup = { pages_closed: pages.every((page) => page.isClosed()), contexts_closed: contexts.every((context) => !context.pages().length), browser_closed: !browser?.isConnected(), static_and_fixture_servers_closed: !web?.listening && !api?.listening };
}
for (const [file, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), digest);
report.static_bytes_unchanged = true;
const target = path.join(root, "docs/research/studio-implementation/independent-browser-" + label + ".json");
await writeFile(target, JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, target), cleanup: report.cleanup, failure: report.failure }));
if (!report.passed) process.exitCode = 1;
