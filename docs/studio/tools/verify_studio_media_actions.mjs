/** Native decoder errors and actual original downloads on the frozen Web UI. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url)), web = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(web, 'package.json'));
const sha = (data) => createHash('sha256').update(data).digest('hex');
const relative = (file) => path.relative(root, file).replaceAll('\\', '/');
const publicMode = process.argv.includes('--public'), baseline = process.argv.includes('--baseline');
assert(!baseline || publicMode && webEngine === 'chromium');
const buildFile = path.resolve(root, process.argv[2] || '');
assert(buildFile.startsWith(path.join(root, 'docs/research/studio-implementation') + path.sep));
const build = JSON.parse(await readFile(buildFile, 'utf8')); assert(build.passed && build.source_before === build.source_after);
const clean = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8'));
const dist = path.join(root, clean.private_logs, 'repository/web/dist');
const stamp = webEngine + '-' + new Date().toISOString().replaceAll(/[:.]/g, '-');
const temporary = path.join(root, '.local/studio149', 'media-actions-' + stamp), media = path.join(root, 'docs/media/studio-149', 'actions-' + stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
const report = { passed: false, at: new Date().toISOString(), baseline_reproduction: baseline, public_static_site: publicMode,
    scope: 'Actual production Web UI with native Windows persistent profiles, real decoder errors, real file inputs and original downloads; small synthetic files, not actual Safari, all codecs, GPU or physical touch hardware acceptance',
    build_report: relative(buildFile), checks: [], downloads: [], screenshots: [], page_errors: [], unexpected_requests: [], static_request_failures: [], test_canvas_ready_observation_timeout_ms: 60000, product_timeouts_changed: false, cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
async function verifyStatic() { if (!baseline) for (const [file, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), digest); }
await verifyStatic();
const fixtures = JSON.parse(await readFile(path.join(root, 'docs/research/studio-implementation/media-actions-fixtures-149.json'), 'utf8')).fixtures;
for (const fixture of fixtures) { fixture.data = [...await readFile(path.join(root, fixture.file))]; assert.equal(fixture.data.length, fixture.bytes); assert.equal(sha(Buffer.from(fixture.data)), fixture.sha256); fixture.key = (fixture.mime_type.startsWith('image/') ? 'image' : 'file') + ':managed-v1:actions-' + fixture.name.replaceAll('.', '-'); }
const asset = (name) => fixtures.find((item) => item.name === name);
const metadata = (name) => { const file = asset(name); return { content: 'blob:obsolete-source', storageKey: file.key, mimeType: file.mime_type, bytes: file.bytes, status: 'success', naturalWidth: file.width || file.media?.width, naturalHeight: file.height || file.media?.height, durationMs: file.duration_seconds ? file.duration_seconds * 1000 : undefined }; };
const node = (id, type, title, x, y, data, height = 160) => ({ id, type, title, position: { x, y }, width: 270, height, metadata: data });
const image = (name, id) => ({ ...metadata(name), id, naturalWidth: 16, naturalHeight: 12, status: 'success' });
const project = { id: 'media-actions-project', title: '媒体原件与播放提示', createdAt: '2026-10-31T00:00:00.000Z', updatedAt: '2026-10-31T00:00:00.000Z', viewport: { x: 0, y: 0, k: 0.85 }, backgroundMode: 'lines', showImageInfo: false, chatSessions: [], activeChatId: null, connections: [], nodes: [
    node('video-webm', 'video', 'WebM 原件', 30, 100, metadata('synthetic.webm')),
    node('video-mp4', 'video', 'MP4 原件', 335, 100, metadata('synthetic.mp4')),
    node('image-jpeg', 'image', 'JPEG 原件', 640, 100, { ...metadata('original.jpg'), mimeType: 'image/png' }),
    node('audio-wav', 'audio', 'WAV 原件', 30, 330, metadata('synthetic.wav'), 140),
    node('audio-flac', 'audio', 'FLAC 原件', 335, 330, metadata('synthetic.flac'), 140),
    node('image-batch', 'image', 'JPEG 与 WebP 备选', 640, 330, { ...metadata('original.jpg'), images: [image('original.jpg', 'primary-jpeg'), image('variant.webp', 'alternate-webp')], primaryImageId: 'primary-jpeg' }),
    node('broken-audio', 'audio', '不可解码音频', 30, 560, metadata('invalid-audio.wav'), 150),
    node('broken-video', 'video', '未知格式原件', 335, 560, { ...metadata('invalid-video.bin'), mimeType: 'video/mp4' }, 150),
] };
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-local-media"; export {rawJournalTransaction} from "./src/services/studio-journal-db"; export {blobHash} from "./src/services/api/aggregator/client";', resolveDir: web }, bundle: true, platform: 'browser', format: 'esm', define: { 'import.meta.env.VITE_STUDIO_MODE': '"managed"' }, outfile: path.join(temporary, 'probe.mjs'), logLevel: 'warning' });
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
let browser, server, context, page, video, origin; let downloadCount = 0;
async function snapshot() { return page.evaluate(async () => { const p = await import('/probe.mjs'), files = await Promise.all((await p.journalList('local_media')).map(async (file) => ({ key: file.id, bytes: file.blob.size, mimeType: file.blob.type, sha256: await p.blobHash(file.blob) }))); const taskCounts = {}; for (const store of ['intents', 'uploads', 'results', 'budgets']) taskCounts[store] = (await p.journalList(store)).length; const doc = await p.journalGet('documents', p.documentNames[0]); return { files, taskCounts, projects: JSON.parse(doc.value).state.projects }; }); }
async function shot(name) { const file = path.join(media, name + '.png'); await page.screenshot({ path: file }); report.screenshots.push({ file: relative(file), bytes: (await stat(file)).size, sha256: sha(await readFile(file)) }); }
const nodeIn = (id) => page.locator('[data-node-id="' + id + '"]');
// Use the real layer-list focus action so resized nodes/panels cannot cover a
// neighbouring target. Body clicks deliberately open generation panels.
async function toolbar(id) {
    const row = page.locator('[data-node-item]').nth(project.nodes.findIndex((node) => node.id === id));
    await row.locator(':scope > button[title]').click();
    const bar = page.locator('[data-node-toolbar="' + id + '"]'); await bar.waitFor();
    assert.equal(await page.locator('[data-node-toolbar]').count(), 1);
    assert.equal(await page.locator('[data-node-panel]').count(), 0);
    return bar;
}
async function downloadOriginal(control, name, extension) {
    report.last_download_attempt = { fixture: name, started: new Date().toISOString(), click_completed: false };
    const [saved] = await Promise.all([page.waitForEvent('download'), control.click().then(() => { report.last_download_attempt.click_completed = true; })]);
    const filename = saved.suggestedFilename(), file = path.join(temporary, 'saved-' + report.downloads.length + '-' + path.basename(filename));
    await saved.saveAs(file); const data = await readFile(file), fixture = asset(name);
    const item = { fixture: name, suggested_filename: filename, expected_extension: extension, actual_extension: path.extname(filename), bytes: data.length, sha256: sha(data) };
    report.downloads.push(item); assert.equal(data.length, fixture.bytes); assert.equal(sha(data), fixture.sha256);
    assert.equal(path.extname(filename), '.' + extension, 'Original filename must preserve its actual media format');
    return item;
}
async function replace(id, title, file) {
    const bar = await toolbar(id);
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), bar.getByRole('button', { name: title, exact: true }).click()]);
    await chooser.setFiles({ name: file.name, mimeType: file.mime_type, buffer: Buffer.from(file.data) });
    await page.waitForFunction(async ({ id, hash }) => { const p = await import('/probe.mjs'), doc = await p.journalGet('documents', p.documentNames[0]), node = JSON.parse(doc.value).state.projects[0].nodes.find((entry) => entry.id === id); const file = await p.getManagedMediaBlob(node.metadata.storageKey); return file && await p.blobHash(file) === hash; }, { id, hash: file.sha256 });
}
async function downloadFallback(id, name, extension) {
    await toolbar(id);
    return downloadOriginal(page.locator('[data-canvas-media-error="' + id + '"]').getByRole('button', { name: '下载原件', exact: true }), name, extension);
}
try {
    if (!publicMode) {
        server = createServer(async (request, response) => {
            try {
                const pathname = new URL(request.url, 'http://fixture.invalid').pathname;
                if (pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
                let target = path.resolve(dist, decodeURIComponent(pathname.replace(/^\/studio\//, '')));
                assert(pathname.startsWith('/studio/') && (target === dist || target.startsWith(dist + path.sep)));
                try { if (!(await stat(target)).isFile()) target = path.join(dist, 'index.html'); } catch { target = path.join(dist, 'index.html'); }
                response.writeHead(200, { 'Content-Type': ({ '.js': 'application/javascript', '.html': 'text/html', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2' })[path.extname(target)] || 'application/octet-stream', 'Content-Security-Policy': csp }); response.end(await readFile(target));
            } catch { response.writeHead(500); response.end(); }
        }); await new Promise((resolve) => server.listen(0, '192.168.31.95', resolve)); origin = 'http://192.168.31.95:' + server.address().port;
    } else origin = 'http://43.165.167.179:39445';
    const index = await fetch(origin + '/studio/'); assert(index.ok); assert.equal(sha(Buffer.from(await index.arrayBuffer())), build.static_artifact_sha256['index.html']);
    report.origin = origin;
    browser = await launchStudioWebBrowser({ root, temporary, report }); context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: temporary } });
    await context.route('**/*', async (route) => {
        const request = route.request(), url = new URL(request.url());
        if (['http:', 'https:'].includes(url.protocol) && (url.origin !== origin || /\/(?:v1|api|admin|internal)\//.test(url.pathname))) { report.unexpected_requests.push(request.method() + ' ' + url.origin + url.pathname); return route.abort(); }
        if (url.pathname === '/probe.mjs') return route.fulfill({ status: 200, contentType: 'application/javascript', body: await readFile(path.join(temporary, 'probe.mjs')) });
        if (url.pathname === '/blank') return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/html', 'Content-Security-Policy': csp }, body: '<!doctype html><title>Owned media actions fixture</title>' });
        return route.continue();
    });
    await context.addInitScript(() => { globalThis.__nativeMediaErrors = []; addEventListener('error', (event) => { if (event.target instanceof HTMLMediaElement) globalThis.__nativeMediaErrors.push({ node_id: event.target.closest('[data-node-id]')?.getAttribute('data-node-id'), code: event.target.error?.code || null, source: event.target.src }); }, true); });
    page = await context.newPage(); video = page.video(); page.on('pageerror', (error) => report.page_errors.push(error.message)); page.on('download', () => downloadCount++);
    page.on('requestfailed', (request) => { const url = new URL(request.url()); if (url.origin === origin) report.static_request_failures.push({ path: url.pathname, error: request.failure()?.errorText }); });
    await page.goto(origin + '/blank'); assert.equal(await page.evaluate(() => isSecureContext), false);
    await page.evaluate(async ({ project, fixtures }) => { const p = await import('/probe.mjs'); await p.initializeDocuments({ [p.documentNames[0]]: null, [p.documentNames[1]]: null }); for (const file of fixtures) await p.addManagedMedia(file.key, new Blob([new Uint8Array(file.data)], { type: file.mime_type })); const doc = await p.journalGet('documents', p.documentNames[0]); await p.saveDocument(doc.id, JSON.stringify({ version: 0, state: { projects: [project], deletedProjects: [] } }), doc.revision); }, { project, fixtures });
    await page.goto(origin + '/studio/canvas/' + project.id); await page.locator('[data-canvas-title]').waitFor({ timeout: 60000 });
    await page.waitForFunction(() => document.querySelector('[data-node-id="image-jpeg"] img')?.naturalWidth === 16);
    if (!baseline) {
        for (const id of ['broken-audio', 'broken-video']) await page.locator('[data-canvas-media-error="' + id + '"]').waitFor();
        await shot('media-originals-and-native-fallback');
    }
    const before = await snapshot(); report.originals_before = before.files;
    const webmToolbar = await toolbar('video-webm'); await downloadOriginal(webmToolbar.getByRole('button', { name: '下载视频', exact: true }), 'synthetic.webm', 'webm');
    check('actual standard video download preserves WebM filename and exact original bytes');
    assert(!baseline, 'Old application unexpectedly passes the known WebM regression; inspect baseline');
    for (const [id, name, label, extension] of [['video-mp4', 'synthetic.mp4', '下载视频', 'mp4'], ['audio-wav', 'synthetic.wav', '下载音频', 'wav'], ['audio-flac', 'synthetic.flac', '下载音频', 'flac'], ['image-jpeg', 'original.jpg', '下载图片', 'jpg']]) { const bar = await toolbar(id); await downloadOriginal(bar.getByRole('button', { name: label, exact: true }), name, extension); }
    check('standard MP4/WAV/FLAC/JPEG downloads use stored original type and SHA, including stale JPEG metadata');
    const batch = nodeIn('image-batch'); await batch.hover({ position: { x: 8, y: 8 } }); await downloadOriginal(batch.locator('button[title="下载"]').first(), 'original.jpg', 'jpg');
    await batch.getByRole('button', { name: '图片组已收起', exact: true }).click();
    const variant = batch.locator('button[title="下载"]').first(); await variant.hover(); await downloadOriginal(variant, 'variant.webp', 'webp');
    await batch.getByRole('button', { name: '图片组已展开', exact: true }).click();
    check('actual primary and expanded batch-image downloads preserve JPEG and WebP originals');
    for (const id of ['broken-audio', 'broken-video']) await page.locator('[data-canvas-media-error="' + id + '"]').waitFor();
    report.native_decoder_errors = await page.evaluate(() => globalThis.__nativeMediaErrors);
    assert(['broken-audio', 'broken-video'].every((id) => report.native_decoder_errors.some((event) => event.node_id === id && event.code && event.source.startsWith('blob:' + origin + '/'))));
    check('native invalid audio/video error events produce explicit playback fallback without task failure or generation');
    if (webEngine === 'webkit') {
        for (const id of ['video-webm', 'video-mp4', 'audio-wav', 'audio-flac']) await page.locator('[data-canvas-media-error="' + id + '"]').waitFor();
        report.valid_fixture_playback_accepted = false;
        check('Windows WebKit unsupported valid samples retain original downloads and explicit failure; decoder support unaccepted');
    } else {
        await page.waitForFunction(() => ['video-webm', 'video-mp4', 'audio-wav', 'audio-flac'].every((id) => { const media = document.querySelector('[data-node-id="' + id + '"] video,[data-node-id="' + id + '"] audio'); return media && media.readyState >= 2 && !media.error; }));
        report.valid_fixture_playback_accepted = 'loaded and decoded only; full playback/voice/background not accepted';
        check('valid native MP4/WebM/WAV/FLAC elements remain usable and have no failure overlay');
    }
    await downloadFallback('broken-audio', 'invalid-audio.wav', 'wav');
    await downloadFallback('broken-video', 'invalid-video.bin', 'bin');
    assert.equal(await page.locator('[data-node-panel]').count(), 0);
    check('fallback downloads retain undecodable bytes/WAV/bin without opening an editor; focused layer actions stay on the selected node');
    const missing = asset('invalid-audio.wav'); await page.evaluate(async (key) => { const p = await import('/probe.mjs'); await p.rawJournalTransaction(['local_media'], 'readwrite', (tx, result) => { tx.objectStore('local_media').delete(key); result(); }); }, missing.key);
    await toolbar('broken-audio');
    const count = downloadCount; await page.locator('[data-canvas-media-error="broken-audio"]').getByRole('button', { name: '下载原件', exact: true }).click();
    await page.getByText('本机原件不可读，请重新载入原件或从完整备份恢复', { exact: true }).waitFor(); assert.equal(downloadCount, count);
    await page.evaluate(async (file) => { const p = await import('/probe.mjs'); await p.addManagedMedia(file.key, new Blob([new Uint8Array(file.data)], { type: file.mime_type })); }, missing);
    await downloadFallback('broken-audio', missing.name, 'wav');
    check('missing stored original refuses download instead of falling back to cached URL; explicit restoration recovers exact bytes');
    const after = await snapshot(); assert.deepEqual(after.files, before.files); assert.deepEqual(after.taskCounts, before.taskCounts); assert(Object.values(after.taskCounts).every((count) => count === 0));
    assert(after.projects[0].nodes.every((node) => node.metadata.status === 'success'));
    check('all download/failure paths leave originals and generation task stores unchanged');
    await page.reload(); await page.locator('[data-canvas-media-error="broken-audio"]').waitFor();
    await downloadFallback('broken-audio', missing.name, 'wav');
    check('refresh recreates native error state and preserves original download without submitting work');
    await replace('broken-audio', '替换音频', asset('synthetic.wav')); await replace('broken-video', '替换视频', asset('synthetic.webm'));
    if (webEngine !== 'webkit') {
        for (const id of ['broken-audio', 'broken-video']) await page.locator('[data-canvas-media-error="' + id + '"]').waitFor({ state: 'detached' });
        await page.waitForFunction(() => ['broken-audio', 'broken-video'].every((id) => document.querySelector('[data-node-id="' + id + '"] video,[data-node-id="' + id + '"] audio')?.readyState >= 2));
    } else for (const id of ['broken-audio', 'broken-video']) await page.locator('[data-canvas-media-error="' + id + '"]').waitFor();
    check('actual replace-audio/video file choosers load new stored originals; previous source error does not poison supported playback');
    for (const [id, label, file, ext] of [['broken-audio', '下载音频', 'synthetic.wav', 'wav'], ['broken-video', '下载视频', 'synthetic.webm', 'webm']]) { const bar = await toolbar(id); await downloadOriginal(bar.getByRole('button', { name: label, exact: true }), file, ext); }
    check('replacement originals download with their new type and exact SHA'); await shot('replacement-originals');
    await page.setViewportSize({ width: 390, height: 844 });
    report.viewport_before_layout = await page.evaluate(() => ({ inner_width: innerWidth, document_scroll_width: document.documentElement.scrollWidth, body_scroll_width: document.body.scrollWidth }));
    await page.waitForFunction(() => innerWidth === 390 && document.documentElement.scrollWidth <= innerWidth + 1);
    report.viewport_after_layout = await page.evaluate(() => ({ inner_width: innerWidth, document_scroll_width: document.documentElement.scrollWidth, body_scroll_width: document.body.scrollWidth }));
    await shot('390-media-page');
    check('390px Web viewport has no horizontal document overflow; emulation only');
    const final = await snapshot(); assert(final.files.length === 10 && before.files.every((file) => final.files.some((retained) => JSON.stringify(retained) === JSON.stringify(file))));
    assert(Object.values(final.taskCounts).every((count) => count === 0)); report.originals_after = final.files;
    assert.deepEqual(report.page_errors, []); assert.deepEqual(report.unexpected_requests, []); await verifyStatic(); report.static_bytes_unchanged = true; report.production_api_requests = 0; report.generation_posts = 0; report.passed = true;
} catch (error) {
    report.error = { name: error.name, message: error.message, stack: error.stack };
    if (page && !page.isClosed()) report.failure_viewport = await page.evaluate(() => ({ inner_width: innerWidth, document_scroll_width: document.documentElement.scrollWidth, body_scroll_width: document.body.scrollWidth }));
    if (page && !page.isClosed()) { report.failure_alerts = await page.locator('[role="alert"]').allTextContents(); try { await shot('failure'); } catch {} }
} finally {
    if (browser) await browser.close(); if (server) await new Promise((resolve) => server.close(resolve));
    if (video) report.private_recording = relative(await video.path()); report.download_events = downloadCount;
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: !context || context.pages().length === 0, browser_closed: !browser || !browser.isConnected(), fixture_server_closed: !server || !server.listening };
    const file = path.join(root, 'docs/research/studio-implementation/media-actions-' + (baseline ? 'baseline-' : publicMode ? 'public-' : '') + stamp + '.json');
    await writeFile(file, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' }); console.log(JSON.stringify({ file: relative(file), passed: report.passed, checks: report.checks.length, cleanup: report.cleanup, error: report.error?.message }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean) || report.resource_close_errors?.length) process.exitCode = 1;
