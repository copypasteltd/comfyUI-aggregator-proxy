/** Actual static UI and native IndexedDB, synthetic archives; no API, GPU or physical-phone claim. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { launchStudioWebBrowser, webEngine, webEngineArgument } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const web = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(web, 'package.json'));
const { zipSync, unzipSync } = require('fflate');
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const buildPath = path.resolve(root, process.argv[2] || '');
const publicMode = process.argv.includes('--public');
const suite = process.argv.find((value) => value.startsWith('--suite='))?.slice(8) || '141';
assert(['141', '142', '145', '146', '147'].includes(suite));
assert(process.argv.slice(3).every((value) => value === '--public' || value === '--suite=' + suite || webEngineArgument(value)));
if (webEngine !== 'chromium') assert(publicMode, 'Cross-engine acceptance uses the real public HTTP site');
assert(buildPath.startsWith(path.join(root, 'docs/research/studio-implementation') + path.sep));
const build = JSON.parse(await readFile(buildPath, 'utf8'));
assert(build.passed && build.source_before === build.source_after);
const clean = build.clean_install_reused ? JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8')) : build;
const dist = path.join(root, clean.private_logs, 'repository/web/dist');
const label = webEngine + '-' + new Date().toISOString().replaceAll(/[:.]/g, '-');
const temporary = path.join(root, '.local/studio' + suite, 'archive-' + label);
const media = path.join(root, 'docs/media/studio-' + suite, 'archive-' + label);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
for (const [file, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), digest);
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-persistence"; export {validateCanvasArchive} from "./src/lib/canvas/canvas-transfer"; export {blobHash} from "./src/services/api/aggregator/client"; import {getManagedMediaBlob} from "./src/services/studio-local-media"; import localforage from "localforage"; export const images=localforage.createInstance({name:"infinite-canvas",storeName:"image_files"}); export const files=localforage.createInstance({name:"infinite-canvas",storeName:"media_files"}); export const originalBlob=(key)=>key.includes(":managed-v1:")?getManagedMediaBlob(key):images.getItem(key);', resolveDir: web }, bundle: true, platform: 'browser', format: 'esm', define: { 'import.meta.env.VITE_STUDIO_MODE': '"managed"' }, outfile: path.join(temporary, 'probe.mjs'), logLevel: 'warning' });
const report = { passed: false, at: new Date().toISOString(), scope: 'Frozen production Web UI on native Windows browser/plain non-localhost HTTP; synthetic ZIPs and injected native transaction faults, not actual disk exhaustion, API/GPU or physical touch hardware verification', build_report: path.relative(root, buildPath).replaceAll('\\', '/'), checks: [], rejected_archives: [], screenshots: [], page_errors: [], unexpected_requests: [], cleanup: {} };
report.public_static_site = publicMode;
report.suite = suite;
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
// Valid RGB fixtures, generated and independently decoded with Pillow.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGOYd8caAAPRAbZY7+KjAAAAAElFTkSuQmCC', 'base64');
const referencePng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNIWPAAAANEAeGt4NcNAAAAAElFTkSuQmCC', 'base64');
const fakeSecrets = ['synthetic-archive-cookie', 'synthetic-archive-header', 'synthetic-archive-fragment'];
const project = (id, title) => ({ id, title, createdAt: '2026-10-30T00:00:00.000Z', updatedAt: '2026-10-30T00:00:00.000Z', viewport: { x: 0, y: 0, k: 1 }, backgroundMode: 'lines', showImageInfo: false, nodes: [{ id: id + '-image', type: 'image', title: '本机参考', position: { x: 60, y: 120 }, width: 220, height: 220, metadata: { storageKey: 'image:' + id, content: 'blob:old', naturalWidth: 1, naturalHeight: 1, bytes: png.length, mimeType: 'image/png', status: 'success' } }], connections: [], chatSessions: [], activeChatId: null });
const original = project('preserved', '保留的原画布');
Object.assign(original.nodes[0].metadata, { headers: { Cookie: fakeSecrets[0], 'X-Api-Key': fakeSecrets[1] }, diagnosticUrl: 'https://example.invalid/art#api_key=' + fakeSecrets[2] + '&view=full' });
const first = project('source-a', '整包画布 A'), second = project('source-b', '整包画布 B');
second.nodes[0].metadata.images = [{ id: 'variant-b', content: 'blob:old-variant', storageKey: 'image:source-b', naturalWidth: 1, naturalHeight: 1, bytes: png.length, mimeType: 'image/png', status: 'success' }];
second.nodes[0].metadata.primaryImageId = 'variant-b';
delete second.nodes[0].metadata.storageKey;
first.nodes.push({ id: 'raw-text', type: 'text', title: '原文', position: { x: 320, y: 120 }, width: 250, height: 160, metadata: { content: '<script>globalThis.__archiveExecuted=true</script> literal text', status: 'success' } });
first.nodes.push({ id: 'old-task', type: 'studio:task', title: '旧任务', position: { x: 60, y: 400 }, width: 220, height: 180, metadata: { studioIntentId: 'do-not-replay', studioJobId: 'job_' + '1'.repeat(32), script: 'globalThis.__archiveExecuted=true' } });
const archived = { app: 'infinite-canvas', version: 3, exportedAt: '2026-10-30T00:00:00.000Z', studio: { format_version: 1, semantics: 'local-media-snapshot' }, projects: [first, second].map((value) => ({ project: value, files: [{ storageKey: 'image:' + value.id, path: 'projects/' + value.id + '/original.png', mimeType: 'image/png', bytes: png.length, sha256: sha(png) }] })) };
if (suite !== '141') {
    Object.assign(original.nodes[0].metadata, { generationType: 'edit', prompt: 'image:history-only', references: ['image:history-only', 'image:preserved', 'image:history-only'] });
    Object.assign(first.nodes[0].metadata, { generationType: 'edit', prompt: 'image:reference-only', references: ['image:reference-only', 'image:source-a', 'image:reference-only'] });
    archived.projects[0].files.push({ storageKey: 'image:reference-only', path: 'projects/source-a/reference.png', mimeType: 'image/png', bytes: referencePng.length, sha256: sha(referencePng) });
}
function archiveBuffer(value, alter = {}) { return Buffer.from(zipSync({ 'projects.json': Buffer.from(JSON.stringify(value)), ...Object.fromEntries(archived.projects.flatMap((item) => item.files.map((file) => [file.path, file.storageKey === 'image:reference-only' ? referencePng : png]))), ...alter }, { level: 0 })); }
async function exportedProject(page, name, filename) {
    const pending = page.waitForEvent('download');
    await page.locator('article').filter({ has: page.getByRole('heading', { name, exact: true }) }).getByRole('button', { name: '导出', exact: true }).click();
    const download = await pending, file = path.join(temporary, filename);
    await download.saveAs(file);
    const entries = unzipSync(await readFile(file)), value = JSON.parse(Buffer.from(entries['projects.json']).toString('utf8'));
    await page.evaluate(async (data) => { (await import('/probe.mjs')).validateCanvasArchive(data); }, value);
    for (const item of value.projects) for (const asset of item.files) { assert.equal(entries[asset.path]?.length, asset.bytes); assert.equal(sha(entries[asset.path]), asset.sha256); }
    assert(fakeSecrets.every((secret) => !JSON.stringify(value).includes(secret)));
    return { entries, value };
}
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
let server, browser, active;
const pages = [], contexts = [], videos = [];
async function snapshot(page) { return page.evaluate(async () => { const p = await import('/probe.mjs'); const docs = await p.journalList('documents'); const originals = {}; await p.images.iterate((value, key) => { originals[key] = { size: value.size, type: value.type }; }); for(const file of await p.journalList('local_media')) originals[file.id]={size:file.blob.size,type:file.blob.type}; return { documents: docs, originals, intents: await p.journalList('intents'), results: await p.journalList('results'), uploads: await p.journalList('uploads'), budgets: await p.journalList('budgets') }; }); }
async function shot(page, name) { const file = path.join(media, (publicMode ? 'public-' : '') + name + '.png'); await page.screenshot({ path: file, fullPage: true }); report.screenshots.push({ file: path.relative(root, file).replaceAll('\\', '/'), sha256: sha(await readFile(file)), first_page_elapsed_seconds: (Date.now() - report.first_page_started_at) / 1000 }); }
async function submit(page, buffer) { await page.locator('input[type="file"]').setInputFiles({ name: 'synthetic-projects.zip', mimeType: 'application/zip', buffer }); await page.waitForFunction(() => document.querySelector('input[type="file"]')?.value === ''); }
async function seed(origin, mobile = false) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: temporary } }); contexts.push(context);
    await context.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) { report.unexpected_requests.push(url.origin + url.pathname); return route.abort('blockedbyclient'); }
        if (publicMode && url.pathname === '/probe.mjs') return route.fulfill({ status: 200, contentType: 'application/javascript', body: await readFile(path.join(temporary, 'probe.mjs')) });
        if (publicMode && url.pathname === '/blank') return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/html', 'Content-Security-Policy': csp }, body: '<!doctype html><title>Owned archive fixture</title>' });
        return route.continue();
    });
    const page = await context.newPage(); pages.push(page); videos.push(page.video()); active = page;
    report.first_page_started_at ||= Date.now();
    page.on('pageerror', (error) => report.page_errors.push(error.message));
    await page.goto(origin + '/blank');
    await page.evaluate(async ({ project, bytes, referenceBytes, withReferences }) => {
        const p = await import('/probe.mjs');
        await p.images.setItem('image:preserved', new Blob([new Uint8Array(bytes)], { type: 'image/png' }));
        if (withReferences) await p.images.setItem('image:history-only', new Blob([new Uint8Array(referenceBytes)], { type: 'image/png' }));
        await p.initializeDocuments({ [p.documentNames[0]]: null, [p.documentNames[1]]: null });
        const document = await p.journalGet('documents', p.documentNames[0]);
        await p.saveDocument(document.id, JSON.stringify({ version: 0, state: { projects: [project], deletedProjects: [] } }), document.revision);
        await p.journalPut('budgets', { id: 'preserved-budget', nextAt: 42, pausedUntil: 0 });
        const put = IDBObjectStore.prototype.put;
        window.__archiveProbe = { writes: [], mode: '', count: 0 };
        IDBObjectStore.prototype.put = function(value, key) {
            const probe = window.__archiveProbe;
            if (this.name === 'image_files' && typeof key === 'string' && key.startsWith('image:import-')) {
                probe.writes.push(key); probe.count++;
                if (probe.mode === 'second-file' && probe.count === 2) throw new DOMException('Injected quota failure in second archive original', 'QuotaExceededError');
            }
            if (this.name === 'documents' && value.id === 'infinite-canvas:canvas_store' && value.value.includes('整包画布 A') && probe.mode === 'document-abort') {
                probe.rejectedDocument = JSON.parse(value.value).state.projects.map((item) => item.title);
                const request = put.call(this, value, key); this.transaction.abort(); return request;
            }
            return key === undefined ? put.call(this, value) : put.call(this, value, key);
        };
    }, { project: original, bytes: [...png], referenceBytes: [...referencePng], withReferences: suite !== '141' });
    await page.goto(origin + '/studio/canvas');
    // Navigation has a new realm; install the faults into the actual product page.
    await page.evaluate(() => {
        const put = IDBObjectStore.prototype.put;
        window.__archiveProbe = { writes: [], mode: '', count: 0 };
        IDBObjectStore.prototype.put = function(value, key) {
            const probe = window.__archiveProbe;
            if (this.name === 'image_files' && typeof key === 'string' && key.startsWith('image:import-')) { probe.writes.push(key); probe.count++; if (probe.mode === 'second-file' && probe.count === 2) throw new DOMException('Injected quota failure in second archive original', 'QuotaExceededError'); }
            if (this.name === 'documents' && value.id === 'infinite-canvas:canvas_store' && value.value.includes('整包画布 A') && probe.mode === 'document-abort') { probe.rejectedDocument = JSON.parse(value.value).state.projects.map((item) => item.title); const request = key === undefined ? put.call(this, value) : put.call(this, value, key); this.transaction.abort(); return request; }
            return key === undefined ? put.call(this, value) : put.call(this, value, key);
        };
    });
    if (Number(suite) >= 145) await page.evaluate(() => {
        const add = IDBObjectStore.prototype.add;
        IDBObjectStore.prototype.add = function(value, key) {
            const probe = window.__archiveProbe;
            if(this.name === 'local_media' && value?.id?.startsWith('image:managed-v1:')) {
                probe.writes.push(value.id); probe.count++;
                if(probe.mode === 'second-file' && probe.count === 2) throw new DOMException('Injected quota failure in second controlled original', 'QuotaExceededError');
            }
            return key === undefined ? add.call(this,value) : add.call(this,value,key);
        };
    });
    await page.getByRole('heading', { name: '保留的原画布', exact: true }).waitFor();
    return page;
}
try {
    server = createServer(async (req, res) => {
        try {
            const pathname = new URL(req.url, 'http://fixture.invalid').pathname;
            if (pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
            if (pathname === '/blank') { res.writeHead(200, { 'Content-Type': 'text/html', 'Content-Security-Policy': csp }); res.end('<!doctype html><title>Owned archive fixture</title>'); return; }
            let target;
            if (pathname === '/probe.mjs') target = path.join(temporary, 'probe.mjs');
            else if (pathname.startsWith('/studio/')) { target = path.resolve(dist, decodeURIComponent(pathname.slice(8))); assert(target === dist || target.startsWith(dist + path.sep)); try { if (!(await stat(target)).isFile()) target = path.join(dist, 'index.html'); } catch { target = path.join(dist, 'index.html'); } }
            else { report.unexpected_requests.push(req.method + ' ' + pathname); res.writeHead(404); res.end(); return; }
            const mime = { '.js': 'application/javascript', '.mjs': 'application/javascript', '.html': 'text/html', '.css': 'text/css', '.json': 'application/json', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png' }[path.extname(target)] || 'application/octet-stream';
            res.writeHead(200, { 'Content-Type': mime, 'Content-Security-Policy': path.basename(target).startsWith('schema-worker-') ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp, 'Cache-Control': 'no-store' }); res.end(await readFile(target));
        } catch { res.writeHead(500); res.end(); }
    });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const origin = publicMode ? 'http://43.165.167.179:39445' : 'http://canvas-archive.test:' + server.address().port;
    if (publicMode) { const index = await fetch(origin + '/studio/'); assert(index.ok); assert.equal(sha(Buffer.from(await index.arrayBuffer())), build.static_artifact_sha256['index.html']); }
    report.origin = origin;
    browser = await launchStudioWebBrowser({ root, temporary, report, args: ['--host-resolver-rules=MAP canvas-archive.test 127.0.0.1', '--no-proxy-server'] });
    const page = await seed(origin);
    assert.equal(await page.evaluate(() => isSecureContext), false);
    const before = await snapshot(page);
    const invalids = [
        ['unknown-version', (v) => { v.version = 999; }],
        ['invalid-title', (v) => { v.projects[1].project.title = {}; }],
        ['zero-zoom', (v) => { v.projects[1].project.viewport.k = 0; }],
        ['malformed-images', (v) => { v.projects[1].project.nodes[0].metadata.images = {}; }],
        ['duplicate-project', (v) => { v.projects[1].project.id = v.projects[0].project.id; }],
        ['duplicate-file-path', (v) => { v.projects[1].files[0].path = v.projects[0].files[0].path; }],
        ['prototype-field', (v) => { Object.defineProperty(v.projects[1].project, '__proto__', { value: { polluted: true }, enumerable: true }); }],
        ['wrong-length', (v) => { v.projects[1].files[0].bytes++; }],
        ['wrong-sha', (v) => { v.projects[1].files[0].sha256 = '0'.repeat(64); }],
        ['missing-file', (v) => { v.projects[1].files[0].path = 'files/missing.png'; }],
    ];
    if (suite !== '141') invalids.push(
        ['undeclared-reference-original', (v) => { v.projects[0].files = v.projects[0].files.filter((file) => file.storageKey !== 'image:reference-only'); }],
        ['missing-reference-original', (v) => { v.projects[0].files[1].path = 'files/missing-reference.png'; }],
    );
    for (const [name, mutate] of invalids) {
        const value = structuredClone(archived); mutate(value);
        await submit(page, archiveBuffer(value));
        assert.deepEqual(await snapshot(page), before, name);
        assert.equal(await page.evaluate(() => window.__archiveProbe.writes.length), 0, name);
        report.rejected_archives.push({ name, no_original_or_document_writes: true });
    }
    check('all malformed or corrupt archives rejected before any file/project writes; original DB records unchanged', { rejected_archives: invalids.length });
    await shot(page, 'archive-invalid-desktop');
    const download = page.waitForEvent('download'); await page.getByRole('button', { name: '导出', exact: true }).first().click();
    const backup = await download, backupFile = path.join(temporary, 'preserved-export.zip'); await backup.saveAs(backupFile);
    const exported = unzipSync(await readFile(backupFile)), manifest = JSON.parse(Buffer.from(exported['projects.json']).toString('utf8'));
    assert(fakeSecrets.every((secret) => !JSON.stringify(manifest).includes(secret)));
    assert.equal(sha(exported[manifest.projects[0].files.find((file) => file.storageKey === 'image:preserved').path]), sha(png));
    assert.deepEqual(await snapshot(page), before);
    check('actual project ZIP export omits header/cookie/fragment credentials, preserves source document and original SHA');
    if (suite !== '141') {
        await page.evaluate(async (data) => { (await import('/probe.mjs')).validateCanvasArchive(data); }, manifest);
        assert.equal(manifest.projects[0].files.length, 2);
        assert.equal(sha(exported[manifest.projects[0].files.find((file) => file.storageKey === 'image:history-only').path]), sha(referencePng));
        assert.deepEqual(manifest.projects[0].project.nodes[0].metadata.references, original.nodes[0].metadata.references);
        assert.equal(manifest.projects[0].project.nodes[0].metadata.prompt, 'image:history-only');
        check('UI ZIP includes reference-only originals once, retains ordered duplicate references and leaves prompt literals unchanged');
        await page.evaluate(async () => { await (await import('/probe.mjs')).images.removeItem('image:history-only'); });
        const unexpectedDownloads = [];
        const observe = (value) => unexpectedDownloads.push(value.suggestedFilename());
        page.on('download', observe);
        await page.getByRole('button', { name: '导出', exact: true }).first().click();
        await page.getByText('备份未完成：部分原件不可读，请使用原服务和 Key，先载入需要保存的产物', { exact: true }).waitFor();
        page.off('download', observe);
        assert.deepEqual(unexpectedDownloads, []);
        await page.evaluate(async (bytes) => { await (await import('/probe.mjs')).images.setItem('image:history-only', new Blob([new Uint8Array(bytes)], { type: 'image/png' })); }, [...referencePng]);
        assert.deepEqual(await snapshot(page), before);
        check('missing reference-only original refuses UI ZIP download without changing project documents; owned original restored after injection');
    }
    await page.evaluate(() => { window.__archiveProbe.mode = 'second-file'; window.__archiveProbe.count = 0; });
    await submit(page, archiveBuffer(archived));
    await page.getByText('本机存储空间不足，画布未加入项目列表。原有项目已保留，请先备份并释放空间后重试。', { exact: true }).waitFor();
    const fileFailure = await snapshot(page);
    assert.deepEqual(fileFailure.documents, before.documents);
    for (const store of ['intents', 'results', 'uploads', 'budgets']) assert.deepEqual(fileFailure[store], before[store]);
    assert.equal(Object.keys(fileFailure.originals).length, Object.keys(before.originals).length + 1);
    assert.equal(await page.getByRole('heading', { name: /^整包画布/ }).count(), 0);
    assert.deepEqual(fileFailure.originals['image:preserved'], before.originals['image:preserved']);
    check('second native original write quota fault leaves all projects unimported and originals intact; one new unreferenced blob retained', { newly_staged_blobs_retained: 1, actual_disk_exhaustion: false });
    await shot(page, 'archive-second-file-failed');
    await page.evaluate(() => { window.__archiveProbe.mode = ''; });
    await submit(page, archiveBuffer(archived));
    await page.getByRole('heading', { name: '整包画布 A', exact: true }).waitFor(); await page.getByRole('heading', { name: '整包画布 B', exact: true }).waitFor();
    const accepted = await snapshot(page), values = JSON.parse(accepted.documents.find((doc) => doc.id.endsWith('canvas_store')).value).state.projects;
    assert.equal(values.length, 3); assert.equal(accepted.documents.find((doc) => doc.id.endsWith('canvas_store')).revision, before.documents.find((doc) => doc.id.endsWith('canvas_store')).revision + 1);
    const restored = values.find((item) => item.title === '整包画布 A');
    assert(!restored.nodes.some((node) => node.type === 'studio:task' || node.metadata?.studioIntentId));
    assert(restored.nodes.some((node) => node.metadata?.content?.includes('不会重新提交')));
    assert.notEqual(restored.nodes[0].metadata.storageKey, first.nodes[0].metadata.storageKey);
    assert.equal(await page.evaluate(async (key) => { const p = await import('/probe.mjs'); return p.blobHash(await p.originalBlob(key)); }, restored.nodes[0].metadata.storageKey), sha(png));
    check('successful two-project import publishes one atomic document revision with fresh keys, original bytes and offline-only task notes');
    await shot(page, 'archive-imported-library-desktop');
    await page.goto(origin + '/studio/canvas/' + restored.id);
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-node-id] img')).some((image) => image.complete && image.naturalWidth === 1));
    assert.equal(await page.evaluate(() => Boolean(window.__archiveExecuted)), false);
    await page.reload(); await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    assert.equal(await page.evaluate(() => Boolean(window.__archiveExecuted)), false);
    check('restored image loads after refresh; script-shaped text remains inert and no task is re-created');
    await shot(page, 'archive-restored-canvas-desktop');
    const variants = values.find((item) => item.title === '整包画布 B');
    await page.goto(origin + '/studio/canvas/' + variants.id);
    await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-node-id] img')).some((image) => image.complete && image.naturalWidth === 1 && image.src.startsWith('blob:')));
    await page.reload();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-node-id] img')).some((image) => image.complete && image.naturalWidth === 1 && image.src.startsWith('blob:')));
    check('nested image variants without parent content/storage key restore from their own new keys and survive refresh');
    if (suite !== '141') {
        await page.goto(origin + '/studio/canvas');
        await page.getByRole('heading', { name: '整包画布 A', exact: true }).waitFor();
        const reexported = await exportedProject(page, '整包画布 A', 'remapped-references.zip');
        const item = reexported.value.projects[0], metadata = item.project.nodes[0].metadata;
        assert.equal(item.project.title, '整包画布 A'); assert.equal(item.files.length, 2);
        assert.equal(metadata.prompt, 'image:reference-only');
        assert.equal(metadata.references.length, 3); assert.equal(metadata.references[0], metadata.references[2]);
        assert.equal(metadata.references[1], metadata.storageKey);
        assert(metadata.references.every((key) => key.startsWith(Number(suite) >= 145 ? 'image:managed-v1:' : 'image:import-')));
        const hashes = await page.evaluate(async (keys) => { const p = await import('/probe.mjs'); return Promise.all(keys.map(async (key) => p.blobHash(await p.originalBlob(key)))); }, metadata.references);
        assert.deepEqual(hashes, [sha(referencePng), sha(png), sha(referencePng)]);
        check('imported reference keys, order, duplicates and exact native originals survive refresh and actual project UI re-export');
        await shot(page, 'archive-reference-reexport');
    }
    await page.goto(origin + '/studio/canvas/' + restored.id);
    await page.locator('[data-node-id] img[src^="blob:"]').first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await shot(page, 'archive-restored-canvas-390');
    check('390px restored snapshot remains within viewport; emulation only');
    const aborted = await seed(origin);
    const abortBefore = await snapshot(aborted);
    await aborted.evaluate(() => { window.__archiveProbe.mode = 'document-abort'; });
    await submit(aborted, archiveBuffer(archived));
    await aborted.getByText('本机编辑尚未保存', { exact: true }).waitFor();
    assert.deepEqual((await snapshot(aborted)).documents, abortBefore.documents);
    const rejected = await aborted.evaluate(() => window.__archiveProbe.rejectedDocument);
    assert(rejected.includes('整包画布 A') && rejected.includes('整包画布 B'));
    assert.equal(await aborted.getByRole('heading', { name: /^整包画布/ }).count(), 2);
    check('aborted document transaction commits neither imported project; whole unsaved batch remains visible with retry banner');
    await shot(aborted, 'archive-document-aborted');
    await aborted.evaluate(() => { window.__archiveProbe.mode = ''; });
    await aborted.getByRole('button', { name: '重试保存', exact: true }).click();
    await aborted.getByText('本机编辑尚未保存', { exact: true }).waitFor({ state: 'detached' });
    const retry = await snapshot(aborted);
    assert.equal(JSON.parse(retry.documents.find((doc) => doc.id.endsWith('canvas_store')).value).state.projects.length, 3);
    await aborted.reload(); await aborted.getByRole('heading', { name: '整包画布 A', exact: true }).waitFor(); await aborted.getByRole('heading', { name: '整包画布 B', exact: true }).waitFor();
    check('explicit save retry commits the retained whole batch once; both projects survive refresh without repeating media import');
    if (suite !== '141') {
        const winner = await seed(origin);
        const stale = await winner.context().newPage(); pages.push(stale); videos.push(stale.video()); active = stale;
        stale.on('pageerror', (error) => report.page_errors.push(error.message));
        await stale.goto(origin + '/studio/canvas'); await stale.getByRole('heading', { name: '保留的原画布', exact: true }).waitFor();
        await submit(winner, archiveBuffer(archived));
        await winner.getByRole('heading', { name: '整包画布 A', exact: true }).waitFor();
        const committed = await snapshot(winner);
        const committedId = JSON.parse(committed.documents.find((doc) => doc.id.endsWith('canvas_store')).value).state.projects.find((item) => item.title === '整包画布 A').id;
        await submit(stale, archiveBuffer(archived));
        await stale.getByText('本机编辑尚未保存', { exact: true }).waitFor();
        await stale.getByText('另一页面已更新本机文档；请先导出当前画布，再重新载入，未覆盖其他页面的版本', { exact: true }).first().waitFor();
        assert.equal(await stale.getByRole('button', { name: '重试保存', exact: true }).count(), 0);
        assert.deepEqual((await snapshot(stale)).documents, committed.documents);
        const unsaved = await exportedProject(stale, '整包画布 A', 'cross-page-unsaved.zip');
        assert.notEqual(unsaved.value.projects[0].project.id, committedId);
        assert.equal(unsaved.value.projects[0].files.length, 2);
        assert.equal(unsaved.value.projects[0].project.nodes[0].metadata.references.length, 3);
        assert.deepEqual((await snapshot(stale)).documents, committed.documents);
        check('stale second-page import cannot overwrite winner documents or blindly retry; whole unsaved reference project can be exported intact');
        await shot(stale, 'archive-cross-page-conflict');
    }
    assert.deepEqual(report.page_errors, []); assert.deepEqual(report.unexpected_requests, []);
    check('all cases make zero API, generation, plugin, ComfyUI or external requests and no page errors');
    report.passed = true;
} catch (error) { report.failure = error.stack; if (active && !active.isClosed()) await shot(active, 'archive-failure-' + label).catch(() => {}); }
finally {
    await Promise.allSettled(pages.map((page) => page.close()));
    await Promise.allSettled(contexts.map((context) => context.close()));
    if (browser) await browser.close();
    if (server) await new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); });
    report.cleanup = { pages_closed: pages.every((page) => page.isClosed()), contexts_closed: contexts.every((context) => !context.pages().length), browser_closed: !browser?.isConnected(), server_closed: !server?.listening };
    report.private_videos = [];
    for (const video of videos) if (video) try { report.private_videos.push(path.relative(root, await video.path()).replaceAll('\\', '/')); } catch { /* failure retained */ }
}
for (const [file, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), digest);
report.static_bytes_unchanged = true;
const reportFile = path.join(root, 'docs/research/studio-implementation/archive-import-' + (publicMode ? 'public-' : '') + label + '.json');
await writeFile(reportFile, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, reportFile), cleanup: report.cleanup, failure: report.failure }));
if (!report.passed) process.exitCode = 1;
