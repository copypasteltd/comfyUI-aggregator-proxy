/** Delay an actual native read completion while two real editor connections leave. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser, webEngine as browserName } from './studio_web_browser.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url)), web = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(web, 'package.json'));
const buildPath = path.resolve(root, process.argv[2] || ''), publicMode = process.argv.includes('--public'), baseline = process.argv.includes('--baseline');
const evidenceVersion = process.argv.find((value) => value.startsWith('--evidence-version='))?.split('=')[1] || '146';
assert(/^\d+$/.test(evidenceVersion));
assert(['chromium', 'firefox', 'webkit'].includes(browserName));
if (browserName !== 'chromium') assert(publicMode, 'Cross-engine checks use the actual public ordinary HTTP site');
assert(buildPath.startsWith(path.join(root, 'docs/research/studio-implementation') + path.sep));
const build = JSON.parse(await readFile(buildPath, 'utf8')), clean = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8'));
assert(build.passed); if (baseline) assert(publicMode);
const dist = path.join(root, clean.private_logs, 'repository/web/dist'), stamp = browserName + '-' + new Date().toISOString().replaceAll(/[:.]/g, '-');
const privateDir = path.join(root, '.local/studio' + evidenceVersion + '/refresh-' + stamp), media = path.join(root, 'docs/media/studio-' + evidenceVersion + '/refresh-' + stamp);
await mkdir(privateDir, { recursive: true }); await mkdir(media, { recursive: true });
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/studio-journal-db";', resolveDir: web }, platform: 'browser', format: 'esm', bundle: true, outfile: path.join(privateDir, 'probe.mjs'), logLevel: 'warning' });
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
async function verifyBytes() { for (const [name, expected] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, name))), expected); }
if (!baseline) await verifyBytes();
const report = { passed: false, at: new Date().toISOString(), browser_engine: browserName, baseline_1451: baseline, public_static_site: publicMode, build_report: path.relative(root, buildPath).replaceAll('\\', '/'), scope: 'Frozen Web UI; actual native transactions and editor connections, with delayed completion of one committed readonly snapshot. Synthetic local data; no business API/GPU requests.', checks: [], screenshots: [], unexpected_requests: [], page_errors: [], cleanup: {} };
const contexts = [], pages = [], videos = []; let browser, server, origin;
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
async function pageIn(context, url) { const page = await context.newPage(); pages.push(page); videos.push(page.video()); page.on('pageerror', (error) => report.page_errors.push(error.message)); await page.goto(origin + url); return page; }
async function shot(page, name) { const file = path.join(media, name + '.png'); await page.screenshot({ path: file }); report.screenshots.push({ file: path.relative(root, file).replaceAll('\\', '/'), bytes: (await stat(file)).size, sha256: sha(await readFile(file)) }); }
try {
    server = createServer(async (request, response) => {
        try {
            const pathname = new URL(request.url, 'http://fixture.invalid').pathname;
            if (pathname === '/blank') { response.writeHead(200, { 'Content-Type': 'text/html' }); response.end('<!doctype html><title>Owned Web refresh fixture</title>'); return; }
            let target = pathname === '/probe.mjs' ? path.join(privateDir, 'probe.mjs') : path.resolve(dist, decodeURIComponent(pathname.replace(/^\/studio\//, '')));
            if (pathname !== '/probe.mjs') { assert(pathname.startsWith('/studio/') && (target === dist || target.startsWith(dist + path.sep))); try { if (!(await stat(target)).isFile()) target = path.join(dist, 'index.html'); } catch { target = path.join(dist, 'index.html'); } }
            response.writeHead(200, { 'Content-Type': ({ '.html': 'text/html', '.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff': 'font/woff' })[path.extname(target)] || 'application/octet-stream', 'Content-Security-Policy': csp }); response.end(await readFile(target));
        } catch { response.writeHead(500); response.end(); }
    }); await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    origin = publicMode ? 'http://43.165.167.179:39445' : 'http://canvas-refresh.test:' + server.address().port;
    if (publicMode) { const response = await fetch(origin + '/studio/maintenance.html'); assert(response.ok); assert.equal(sha(Buffer.from(await response.arrayBuffer())), build.static_artifact_sha256['maintenance.html']); }
    browser = await launchStudioWebBrowser({ root, temporary: privateDir, report, args: ['--host-resolver-rules=MAP canvas-refresh.test 127.0.0.1', '--no-proxy-server'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: privateDir } }); contexts.push(context);
    await context.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if (url.origin !== origin || /\/(?:v1|api|admin|internal)\//.test(url.pathname)) { report.unexpected_requests.push(route.request().method() + ' ' + url.origin + url.pathname); return route.abort(); }
        if (publicMode && url.pathname === '/probe.mjs') return route.fulfill({ status: 200, contentType: 'application/javascript', body: await readFile(path.join(privateDir, 'probe.mjs')) });
        if (publicMode && url.pathname === '/blank') return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Owned Web refresh fixture</title>' });
        return route.continue();
    });
    const page = await pageIn(context, '/blank');
    await page.evaluate(async () => {
        const p = await import('/probe.mjs');
        await p.rawJournalTransaction(['documents', 'local_media'], 'readwrite', (tx, result) => {
            for (const [id, state] of [['infinite-canvas:canvas_store', { projects: [], deletedProjects: [] }], ['infinite-canvas:asset_store', { assets: [] }]]) tx.objectStore('documents').add({ version: 1, id, revision: 1, value: JSON.stringify({ version: 0, state }), storageKeys: [], legacyValue: null });
            const blob = new Blob(['unused-Web-original']); tx.objectStore('local_media').add({ version: 1, id: 'file:managed-v1:refresh-race', kind: 'media', blob, bytes: blob.size, mimeType: blob.type, createdAt: 1 }); result();
        });
    });
    const first = await pageIn(context, '/studio/'), second = await pageIn(context, '/studio/');
    await page.waitForFunction(async () => { const p = await import('/probe.mjs'); return p.rawJournalTransaction(['editors'], 'readonly', (tx, result) => { const request = tx.objectStore('editors').count(); request.onsuccess = () => result(request.result === 2); }); });
    await page.goto(origin + '/studio/maintenance.html'); await page.getByRole('heading', { name: '仍有页面占用，暂不清理' }).waitFor();
    check('two actual Web editor connections protect the same native original');
    await page.evaluate(() => {
        window.heldCompletions = []; window.holdNextCleanup = 1;
        const native = Object.getOwnPropertyDescriptor(IDBTransaction.prototype, 'oncomplete');
        Object.defineProperty(IDBTransaction.prototype, 'oncomplete', { ...native, set(handler) {
            const hold = window.holdNextCleanup && this.mode === 'readonly' && this.objectStoreNames.length === 6 && this.objectStoreNames.contains('local_media');
            if (hold) { --window.holdNextCleanup; native.set.call(this, function (event) { window.heldCompletions.push(() => handler.call(this, event)); }); }
            else native.set.call(this, handler);
        } });
    });
    await first.close(); await page.waitForFunction(() => window.heldCompletions.length === 1);
    await second.close();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent.includes('清理全部未引用原件') && !button.disabled));
    check('newer native snapshot observes both released connections while older committed read is held');
    await page.evaluate(async () => { window.heldCompletions.shift()(); await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    const state = await page.evaluate(async () => {
        const p = await import('/probe.mjs');
        const editors = await p.rawJournalTransaction(['editors'], 'readonly', (tx, result) => { const request = tx.objectStore('editors').count(); request.onsuccess = () => result(request.result); });
        const button = [...document.querySelectorAll('button')].find((item) => item.textContent.includes('清理全部未引用原件'));
        return { editors, cleanup_enabled: !button.disabled, stale_occupancy: document.body.textContent.includes('仍有页面占用，暂不清理') };
    });
    assert.equal(state.editors, 0); assert.equal(state.cleanup_enabled, !baseline); assert.equal(state.stale_occupancy, baseline);
    report.regression_observed = baseline;
    check(baseline ? 'actual previous 145.1 UI reproduces stale occupancy after older native completion' : 'latest snapshot remains authoritative after older native completion; no stale occupancy', state);
    await shot(page, baseline ? 'baseline-145-stale-occupancy' : 'latest-snapshot-ready');
    assert.deepEqual(report.unexpected_requests, []); assert.deepEqual(report.page_errors, []);
    if (!baseline) { await verifyBytes(); report.static_bytes_unchanged = true; }
    report.actual_api_requests = 0; report.generation_posts = 0; report.passed = true;
} catch (error) { report.error = { name: error.name, message: error.message, stack: error.stack }; }
finally {
    report.resource_close_errors = [];
    for (const context of contexts) try { await context.close(); } catch (error) { report.resource_close_errors.push(error.message); }
    if (browser) try { await browser.close(); } catch (error) { report.resource_close_errors.push(error.message); }
    if (server) await new Promise((resolve) => server.close(resolve));
    report.private_recordings = []; for (const video of videos) if (video) try { report.private_recordings.push(path.relative(root, await video.path()).replaceAll('\\', '/')); } catch (error) { report.resource_close_errors.push(error.message); }
    report.cleanup = { pages_closed: pages.every((page) => page.isClosed()), contexts_closed: contexts.every((context) => !context.pages().length), browser_closed: !browser || !browser.isConnected(), server_closed: !server || !server.listening };
    const file = path.join(root, 'docs/research/studio-implementation/cleanup-refresh-' + (baseline ? 'baseline-' : publicMode ? 'public-' : '') + stamp + '.json'); await writeFile(file, JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file).replaceAll('\\', '/'), cleanup: report.cleanup, error: report.error?.message }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean)) process.exitCode = 1;
