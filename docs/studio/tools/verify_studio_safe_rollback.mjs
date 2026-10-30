/** Exercise real Web release -> compatible recovery -> restore with one native browser DB. */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../../', import.meta.url)), web = path.join(root, 'infinite-canvas/web'), origin = 'http://43.165.167.179:39445';
const require = createRequire(path.join(web, 'package.json'));
const { chromium } = require('C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const sha = (data) => createHash('sha256').update(data).digest('hex'), stamp = new Date().toISOString().replaceAll(/[:.]/g, '-');
const privateDir = path.join(root, '.local/studio146/rollback-' + stamp), media = path.join(root, 'docs/media/studio-146/rollback-' + stamp);
await mkdir(privateDir, { recursive: true }); await mkdir(media, { recursive: true });
const manifests = JSON.parse(await readFile(path.join(root, '.local/studio146/manifests.json'), 'utf8'));
const original = manifests['canvas-20261030.145.1'], current = manifests['canvas-20261030.146.1'], recovery = manifests['canvas-20261030.146.0'];
assert.deepEqual(original.files, recovery.files); assert.deepEqual(current.storage_contract, recovery.storage_contract);
const oldPaths = new Set(original.files.map((file) => '/' + file.path)), newPaths = new Set(current.files.map((file) => '/' + file.path));
const expected = new Map([...original.files, ...current.files].map((file) => ['/' + file.path, file]));
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/studio-journal-db"; export {blobHash} from "./src/services/api/aggregator/client";', resolveDir: web }, bundle: true, platform: 'browser', format: 'esm', outfile: path.join(privateDir, 'probe.mjs'), logLevel: 'warning' });
const operationId = 'web-recheck-3';
const report = { passed: false, at: new Date().toISOString(), operation_id: operationId, start_release: recovery.release_id, scope: 'Actual public independent Web restore from the byte-identical 145.1 recovery, compatible v5 rollback and restore. Raw records must match across static switches and readonly pages; writable-canvas reopening may only refresh validated local Blob URLs, active-project updatedAt, a monotonic canvas document revision and the documented absent-to-empty image-variants default. One owned native Chromium context, synthetic projects/media/frozen task and no business API/GPU requests; not arbitrary-version or full browser fault acceptance.', origin, checks: [], screenshots: [], unexpected_requests: [], page_errors: [], retained_resource_responses: [], cleanup: {} };
const contexts = [], pages = [], videos = [], pendingResponses = [], phaseByRequest = new WeakMap(); let browser, phase = 'original';
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
async function operation(action, id = operationId) {
    await new Promise((resolve, reject) => execFile('E:/dev/sandbox/生图生视API封装/app/.venv/Scripts/python.exe', ['-X', 'utf8', '-B', path.join(root, 'docs/tools/studio_safe_release.py'), action, ...(id === 'initial' ? [] : [id])], { cwd: root, windowsHide: true }, (error, stdout, stderr) => error ? reject(new Error('Static ' + action + ' failed; inspect its original saved status')) : resolve(stdout)));
    const stored = JSON.parse(await readFile(path.join(root, '.local/studio146/safe-' + action + (id === 'initial' ? '' : '-' + id) + '.json'), 'utf8')); assert.equal(stored.exit_code, 0);
    const value = JSON.parse(stored.stdout); assert(value.passed && value.api_unchanged); assert.equal(value.static_service_restarted, false); return value;
}
async function pageIn(context, url) { const page = await context.newPage(); pages.push(page); videos.push(page.video()); page.on('pageerror', (error) => report.page_errors.push(error.message)); await page.goto(origin + url); return page; }
async function shot(page, name) { const file = path.join(media, name + '.png'); await page.screenshot({ path: file }); report.screenshots.push({ file: path.relative(root, file).replaceAll('\\', '/'), bytes: (await stat(file)).size, sha256: sha(await readFile(file)) }); }
async function httpEntry(manifest) { for (const name of ['studio/index.html', 'studio/maintenance.html']) { const response = await fetch(origin + '/' + name); assert(response.ok); assert.equal(sha(Buffer.from(await response.arrayBuffer())), manifest.files.find((file) => file.path === name).sha256); } }
async function snapshot(page) {
    return page.evaluate(async () => {
        const p = await import('/probe.mjs'), stores = ['documents', 'intents', 'uploads', 'results', 'budgets', 'local_media'];
        const journal = await p.rawJournalTransaction(stores, 'readonly', (tx, result) => { const rows = { version: tx.db.version }; let remaining = stores.length; for (const store of stores) { const request = tx.objectStore(store).getAll(); request.onsuccess = () => { rows[store] = request.result; if (!--remaining) result(rows); }; } });
        const request = indexedDB.open('infinite-canvas'); request.onupgradeneeded = () => request.transaction.abort();
        const database = await new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        const legacy = await new Promise((resolve, reject) => { const tx = database.transaction('image_files', 'readonly'), keys = tx.objectStore('image_files').getAllKeys(), values = tx.objectStore('image_files').getAll(); tx.oncomplete = () => resolve({ version: database.version, keys: keys.result, values: values.result }); tx.onabort = () => reject(tx.error); }); database.close();
        const encode = async (value) => { if (value instanceof Blob) return { blob: true, bytes: value.size, mime: value.type, sha256: await p.blobHash(value) }; if (Array.isArray(value)) return Promise.all(value.map(encode)); if (value && typeof value === 'object') return Object.fromEntries(await Promise.all(Object.entries(value).map(async ([key, item]) => [key, await encode(item)]))); return value; };
        return encode({ journal, legacy });
    });
}
async function openStorage(page) { await page.getByRole('link', { name: '连接服务', exact: true }).click(); await page.getByRole('link', { name: '查看本机存储空间', exact: true }).click(); await page.getByRole('heading', { name: '给创作留出空间', exact: true }).waitFor(); }
async function flushAndLeave(page) { await openStorage(page); await page.getByRole('button', { name: '保存并进入安全清理', exact: true }).click(); await page.getByRole('heading', { name: '本机安全清理', exact: true }).waitFor(); }
function semanticSnapshot(value) {
    const result = structuredClone(value), doc = result.journal.documents.find((row) => row.id === 'infinite-canvas:canvas_store');
    assert(Number.isInteger(doc.revision)); doc.revision = 0;
    const body = JSON.parse(doc.value), project = body.state.projects.find((row) => row.id === 'rollback-project');
    assert(Number.isFinite(Date.parse(project.updatedAt))); project.updatedAt = '<render-save-time>';
    for (const node of project.nodes) {
        if (node.type !== 'image' || !node.metadata?.storageKey) continue;
        assert(result.journal.local_media.some((file) => file.id === node.metadata.storageKey));
        assert.match(node.metadata.content, /^blob:http:\/\/43\.165\.167\.179:39445\//);
        node.metadata.content = '<validated-local-blob-url>';
        if (node.metadata.images === undefined) node.metadata.images = [];
        assert(Array.isArray(node.metadata.images));
    }
    doc.value = JSON.stringify(body); return result;
}
function assertRenderedPreservation(actual, expected) {
    const doc = (value) => value.journal.documents.find((row) => row.id === 'infinite-canvas:canvas_store');
    assert(doc(actual).revision >= doc(expected).revision);
    assert(Date.parse(JSON.parse(doc(actual).value).state.projects.find((row) => row.id === 'rollback-project').updatedAt) >= Date.parse(JSON.parse(doc(expected).value).state.projects.find((row) => row.id === 'rollback-project').updatedAt));
    assert.deepEqual(semanticSnapshot(actual), semanticSnapshot(expected));
}
try {
    await httpEntry(recovery);
    process.env.TEMP = privateDir; process.env.TMP = privateDir;
    browser = await chromium.launch({ executablePath: 'C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: privateDir } }); contexts.push(context);
    await context.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if (url.origin !== origin || /\/(?:v1|api|admin|internal)\//.test(url.pathname)) { report.unexpected_requests.push(route.request().method() + ' ' + url.origin + url.pathname); return route.abort(); }
        if (url.pathname === '/probe.mjs') return route.fulfill({ status: 200, contentType: 'application/javascript', body: await readFile(path.join(privateDir, 'probe.mjs')) });
        if (url.pathname === '/blank') return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Owned Web rollback data fixture</title>' });
        return route.continue();
    });
    context.on('request', (request) => phaseByRequest.set(request, phase));
    context.on('response', (response) => { const url = new URL(response.url()); if (!url.pathname.startsWith('/studio/assets/') || !expected.has(url.pathname)) return; const at = phaseByRequest.get(response.request()); pendingResponses.push((async () => { const bytes = await response.body(); report.retained_resource_responses.push({ phase: at, path: url.pathname, status: response.status(), sha256: sha(bytes) }); })()); });
    const inspector = await pageIn(context, '/blank');
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=', 'base64');
    await inspector.evaluate(async (bytes) => {
        const p = await import('/probe.mjs'), png = new Blob([new Uint8Array(bytes)], { type: 'image/png' });
        const wav = new ArrayBuffer(44 + 800 * 2), view = new DataView(wav), text = (at, value) => [...value].forEach((char, index) => view.setUint8(at + index, char.charCodeAt(0)));
        text(0, 'RIFF'); view.setUint32(4, wav.byteLength - 8, true); text(8, 'WAVE'); text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, 8000, true); view.setUint32(28, 16000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, 1600, true);
        const audio = new Blob([wav], { type: 'audio/wav' }), pngHash = await p.blobHash(png);
        const imageKey = 'image:managed-v1:rollback-image', audioKey = 'file:managed-v1:rollback-audio';
        const project = { id: 'rollback-project', title: 'Web 回退验证', nodes: [{ id: 'rollback-image', type: 'image', title: '保留的本机图片', position: { x: 60, y: 120 }, width: 220, height: 220, metadata: { storageKey: imageKey, naturalWidth: 1, naturalHeight: 1, bytes: png.size, mimeType: png.type, status: 'success' } }], connections: [], chatSessions: [], activeChatId: null, viewport: { x: 0, y: 0, k: 1 }, backgroundMode: 'dots', showImageInfo: false, createdAt: '2026-10-30T00:00:00.000Z', updatedAt: '2026-10-30T00:00:00.000Z' };
        const trashProject = { ...project, id: 'rollback-trash', title: '回收站保留作品' };
        const trash = { id: trashProject.id, deletedAt: new Date().toISOString() };
        await p.rawJournalTransaction(['documents', 'local_media', 'intents', 'uploads', 'results', 'budgets'], 'readwrite', (tx, result) => {
            for (const [id, state] of [['infinite-canvas:canvas_store', { projects: [project, trashProject], deletedProjects: [trash] }], ['infinite-canvas:asset_store', { assets: [] }]]) tx.objectStore('documents').add({ version: 1, id, revision: 7, value: JSON.stringify({ version: 0, state }), storageKeys: id.endsWith('canvas_store') ? [imageKey] : [], legacyValue: null });
            for (const [id, blob, kind] of [[imageKey, png, 'image'], [audioKey, audio, 'media']]) tx.objectStore('local_media').add({ version: 1, id, blob, kind, bytes: blob.size, mimeType: blob.type, createdAt: 1 });
            const identity = { profileId: 'synthetic-rollback', origin: 'http://fixture.invalid', fingerprint: 'synthetic-rollback' };
            tx.objectStore('intents').add({ version: 1, id: 'original-rollback-task', identity, projectId: project.id, nodeId: 'original-task-node', body: JSON.stringify({ model: 'synthetic-original', inputs: { prompt: 'unchanged Web rollback task', references: [imageKey, audioKey, imageKey] } }), idempotencyKey: 'original-rollback-idempotency', releaseDigest: 'original-release', createdAt: 1, updatedAt: 2, stage: 'terminal', output: { mediaTypes: ['image/png'], maxBytes: 1000 } });
            tx.objectStore('uploads').add({ version: 1, id: 'original-reference', identity, name: 'synthetic.png', blob: png, sha256: pngHash, idempotencyKey: 'original-reference-idempotency', createdAt: 1 });
            tx.objectStore('results').add({ version: 1, id: 'studio:rollback-result', identity, intentId: 'original-rollback-task', metadata: { id: 'original-result' }, blob: png, savedAt: 2 });
            tx.objectStore('budgets').add({ id: 'original-rollback-budget', nextAt: 10, pausedUntil: 20 }); result();
        });
        const request = indexedDB.open('infinite-canvas', 1); request.onupgradeneeded = () => { for (const store of ['image_files', 'media_files', 'image_previews', 'image_generation_logs', 'video_generation_logs', 'app_state']) request.result.createObjectStore(store); };
        const database = await new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        const tx = database.transaction('image_files', 'readwrite'); tx.objectStore('image_files').add(png, 'image:legacy-rollback'); await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); }); database.close();
    }, [...png]);
    const oldPage = await pageIn(context, '/studio/'); await oldPage.getByText('Web 回退验证', { exact: true }).waitFor();
    const baseline = await snapshot(inspector); assert.equal(baseline.journal.version, 5);
    report.before_data_sha256 = sha(JSON.stringify(baseline));
    check('actual byte-identical 145.1 recovery Web page loads native v5 project, trash, originals, frozen task, reference/result blobs and budget');
    report.initial_restore = await operation('restore', 'web-start-3'); phase = 'old-page-on-new'; await httpEntry(current);
    await openStorage(oldPage); assert.deepEqual(await snapshot(inspector), baseline);
    check('old v5 Web page loads its original lazy resources after real 146.1 restore without changing business records');
    const newPage = await pageIn(context, '/studio/canvas/rollback-project'); await newPage.locator('[data-canvas-title]').waitFor();
    await newPage.waitForFunction(() => [...document.querySelectorAll('[data-node-id] img')].some((image) => image.complete && image.naturalWidth === 1 && image.src.startsWith('blob:')));
    await newPage.locator('input[type="file"][accept*="audio/mpeg"]').setInputFiles({ name: 'new-Web-original.png', mimeType: 'image/png', buffer: png });
    await inspector.waitForFunction(async () => { const p = await import('/probe.mjs'); return p.rawJournalTransaction(['documents', 'local_media'], 'readonly', (tx, result) => { const files = tx.objectStore('local_media').getAll(), doc = tx.objectStore('documents').get('infinite-canvas:canvas_store'); let count = 0; const ready = () => { if (++count === 2) result(files.result.length === 3 && doc.result.storageKeys.length === 2); }; files.onsuccess = ready; doc.onsuccess = ready; }); });
    await newPage.keyboard.press('Escape');
    await shot(newPage, 'new-Web-project-before-rollback');
    await flushAndLeave(newPage);
    const committed = await snapshot(inspector);
    const saved = JSON.parse(committed.journal.documents.find((row) => row.id === 'infinite-canvas:canvas_store').value).state.projects.find((row) => row.id === 'rollback-project');
    assert.equal(saved.nodes.length, 2); assert.equal(new Set(saved.nodes.map((node) => node.metadata.storageKey)).size, 2);
    assert.equal(committed.journal.documents.find((row) => row.id === 'infinite-canvas:canvas_store').storageKeys.length, 2);
    for (const name of ['intents', 'uploads', 'results', 'budgets']) assert.deepEqual(committed.journal[name], baseline.journal[name]);
    assert.deepEqual(committed.legacy, baseline.legacy);
    assert.deepEqual(committed.journal.local_media.filter((file) => baseline.journal.local_media.some((old) => old.id === file.id)), baseline.journal.local_media);
    report.committed_data_sha256 = sha(JSON.stringify(committed));
    report.committed_semantic_sha256 = sha(JSON.stringify(semanticSnapshot(committed)));
    check('new 146.1 Web editor renders original and durably saves both nodes via the explicit UI save-and-leave barrier while frozen task and original bytes remain identical');
    await newPage.close();
    const lazyNewPage = await pageIn(context, '/studio/'); await lazyNewPage.getByText('Web 回退验证', { exact: true }).waitFor();
    assert.deepEqual(await snapshot(inspector), committed);
    report.reject_v4 = await operation('reject-v4'); assert(report.reject_v4.refused && report.reject_v4.pointer_unchanged);
    check('actual installed v4-only 144.1 target is refused before changing the static pointer');
    report.rollback = await operation('rollback'); phase = 'new-page-on-recovery'; await httpEntry(recovery);
    await openStorage(lazyNewPage); const afterRollback = await snapshot(inspector); assert.deepEqual(afterRollback, committed);
    report.raw_rollback_data_sha256 = sha(JSON.stringify(afterRollback));
    check('actual rollback and newly loaded 146.1 lazy pages preserve every committed raw business record exactly');
    const recoveredPage = await pageIn(context, '/studio/canvas/rollback-project'); await recoveredPage.locator('[data-canvas-title]').waitFor();
    await recoveredPage.waitForFunction(() => [...document.querySelectorAll('[data-node-id] img')].some((image) => image.complete && image.naturalWidth === 1 && image.src.startsWith('blob:')));
    await recoveredPage.waitForFunction(() => document.querySelectorAll('[data-node-id]').length === 2);
    await shot(recoveredPage, 'compatible-recovery-renders-saved-Web-project');
    await flushAndLeave(recoveredPage);
    const recovered = await snapshot(inspector); assertRenderedPreservation(recovered, committed);
    report.raw_before_restore_data_sha256 = sha(JSON.stringify(recovered));
    check('real compatible recovery preserves new saved project, trash, task body/ID/idempotency, originals, reference/result blobs, legacy file and budget');
    report.restore = await operation('restore'); phase = 'restored'; await httpEntry(current);
    const rawRestored = await snapshot(inspector); assert.deepEqual(rawRestored, recovered);
    report.raw_after_restore_data_sha256 = sha(JSON.stringify(rawRestored));
    const restoredPage = await pageIn(context, '/studio/canvas/rollback-project'); await restoredPage.locator('[data-canvas-title]').waitFor();
    await restoredPage.waitForFunction(() => [...document.querySelectorAll('[data-node-id] img')].filter((image) => image.complete && image.naturalWidth === 1 && image.src.startsWith('blob:')).length === 2);
    await shot(restoredPage, 'restored-Web-project');
    await flushAndLeave(restoredPage);
    const final = await snapshot(inspector); assertRenderedPreservation(final, recovered); assertRenderedPreservation(final, committed);
    report.after_data_sha256 = sha(JSON.stringify(final));
    report.after_semantic_sha256 = sha(JSON.stringify(semanticSnapshot(final)));
    assert.equal(report.after_semantic_sha256, report.committed_semantic_sha256);
    check('actual restore preserves raw records before reopening and complete saved work after render, with only validated Blob URLs, active updatedAt and monotonic canvas revision allowed to refresh');
    await Promise.all(pendingResponses);
    for (const response of report.retained_resource_responses) { assert.equal(response.status, 200); assert.equal(response.sha256, expected.get(response.path).sha256); }
    assert(report.retained_resource_responses.some((response) => response.phase === 'old-page-on-new' && oldPaths.has(response.path) && !newPaths.has(response.path)));
    assert(report.retained_resource_responses.some((response) => response.phase === 'new-page-on-recovery' && newPaths.has(response.path) && !oldPaths.has(response.path)));
    check('real old-only and new-only lazy resources are served byte-identically across both pointer directions');
    assert.deepEqual(report.unexpected_requests, []); assert.deepEqual(report.page_errors, []);
    report.actual_api_requests = 0; report.generation_posts = 0; report.passed = true;
} catch (error) { report.error = { name: error.name, message: error.message, stack: error.stack }; }
finally {
    for (const context of contexts) await context.close(); if (browser) await browser.close();
    report.private_recordings = []; for (const video of videos) if (video) report.private_recordings.push(path.relative(root, await video.path()).replaceAll('\\', '/'));
    report.cleanup = { pages_closed: pages.every((page) => page.isClosed()), contexts_closed: contexts.every((context) => !context.pages().length), browser_closed: !browser || !browser.isConnected() };
    const file = path.join(root, 'docs/research/studio-implementation/safe-rollback-Web-' + stamp + '.json'); await writeFile(file, JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file).replaceAll('\\', '/'), cleanup: report.cleanup, error: report.error?.message }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean)) process.exitCode = 1;
