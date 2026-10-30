/** Actual UI ZIP hand-off across owned native Web engines/origins; synthetic local originals only. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const web = path.join(root, 'infinite-canvas/web'), require = createRequire(path.join(web, 'package.json'));
const { unzipSync } = require('fflate');
const sha = (data) => createHash('sha256').update(data).digest('hex');
const relative = (file) => path.relative(root, file).replaceAll('\\', '/');
const argument = (name) => process.argv.find((item) => item.startsWith('--' + name + '='))?.slice(name.length + 3);
const stage = Number(argument('stage')), chainId = argument('chain');
assert(Number.isInteger(stage) && stage >= 0 && stage <= 3);
assert(/^[0-9a-f-]{36}$/.test(chainId));
assert.equal(webEngine, ['firefox', 'chromium', 'webkit', 'firefox'][stage]);
const bindAddress = argument('bind-address');
if (stage === 1 || stage === 2) assert(/^192\.168\.\d+\.\d+$/.test(bindAddress));
const evidence = path.join(root, 'docs/research/studio-implementation');
const buildName = 'clean-build-2026-10-30T13-52-01-248Z.json';
const build = JSON.parse(await readFile(path.join(evidence, buildName), 'utf8'));
assert(build.passed && build.source_before === build.source_after);
const install = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8'));
const dist = path.join(root, install.private_logs, 'repository/web/dist');
async function verifyStatic() { for (const [name, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, name))), digest); }
await verifyStatic();
const stamp = webEngine + '-' + new Date().toISOString().replaceAll(/[:.]/g, '-');
const temporary = path.join(root, '.local/studio148/migration-' + chainId, 'stage-' + stage + '-' + stamp);
const media = path.join(root, 'docs/media/studio-148', 'migration-' + stage + '-' + stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
const chainFile = path.join(root, '.local/studio148/migration-' + chainId, 'chain.json');
const chain = stage ? JSON.parse(await readFile(chainFile, 'utf8')) : { id: chainId, stages: [], scope: 'Synthetic local-media snapshot; not live task migration or cloud synchronization' };
assert.equal(chain.id, chainId); assert.equal(chain.stages.length, stage);
const report = { passed: false, at: new Date().toISOString(), stage, chain_id: chainId,
    scope: 'Actual frozen production Web UI ZIP download/file-input import and native IndexedDB across fresh persistent browser profiles and public/LAN ordinary HTTP origins; synthetic local originals, not GPU outputs, actual Safari, cloud synchronization or native app acceptance',
    build_report: 'docs/research/studio-implementation/' + buildName,
    static_release: 'canvas-20261030.146.1', new_product_release: false,
    checks: [], screenshots: [], page_errors: [], unexpected_requests: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
const fixtureReport = JSON.parse(await readFile(path.join(evidence, 'Web-migration-fixtures-148.json'), 'utf8'));
assert(fixtureReport.passed);
const fixtures = await Promise.all(fixtureReport.fixtures.map(async (item) => {
    const data = await readFile(path.join(root, item.file)); assert.equal(data.length, item.bytes); assert.equal(sha(data), item.sha256);
    return { ...item, data: [...data], key: (item.mime_type.startsWith('image/') ? 'image' : 'file') + ':managed-v1:migration-' + item.name.replaceAll('.', '-') };
}));
const title = '跨浏览器混合媒体作品';
const fixture = (name) => fixtures.find((item) => item.name === name);
const baseMetadata = (name) => { const item = fixture(name); return { content: 'blob:source-obsolete', storageKey: item.key, bytes: item.bytes, mimeType: item.mime_type, status: 'success', naturalWidth: item.width || item.media?.width, naturalHeight: item.height || item.media?.height, durationMs: item.duration_seconds ? item.duration_seconds * 1000 : undefined }; };
const node = (id, type, name, x, y, metadata, height = 180) => ({ id, type, title: name, position: { x, y }, width: 280, height, metadata });
const sourceProject = { id: 'migration-source', title, createdAt: '2026-10-30T00:00:00.000Z', updatedAt: '2026-10-30T00:00:00.000Z',
    viewport: { x: 0, y: 0, k: 0.85 }, backgroundMode: 'lines', showImageInfo: false, chatSessions: [], activeChatId: null,
    nodes: [
        node('original-image', 'image', '原图与重复参考', 40, 100, { ...baseMetadata('web-original.png'), generationType: 'edit', prompt: fixture('web-reference.png').key,
            references: [fixture('web-reference.png').key, fixture('web-original.png').key, fixture('web-reference.png').key],
            headers: { Cookie: 'synthetic-migration-cookie', Authorization: 'Bearer synthetic-migration-header' }, diagnosticUrl: 'https://example.invalid/original?token=synthetic-migration-query#api_key=synthetic-migration-fragment' }),
        node('video-mp4', 'video', 'MP4 H.264', 350, 100, baseMetadata('synthetic.mp4')),
        node('video-webm', 'video', 'WebM VP8', 660, 100, baseMetadata('synthetic.webm')),
        node('audio-wav', 'audio', 'WAV PCM', 40, 340, baseMetadata('synthetic.wav'), 140),
        node('audio-flac', 'audio', 'FLAC', 350, 340, baseMetadata('synthetic.flac'), 140),
        node('model-glb', 'studio:model3d', '双动画 GLB 原件', 970, 100, baseMetadata('synthetic-animated.glb'), 340),
        node('literal-text', 'text', '只读原文', 40, 540, { content: '<script>globalThis.__migrationExecuted=true</script> literal original text', status: 'success' }),
        node('old-task', 'studio:task', '历史任务说明', 350, 540, { studioIntentId: 'do-not-replay', studioJobId: 'job_' + '1'.repeat(32), studioStatus: 'succeeded' }),
    ], connections: ['video-mp4', 'video-webm', 'audio-wav', 'audio-flac', 'model-glb'].map((id) => ({ id: 'edge-' + id, fromNodeId: 'original-image', toNodeId: id })) };
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-local-media"; export {blobHash} from "./src/services/api/aggregator/client"; export {validateCanvasArchive,canvasStorageKeys} from "./src/lib/canvas/canvas-transfer";', resolveDir: web }, bundle: true, platform: 'browser', format: 'esm', define: { 'import.meta.env.VITE_STUDIO_MODE': '"managed"' }, outfile: path.join(temporary, 'probe.mjs'), logLevel: 'warning' });
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
let server, browser, context, page, video, origin;
async function snapshot() { return page.evaluate(async () => {
    const p = await import('/probe.mjs'), documents = await p.journalList('documents');
    const files = await Promise.all((await p.journalList('local_media')).map(async (file) => ({ key: file.id, mimeType: file.blob.type, bytes: file.blob.size, sha256: await p.blobHash(file.blob) })));
    const projects = JSON.parse(documents.find((doc) => doc.id.endsWith('canvas_store')).value).state.projects;
    const tasks = {}; for (const store of ['intents', 'uploads', 'results', 'budgets']) tasks[store] = (await p.journalList(store)).length;
    return { documents: documents.map(({ id, revision, storageKeys }) => ({ id, revision, storageKeys })), projects, files, tasks };
}); }
function verifySnapshot(value, previous) {
    assert.equal(value.projects.length, 1); const project = value.projects[0];
    assert.equal(project.title, title); assert.equal(project.nodes.length, 9); assert.equal(value.files.length, 8);
    assert.deepEqual(value.tasks, { intents: 0, uploads: 0, results: 0, budgets: 0 });
    assert.deepEqual(project.connections, sourceProject.connections);
    assert.deepEqual(value.files.map(({ sha256, bytes, mimeType }) => ({ sha256, bytes, mimeType })).sort((a, b) => a.sha256.localeCompare(b.sha256)), fixtures.map(({ sha256, bytes, mime_type }) => ({ sha256, bytes, mimeType: mime_type })).sort((a, b) => a.sha256.localeCompare(b.sha256)));
    const image = project.nodes.find((entry) => entry.id === 'original-image').metadata;
    assert.equal(image.prompt, fixture('web-reference.png').key); assert.equal(image.references.length, 3);
    assert.equal(image.references[0], image.references[2]); assert.equal(image.references[1], image.storageKey);
    assert.deepEqual(image.references.map((key) => value.files.find((file) => file.key === key)?.sha256), [fixture('web-reference.png').sha256, fixture('web-original.png').sha256, fixture('web-reference.png').sha256]);
    for (const source of sourceProject.nodes.filter((item) => item.metadata?.storageKey)) {
        const restored = project.nodes.find((item) => item.id === source.id); assert.equal(restored.type, source.type);
        for (const field of ['bytes', 'mimeType', 'durationMs', 'naturalWidth', 'naturalHeight']) if (source.metadata[field] !== undefined) assert.equal(restored.metadata[field], source.metadata[field]);
        assert.equal(value.files.find((file) => file.key === restored.metadata.storageKey)?.sha256, fixture(source.id === 'original-image' ? 'web-original.png' : { 'video-mp4': 'synthetic.mp4', 'video-webm': 'synthetic.webm', 'audio-wav': 'synthetic.wav', 'audio-flac': 'synthetic.flac', 'model-glb': 'synthetic-animated.glb' }[source.id]).sha256);
    }
    if (previous) {
        assert.notEqual(project.id, previous.projects[0].id);
        assert(value.files.every((file) => !previous.files.some((old) => old.key === file.key)), 'Every imported original receives a new key');
        assert.equal(project.nodes.find((item) => item.id === 'old-task').type, 'text');
        assert(project.nodes.find((item) => item.id === 'old-task').metadata.content.includes('不会重新提交'));
        assert(!JSON.stringify(project).includes('do-not-replay'));
    }
    return project;
}
async function shot(name) { const file = path.join(media, name + '.png'); await page.screenshot({ path: file }); report.screenshots.push({ file: relative(file), bytes: (await stat(file)).size, sha256: sha(await readFile(file)) }); }
async function inspectArchive(file) {
    const data = await readFile(file), entries = unzipSync(data), value = JSON.parse(Buffer.from(entries['projects.json']).toString('utf8'));
    await page.evaluate(async (archive) => (await import('/probe.mjs')).validateCanvasArchive(archive), value);
    assert.equal(value.projects.length, 1); assert.equal(value.projects[0].files.length, 8);
    assert.equal(value.projects[0].project.nodes.length, 9);
    for (const item of value.projects[0].files) { assert.equal(entries[item.path]?.length, item.bytes); assert.equal(sha(entries[item.path]), item.sha256); }
    for (const secret of ['synthetic-migration-cookie', 'synthetic-migration-header', 'synthetic-migration-query', 'synthetic-migration-fragment']) assert(!JSON.stringify(value).includes(secret));
    return { file: relative(file), bytes: data.length, sha256: sha(data), files: value.projects[0].files, project: value.projects[0].project };
}
try {
    if (stage === 1 || stage === 2) {
        server = createServer(async (request, response) => {
            try {
                const pathname = new URL(request.url, 'http://fixture.invalid').pathname;
                if (pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
                let target = path.resolve(dist, decodeURIComponent(pathname.replace(/^\/studio\//, '')));
                assert(pathname.startsWith('/studio/') && target.startsWith(dist + path.sep));
                try { if (!(await stat(target)).isFile()) target = path.join(dist, 'index.html'); } catch { target = path.join(dist, 'index.html'); }
                const contentType = ({ '.js': 'application/javascript', '.html': 'text/html', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2' })[path.extname(target)] || 'application/octet-stream';
                response.writeHead(200, { 'Content-Type': contentType, 'Content-Security-Policy': csp }); response.end(await readFile(target));
            } catch { response.writeHead(500); response.end(); }
        }); await new Promise((resolve) => server.listen(0, bindAddress, resolve));
        origin = 'http://' + bindAddress + ':' + server.address().port;
    } else origin = 'http://43.165.167.179:39445';
    report.origin = origin; report.origin_kind = server ? 'owned LAN HTTP new origin serving byte-identical frozen static UI' : 'actual public static site, fresh owned profile';
    for (const name of ['index.html', 'maintenance.html']) { const response = await fetch(origin + '/studio/' + name); assert(response.ok); assert.equal(sha(Buffer.from(await response.arrayBuffer())), build.static_artifact_sha256[name]); }
    browser = await launchStudioWebBrowser({ root, temporary, report });
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: temporary } });
    await context.route('**/*', async (route) => {
        const request = route.request(), url = new URL(request.url());
        if (['http:', 'https:'].includes(url.protocol) && (url.origin !== origin || /\/(v1|api|admin|internal)\//.test(url.pathname))) { report.unexpected_requests.push(request.method() + ' ' + url.origin + url.pathname); return route.abort(); }
        if (url.pathname === '/probe.mjs') return route.fulfill({ status: 200, contentType: 'application/javascript', body: await readFile(path.join(temporary, 'probe.mjs')) });
        if (url.pathname === '/blank') return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/html', 'Content-Security-Policy': csp }, body: '<!doctype html><title>Owned migration fixture</title>' });
        return route.continue();
    });
    page = await context.newPage(); video = page.video(); page.on('pageerror', (error) => report.page_errors.push(error.message));
    await page.goto(origin + '/blank'); assert.equal(await page.evaluate(() => isSecureContext), false);
    await page.evaluate(async ({ project, originals }) => {
        const p = await import('/probe.mjs');
        await p.initializeDocuments({ [p.documentNames[0]]: null, [p.documentNames[1]]: null });
        if (project) {
            for (const item of originals) await p.addManagedMedia(item.key, new Blob([new Uint8Array(item.data)], { type: item.mime_type }));
            const document = await p.journalGet('documents', p.documentNames[0]);
            await p.saveDocument(document.id, JSON.stringify({ version: 0, state: { projects: [project], deletedProjects: [] } }), document.revision);
        }
    }, { project: stage === 0 ? sourceProject : null, originals: fixtures.filter((item) => item.name !== 'actual-file-input.png') });
    if (stage === 0) {
        await page.goto(origin + '/studio/canvas/' + sourceProject.id); await page.locator('[data-canvas-title]').waitFor();
        await page.locator('input[type="file"][accept*="audio/mpeg"]').setInputFiles(path.join(root, fixture('actual-file-input.png').file));
        await page.waitForFunction(async () => { const p = await import('/probe.mjs'), doc = await p.journalGet('documents', p.documentNames[0]); return JSON.parse(doc.value).state.projects[0].nodes.length === 9 && (await p.journalList('local_media')).length === 8; });
        await page.keyboard.press('Escape'); check('source native document plus actual image file-input persists nine nodes and eight mixed originals');
    } else {
        const empty = await snapshot(); assert.equal(empty.projects.length, 0); assert.equal(empty.files.length, 0);
        check('recipient is a fresh browser profile and origin with no project, task or original copied through its database');
        await page.goto(origin + '/studio/canvas'); await page.getByRole('heading', { name: '还没有画布', exact: true }).waitFor();
        await page.evaluate(() => {
            globalThis.__migrationMessages = [];
            new MutationObserver(() => { for (const element of document.querySelectorAll('.ant-message-notice-content')) if (element.textContent && !globalThis.__migrationMessages.includes(element.textContent)) globalThis.__migrationMessages.push(element.textContent); }).observe(document.body, { childList: true, subtree: true });
        });
        const previous = chain.stages[stage - 1], input = path.join(root, previous.export.file);
        report.input_archive = await inspectArchive(input); assert.equal(report.input_archive.sha256, previous.export.sha256);
        await page.locator('input[type="file"][accept*="zip"]').setInputFiles(input);
        await page.getByRole('heading', { name: title, exact: true }).waitFor();
        await page.waitForFunction(() => document.querySelector('input[type="file"][accept*="zip"]')?.value === '');
        assert.equal(sha(await readFile(input)), previous.export.sha256);
        check('actual recipient ZIP file-input imports the preceding browser downloaded archive without altering it');
    }
    const state = await snapshot(), project = verifySnapshot(state, stage ? chain.stages[stage - 1].snapshot : null);
    report.snapshot = state; check('all eight original lengths, MIME types and SHA256 plus nodes, edges and media metadata survive');
    check('reference-only image, reference order and duplicate references remain protected; prompt stays literal');
    if (stage) check('new project ID and eight fresh storage keys; historical task becomes inert text with no replay');
    await page.goto(origin + '/studio/canvas/' + project.id);
    await page.locator('[data-canvas-title]').waitFor();
    await page.waitForFunction(() => document.querySelector('[data-node-id="original-image"] img')?.naturalWidth === 1);
    assert.equal(await page.evaluate(() => Boolean(globalThis.__migrationExecuted)), false);
    report.playback_observations = await page.evaluate(async () => Promise.all([...document.querySelectorAll('audio,video')].map(async (element) => {
        const base = { node_id: element.closest('[data-node-id]')?.getAttribute('data-node-id'), type: element.tagName.toLowerCase(), source_is_local_blob: element.currentSrc.startsWith('blob:') || element.src.startsWith('blob:') };
        const state = () => ({ ...base, ready_state: element.readyState, duration: Number.isFinite(element.duration) ? element.duration : null, error_code: element.error?.code || null, error_message: element.error?.message || null });
        if (!element.readyState && !element.error) await new Promise((resolve) => { const end = () => { clearTimeout(timer); element.removeEventListener('loadeddata', end); element.removeEventListener('error', end); resolve(); }; const timer = setTimeout(end, 5000); element.addEventListener('loadeddata', end); element.addEventListener('error', end); });
        return state();
    })));
    assert.equal(report.playback_observations.length, 4);
    report.playback_observation_scope = 'Native decoder observations only, test bounded to 5 seconds per media readiness; unsupported codecs remain separate acceptance work and do not change byte-identical backup checks or product timeouts';
    await shot('mixed-canvas-desktop');
    const pending = page.waitForEvent('download'); await page.getByRole('button', { name: '保存 GLB 原件', exact: true }).click();
    const download = await pending, saved = path.join(temporary, 'downloaded-original.glb'); await download.saveAs(saved);
    assert.equal(sha(await readFile(saved)), fixture('synthetic-animated.glb').sha256);
    check('imported built-in 3D node remains a GLB card and actual original download preserves all bytes');
    await page.reload(); await page.locator('[data-canvas-title]').waitFor();
    await page.waitForFunction(() => document.querySelector('[data-node-id="original-image"] img')?.naturalWidth === 1);
    const refreshed = await snapshot(); verifySnapshot(refreshed, stage ? chain.stages[stage - 1].snapshot : null);
    assert.deepEqual(refreshed.files, state.files); assert.equal(refreshed.projects[0].id, project.id);
    assert.equal(await page.evaluate(() => Boolean(globalThis.__migrationExecuted)), false);
    check('native browser refresh recovers all originals and references; script-shaped text remains inert and task stores empty');
    await page.setViewportSize({ width: 390, height: 844 }); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await shot('mixed-canvas-390'); check('390px Web viewport remains within width; responsive emulation only');
    await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(origin + '/studio/canvas');
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    const exporting = page.waitForEvent('download'); await page.locator('article').filter({ has: page.getByRole('heading', { name: title, exact: true }) }).getByRole('button', { name: '导出', exact: true }).click();
    const exported = await exporting, output = path.join(media, 'actual-Web-export.zip'); await exported.saveAs(output);
    report.export = await inspectArchive(output);
    check('actual project export contains all eight SHA-verified originals and strips structured Cookie/header/URL credentials');
    await shot('actual-project-export');
    assert.deepEqual(report.unexpected_requests, []); assert.deepEqual(report.page_errors, []); await verifyStatic();
    report.static_bytes_unchanged = true; report.production_api_requests = 0; report.generation_posts = 0; report.passed = true;
} catch (error) {
    report.error = { name: error.name, message: error.message, stack: error.stack };
    if (page && !page.isClosed()) { report.failure_alerts = await page.locator('[role="alert"]').allTextContents(); report.messages = await page.evaluate(() => globalThis.__migrationMessages || []); try { report.failure_snapshot = await snapshot(); await shot('failure'); } catch {} }
} finally {
    if (browser) await browser.close(); if (server) await new Promise((resolve) => server.close(resolve));
    if (video) report.private_recording = relative(await video.path());
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: !context || context.pages().length === 0, browser_closed: !browser || !browser.isConnected(), fixture_server_closed: !server || !server.listening };
    const file = path.join(evidence, 'Web-migration-' + stage + '-' + stamp + '.json');
    await writeFile(file, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
    if (report.passed && Object.values(report.cleanup).every(Boolean) && !report.resource_close_errors?.length) {
        chain.stages.push({ report: relative(file), browser: webEngine, origin, export: report.export, snapshot: report.snapshot });
        await writeFile(chainFile, JSON.stringify(chain, null, 2) + '\n');
    }
    console.log(JSON.stringify({ file: relative(file), chain: relative(chainFile), passed: report.passed, checks: report.checks.length, cleanup: report.cleanup, error: report.error?.message }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean) || report.resource_close_errors?.length) process.exitCode = 1;
