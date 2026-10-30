/** Frozen multi-entry static UI, native IndexedDB and actual local file input/download. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser, webEngine, webEngineArgument } from './studio_web_browser.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url)), web = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(web, 'package.json'));
const buildPath = path.resolve(root, process.argv[2] || ''), publicMode = process.argv.includes('--public');
const evidenceVersion = process.argv.find((value) => value.startsWith('--evidence-version='))?.split('=')[1] || '145';
assert(/^\d+$/.test(evidenceVersion));
assert(process.argv.slice(3).every((value) => value === '--public' || value === '--evidence-version=' + evidenceVersion || webEngineArgument(value)));
if (webEngine !== 'chromium') assert(publicMode, 'Cross-engine acceptance uses the real public HTTP site');
assert(buildPath.startsWith(path.join(root, 'docs/research/studio-implementation') + path.sep));
const build = JSON.parse(await readFile(buildPath, 'utf8')); assert(build.passed && build.source_before === build.source_after);
const clean = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8')), dist = path.join(root, clean.private_logs, 'repository/web/dist');
const sha = (value) => createHash('sha256').update(value).digest('hex');
async function verifyBytes() { for (const [name, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, name))), digest); }
await verifyBytes();
const stamp = webEngine + '-' + new Date().toISOString().replaceAll(/[:.]/g, '-');
const temporary = path.join(root, '.local/studio' + evidenceVersion + '/cleanup-' + stamp), media = path.join(root, 'docs/media/studio-' + evidenceVersion + '/cleanup-' + stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/studio-journal-db"; export * from "./src/services/studio-local-media-cleanup"; export {blobHash} from "./src/services/api/aggregator/client";', resolveDir: web }, bundle: true, platform: 'browser', format: 'esm', outfile: path.join(temporary, 'probe.mjs'), logLevel: 'warning' });
// Valid RGB PNG, generated and independently decoded with Pillow (old fixture had bad IDAT CRC).
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGOYd8caAAPRAbZY7+KjAAAAAElFTkSuQmCC', 'base64');
const wav = Buffer.alloc(44 + 8000 * 2); wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
const orphanA = 'image:managed-v1:orphan-A', orphanB = 'file:managed-v1:orphan-B';
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const report = { passed: false, at: new Date().toISOString(), scope: 'Frozen static production Web UI on ordinary HTTP; native browser upload/download, local originals/refusal/cleanup, 390px viewport emulation; synthetic local data, not real disk exhaustion, physical touch hardware or GPU acceptance', public_static_site: publicMode, build_report: path.relative(root, buildPath).replaceAll('\\', '/'), checks: [], screenshots: [], unexpected_requests: [], page_errors: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
let browser, server, origin; const contexts = [], pages = [], videos = [];
async function pageIn(context) { const page = await context.newPage(); pages.push(page); videos.push(page.video()); page.on('pageerror', (error) => report.page_errors.push(error.message)); return page; }
async function contextIn() {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: temporary } }); contexts.push(context);
    await context.route('**/*', async (route) => {
        const request = route.request(), url = new URL(request.url());
        if (['http:', 'https:'].includes(url.protocol) && (url.origin !== origin || /\/(?:v1|api|admin|internal)\//.test(url.pathname))) { report.unexpected_requests.push(request.method() + ' ' + url.origin + url.pathname); return route.abort(); }
        if (publicMode && url.pathname === '/probe.mjs') return route.fulfill({ status: 200, contentType: 'application/javascript', body: await readFile(path.join(temporary, 'probe.mjs')) });
        if (publicMode && url.pathname === '/blank') return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/html', 'Content-Security-Policy': csp }, body: '<!doctype html><title>Owned cleanup fixture</title>' });
        return route.continue();
    }); return context;
}
async function shot(page, name, reset = true) {
    if (reset) await page.evaluate(() => { document.scrollingElement?.scrollTo(0, 0); document.body.scrollTo(0, 0); document.querySelector('.studio-content')?.scrollTo(0, 0); });
    const file = path.join(media, (publicMode ? 'public-' : '') + name + '.png'); await page.screenshot({ path: file }); report.screenshots.push({ file: path.relative(root, file).replaceAll('\\', '/'), bytes: (await stat(file)).size, sha256: sha(await readFile(file)), viewport_only: true });
}
async function seed(page) {
    await page.goto(origin + '/blank');
    await page.evaluate(async (bytes) => {
        const request = indexedDB.open('comfy-aggregator-studio:journal', 4);
        request.onupgradeneeded = () => { const intents = request.result.createObjectStore('intents', { keyPath: 'id' }); intents.createIndex('active_node', 'activeNodeKey', { unique: true }); for (const store of ['documents', 'uploads', 'results', 'budgets']) request.result.createObjectStore(store, { keyPath: 'id' }); };
        const db = await new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        const tx = db.transaction('documents', 'readwrite');
        const project = { id: 'cleanup-project', title: '安全清理测试画布', nodes: [], connections: [], chatSessions: [], activeChatId: null, viewport: { x: 0, y: 0, k: 1 }, backgroundMode: 'dots', showImageInfo: false, createdAt: '2026-10-30T00:00:00.000Z', updatedAt: '2026-10-30T00:00:00.000Z' };
        ['infinite-canvas:canvas_store', 'infinite-canvas:asset_store'].forEach((id, index) => tx.objectStore('documents').add({ version: 1, id, revision: 1, value: JSON.stringify({ version: 0, state: index ? { assets: [] } : { projects: [project], deletedProjects: [] } }), storageKeys: [], legacyValue: null }));
        await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); }); db.close();
        const legacy = indexedDB.open('infinite-canvas', 1);
        legacy.onupgradeneeded = () => { for (const name of ['image_files', 'media_files', 'image_previews', 'image_generation_logs', 'video_generation_logs', 'app_state']) legacy.result.createObjectStore(name); };
        const old = await new Promise((resolve, reject) => { legacy.onsuccess = () => resolve(legacy.result); legacy.onerror = () => reject(legacy.error); });
        const oldTx = old.transaction('image_files', 'readwrite'); oldTx.objectStore('image_files').add(new Blob([new Uint8Array(bytes)], { type: 'image/png' }), 'image:legacy-preserved'); await new Promise((resolve, reject) => { oldTx.oncomplete = resolve; oldTx.onabort = () => reject(oldTx.error); }); old.close();
    }, [...png]);
    await page.goto(origin + '/studio/canvas/cleanup-project'); await page.locator('[data-canvas-title]').waitFor();
}
async function snapshot(page) { return page.evaluate(async () => { const p = await import('/probe.mjs'); return p.readCleanupSnapshot(); }); }
const article = (page, key) => page.locator('article').filter({ has: page.getByText(key, { exact: true }) });
async function confirm(page, name = '清理原件') { await page.locator('.ant-popconfirm:visible .ant-popconfirm-buttons').getByRole('button', { name, exact: true }).click(); await page.locator('.ant-popconfirm:visible').waitFor({ state: 'hidden' }); }
try {
    server = createServer(async (request, response) => {
        try {
            const pathname = new URL(request.url, 'http://fixture.invalid').pathname;
            if (pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
            if (pathname === '/blank') { response.writeHead(200, { 'Content-Type': 'text/html' }); response.end('<!doctype html><title>Owned cleanup fixture</title>'); return; }
            let target = pathname === '/probe.mjs' ? path.join(temporary, 'probe.mjs') : path.resolve(dist, decodeURIComponent(pathname.replace(/^\/studio\//, '')));
            if (pathname !== '/probe.mjs') { assert(pathname.startsWith('/studio/') && (target === dist || target.startsWith(dist + path.sep))); try { if (!(await stat(target)).isFile()) target = path.join(dist, 'index.html'); } catch { target = path.join(dist, 'index.html'); } }
            response.writeHead(200, { 'Content-Type': ({ '.js': 'application/javascript', '.mjs': 'application/javascript', '.html': 'text/html', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff': 'font/woff' })[path.extname(target)] || 'application/octet-stream', 'Content-Security-Policy': path.basename(target).startsWith('schema-worker-') ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); response.end(await readFile(target));
        } catch { response.writeHead(500); response.end(); }
    }); await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    origin = publicMode ? 'http://43.165.167.179:39445' : 'http://canvas-cleanup.test:' + server.address().port;
    if (publicMode) for (const entry of ['index.html', 'maintenance.html']) { const response = await fetch(origin + '/studio/' + entry); assert(response.ok); assert.equal(sha(Buffer.from(await response.arrayBuffer())), build.static_artifact_sha256[entry]); }
    report.origin = origin;
    browser = await launchStudioWebBrowser({ root, temporary, report, args: ['--host-resolver-rules=MAP canvas-cleanup.test 127.0.0.1', '--no-proxy-server'] });
    const context = await contextIn(), page = await pageIn(context); await seed(page); assert.equal(await page.evaluate(() => isSecureContext), false);
    await page.locator('input[type="file"][accept*="audio/mpeg"]').setInputFiles({ name: 'synthetic-local.png', mimeType: 'image/png', buffer: png });
    await page.waitForFunction(async () => { const p = await import('/probe.mjs'); const value = await p.readCleanupSnapshot(); return value.files.length === 1 && value.files[0].references.length > 0; });
    await page.keyboard.press('Escape');
    await page.locator('input[type="file"][accept*="audio/mpeg"]').setInputFiles({ name: 'synthetic-local.wav', mimeType: 'audio/wav', buffer: wav });
    await page.waitForFunction(async () => { const p = await import('/probe.mjs'); const value = await p.readCleanupSnapshot(); return value.files.length === 2 && value.files.every((file) => file.references.length > 0); });
    assert((await snapshot(page)).files.every((file) => /^(image|file):managed-v1:/.test(file.id)));
    check('actual canvas image and audio file inputs persist new controlled originals with committed document references');
    await page.keyboard.press('Escape');
    await page.evaluate(async ({ png, first, second }) => { const p = await import('/probe.mjs'); await p.rawJournalTransaction(['local_media'], 'readwrite', (tx, result) => { for (const [id, kind, blob] of [[first, 'image', new Blob([new Uint8Array(png)], { type: 'image/png' })], [second, 'media', new Blob(['candidate-reference'])]]) tx.objectStore('local_media').add({ version: 1, id, kind, blob, createdAt: 1, bytes: blob.size, mimeType: blob.type }); result(); }); }, { png: [...png], first: orphanA, second: orphanB });
    const other = await pageIn(context); await other.goto(origin + '/studio/canvas'); await other.getByRole('heading', { name: '安全清理测试画布', exact: true }).waitFor();
    await page.getByRole('link', { name: '连接服务', exact: true }).click(); await page.getByRole('link', { name: '查看本机存储空间', exact: true }).click();
    await page.getByRole('button', { name: '保存并进入安全清理', exact: true }).click();
    await page.getByRole('heading', { name: '本机安全清理', exact: true }).waitFor();
    assert(new URL(page.url()).pathname.endsWith('/studio/maintenance.html'));
    await page.getByRole('heading', { name: '仍有页面占用，暂不清理', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '清理全部未引用原件', exact: true }).isDisabled(), true);
    check('real save-and-leave navigates to separate static entry; another live canvas disables deletion');
    await shot(page, 'desktop-other-editor-protected');
    await other.close(); await page.getByRole('button', { name: '清理全部未引用原件', exact: true }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent.includes('清理全部未引用原件') && !button.disabled));
    check('closing actual second editor automatically releases native marker and enables eligible cleanup');
    await shot(page, 'desktop-ready-to-clean');
    const download = page.waitForEvent('download'); await article(page, orphanA).getByRole('button', { name: '下载原件备份', exact: true }).click();
    const saved = await download, backup = path.join(temporary, 'download-original.png'); await saved.saveAs(backup); assert.equal(sha(await readFile(backup)), sha(png));
    check('actual original download preserves full byte length and SHA', { bytes: png.length, sha256: sha(png) });
    await article(page, orphanA).getByRole('button', { name: '清理原件', exact: true }).click();
    await page.locator('.ant-popconfirm:visible .ant-popconfirm-buttons').getByRole('button', { name: /^保\s*留$/ }).click();
    await page.locator('.ant-popconfirm:visible').waitFor({ state: 'hidden' });
    assert((await snapshot(page)).files.some((file) => file.id === orphanA)); check('cancelling deletion confirmation preserves original');
    await page.evaluate(async (id) => { const p = await import('/probe.mjs'); await p.rawJournalTransaction(['documents'], 'readwrite', (tx, result) => { const store = tx.objectStore('documents'), request = store.get('infinite-canvas:asset_store'); request.onsuccess = () => { const entry = request.result; entry.value = JSON.stringify({ version: 0, state: { assets: [{ id: 'new-reference', data: { storageKey: id } }] } }); entry.storageKeys = [id]; entry.revision++; store.put(entry); result(); }; }); }, orphanB);
    await article(page, orphanB).getByRole('button', { name: '清理原件', exact: true }).click(); await confirm(page);
    await page.getByText('原件仍被画布、回收站或任务引用，本次未删除任何文件', { exact: true }).waitFor();
    assert((await snapshot(page)).files.some((file) => file.id === orphanB)); check('actual delete rechecks latest references and refuses stale UI candidate');
    await page.getByRole('button', { name: '重新检查', exact: true }).click();
    await article(page, orphanB).getByText('保留 · 我的素材', { exact: true }).waitFor();
    await article(page, orphanA).getByRole('button', { name: '清理原件', exact: true }).click(); await confirm(page);
    await page.getByText(/已清理 1 份本机原件/).waitFor();
    const after = await snapshot(page); assert.equal(after.files.length, 3); assert(after.files.every((file) => file.references.length));
    const legacyHash = await page.evaluate(async () => { const p = await import('/probe.mjs'), request = indexedDB.open('infinite-canvas'); const db = await new Promise((resolve) => { request.onsuccess = () => resolve(request.result); }); try { const tx = db.transaction('image_files', 'readonly'), file = tx.objectStore('image_files').get('image:legacy-preserved'); const blob = await new Promise((resolve) => { file.onsuccess = () => resolve(file.result); }); return p.blobHash(blob); } finally { db.close(); } });
    assert.equal(legacyHash, sha(png)); check('confirmed cleanup removes only selected controlled file; referenced files and old raw original stay intact');
    await shot(page, 'desktop-cleaned-and-retained');
    await page.setViewportSize({ width: 390, height: 844 }); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await shot(page, '390-cleaned-and-retained');
    await page.getByRole('heading', { name: '本机安全清理', exact: true }).scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: '下载原件备份', exact: true }).last().scrollIntoViewIfNeeded();
    assert(await page.getByRole('button', { name: '下载原件备份', exact: true }).last().isVisible());
    assert(await page.evaluate(() => document.body.scrollTop > 0 || (document.scrollingElement?.scrollTop || 0) > 0));
    await shot(page, '390-bottom-original-controls', false);
    await page.getByRole('link', { name: '返回画布', exact: true }).scrollIntoViewIfNeeded();
    check('390px maintenance layout fits viewport and native scrolling reaches bottom controls and returns to header; emulation only');
    await page.getByRole('link', { name: '返回画布', exact: true }).click(); await page.getByRole('heading', { name: '给创作留出空间', exact: true }).waitFor();
    check('returning from maintenance creates a fresh usable editor page');
    const badContext = await contextIn(), unsaved = await pageIn(badContext); await seed(unsaved);
    await unsaved.evaluate(() => { const put = IDBObjectStore.prototype.put; IDBObjectStore.prototype.put = function (...args) { if (this.name === 'documents') throw new DOMException('Owned quota injection', 'QuotaExceededError'); return put.apply(this, args); }; });
    await unsaved.locator('[data-canvas-title] button').dblclick(); await unsaved.locator('[data-canvas-title] input').fill('未保存的画布标题'); await unsaved.locator('[data-canvas-title] input').press('Enter');
    await unsaved.getByText('本机编辑尚未保存', { exact: true }).waitFor();
    await unsaved.getByRole('link', { name: '连接服务', exact: true }).click(); await unsaved.getByRole('link', { name: '查看本机存储空间', exact: true }).click();
    await unsaved.getByRole('button', { name: '保存并进入安全清理', exact: true }).click();
    await unsaved.getByText('操作未完成', { exact: true }).waitFor(); assert.equal(new URL(unsaved.url()).pathname, '/studio/storage');
    check('real document quota failure prevents leaving editor for maintenance; unsaved banner remains');
    await shot(unsaved, 'desktop-save-failure-retained');
    assert.deepEqual(report.unexpected_requests, []); assert.deepEqual(report.page_errors, []);
    report.actual_api_requests = 0; report.generation_posts = 0; report.static_bytes_unchanged = true; await verifyBytes(); report.passed = true;
} catch (error) {
    report.error = { name: error.name, message: error.message, stack: error.stack };
    report.failure_pages = [];
    for (const page of pages.filter((page) => !page.isClosed())) report.failure_pages.push({ path: new URL(page.url()).pathname, alerts: await page.locator('[role="alert"]').allTextContents() });
}
finally {
    for (const context of contexts) await context.close(); if (browser) await browser.close(); if (server) await new Promise((resolve) => server.close(resolve));
    report.private_recordings = []; for (const video of videos) if (video) report.private_recordings.push(path.relative(root, await video.path()).replaceAll('\\', '/'));
    report.cleanup = { pages_closed: pages.every((page) => page.isClosed()), contexts_closed: contexts.every((context) => !context.pages().length), browser_closed: !browser || !browser.isConnected(), fixture_server_closed: !server || !server.listening };
    const file = path.join(root, 'docs/research/studio-implementation/managed-cleanup-ui' + (publicMode ? '-public' : '') + '-' + stamp + '.json'); await writeFile(file, JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({ file: path.relative(root, file).replaceAll('\\', '/'), passed: report.passed, checks: report.checks.length, cleanup: report.cleanup, error: report.error?.message }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean)) process.exitCode = 1;
