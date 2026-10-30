/** Native IndexedDB protocol verification; synthetic local bytes, no service/GPU. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url)), web = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(web, 'package.json'));
const evidenceVersion = process.argv[2] || '145'; assert(/^\d+$/.test(evidenceVersion));
const stamp = new Date().toISOString().replaceAll(/[:.]/g, '-'), temporary = path.join(root, '.local/studio' + evidenceVersion + '/protocol-' + stamp);
await mkdir(temporary, { recursive: true });
const probe = 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-editor-session"; export * from "./src/services/studio-local-media"; export * from "./src/services/studio-local-media-cleanup"; export * from "./src/services/studio-journal-db";';
const build = async (contents, resolveDir, outfile, globalName) => require('esbuild').build({ stdin: { contents, resolveDir, loader: 'ts' }, bundle: true, platform: 'browser', format: 'iife', globalName, outfile, logLevel: 'warning' });
await build(probe, web, path.join(temporary, 'probe.js'), 'probe');
const previous = execFileSync('git', ['show', '855ea4ffee6a69df3bdec3d38bd0992dba60fe33:web/src/services/aggregator-journal.ts'], { cwd: path.join(root, 'infinite-canvas'), encoding: 'utf8' });
await build(previous, path.join(web, 'src/services'), path.join(temporary, 'previous.js'), 'previous');
const report = { source_revision: execFileSync('git', ['rev-parse', 'HEAD'], {cwd:path.join(root,'infinite-canvas'),encoding:'utf8'}).trim(), passed: false, at: new Date().toISOString(), scope: 'Native Windows Chromium IndexedDB v4-to-v5, connection liveness, atomic references/deletion and malformed-state refusal; synthetic local data, no actual disk exhaustion/phone/GPU acceptance', checks: [], api_requests: [], page_errors: [], cleanup: {} };
let browser, server; const contexts = [];
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
const databaseName = 'comfy-aggregator-studio:journal';
async function pageIn(context) { const page = await context.newPage(); page.on('pageerror', (error) => report.page_errors.push(error.message)); await page.goto(origin); await page.addScriptTag({ path: path.join(temporary, 'probe.js') }); return page; }
async function contextIn() { const context = await browser.newContext(); contexts.push(context); context.on('request', (request) => { if (/\/v1\/|:8188|:8000/.test(request.url())) report.api_requests.push(request.method() + ' ' + request.url()); }); return context; }
async function seedV4(page, extra = false) {
    return page.evaluate(async ({ name, extra }) => {
        const request = indexedDB.open(name, 4);
        request.onupgradeneeded = () => {
            const intents = request.result.createObjectStore('intents', { keyPath: 'id' }); intents.createIndex('active_node', 'activeNodeKey', { unique: true });
            for (const store of ['documents', 'results', 'uploads', 'budgets', ...(extra ? ['unknown_store'] : [])]) request.result.createObjectStore(store, { keyPath: 'id' });
        };
        const database = await new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        const tx = database.transaction(['documents', 'intents', 'budgets', 'results', 'uploads'], 'readwrite');
        const docs = ['infinite-canvas:canvas_store', 'infinite-canvas:asset_store'].map((id, index) => ({ version: 1, id, revision: 8, value: JSON.stringify({ version: 0, state: index ? { assets: [] } : { projects: [], deletedProjects: [] } }), storageKeys: [], legacyValue: null }));
        docs.forEach((doc) => tx.objectStore('documents').put(doc));
        const intent = { version: 1, id: 'original-task', identity: { profileId: 'synthetic', origin: 'http://fixture.invalid', fingerprint: 'synthetic' }, projectId: 'original-project', nodeId: 'original-node', body: '{"model":"synthetic","inputs":{"prompt":"unchanged original"}}', idempotencyKey: 'same-original-idempotency', releaseDigest: 'same-release', createdAt: 1, updatedAt: 2, stage: 'terminal', output: { mediaTypes: ['image/png'], maxBytes: 100 } };
        tx.objectStore('intents').put(intent); tx.objectStore('budgets').put({ id: 'original-budget', nextAt: 10, pausedUntil: 20 });
        tx.objectStore('uploads').put({ version: 1, id: 'preserved-upload', identity: intent.identity, name: 'synthetic-original.png', blob: new Blob(['original-upload'], { type: 'image/png' }), sha256: 'b'.repeat(64), idempotencyKey: 'original-upload-key', createdAt: 1 });
        tx.objectStore('results').put({ version: 1, id: 'studio:preserved', identity: intent.identity, intentId: intent.id, metadata: { id: 'preserved-result' }, blob: new Blob(['original-result'], { type: 'image/png' }), savedAt: 2 });
        await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); }); database.close();
        return { docs: docs.sort((a, b) => a.id.localeCompare(b.id)), intent };
    }, { name: databaseName, extra });
}
async function raw(page, store) { return page.evaluate((store) => probe.rawJournalTransaction([store], 'readonly', (tx, result) => { const request = tx.objectStore(store).getAll(); request.onsuccess = () => result(request.result); }), store); }
async function releaseClaims(page) {
    await page.evaluate(async () => { (await probe.readCleanupSnapshot()).editors.forEach(probe.probeStudioEditor); });
    await page.waitForFunction(async () => !(await probe.readCleanupSnapshot()).editors.length);
}
let origin;
try {
    server = createServer((_request, response) => { response.writeHead(200, { 'Content-Type': 'text/html' }); response.end('<!doctype html><title>Owned native cleanup fixture</title>'); });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + server.address().port;
    browser = await launchStudioWebBrowser({ root, temporary, report });
    const context = await contextIn(), editor = await pageIn(context), maintenance = await pageIn(context);
    const originals = await seedV4(editor);
    await editor.addScriptTag({ path: path.join(temporary, 'previous.js') });
    assert.deepEqual(await editor.evaluate(() => previous.journalList('documents')), originals.docs);
    check('actual previous v4 journal reads the seeded immutable documents');
    const claim = await editor.evaluate(() => probe.ensureStudioEditor());
    assert.deepEqual(await raw(maintenance, 'documents'), originals.docs); assert.deepEqual(await raw(maintenance, 'intents'), [originals.intent]);
    assert.deepEqual(await raw(maintenance, 'budgets'), [{ id: 'original-budget', nextAt: 10, pausedUntil: 20 }]);
    assert.deepEqual(await maintenance.evaluate(async () => { const upload = await probe.rawJournalTransaction(['uploads'], 'readonly', (tx, result) => { const request = tx.objectStore('uploads').get('preserved-upload'); request.onsuccess = () => result(request.result); }); const file = await probe.rawJournalTransaction(['results'], 'readonly', (tx, result) => { const request = tx.objectStore('results').get('studio:preserved'); request.onsuccess = () => result(request.result); }); return [await upload.blob.text(), upload.idempotencyKey, await file.blob.text(), file.intentId]; }), ['original-upload', 'original-upload-key', 'original-result', 'original-task']);
    check('native v4-to-v5 retains original document bytes/revisions, frozen task body/idempotency and budget');
    assert.equal(await editor.evaluate(async () => { try { await previous.journalList('documents'); return 'unexpected-success'; } catch (error) { return error.name; } }), 'VersionError');
    check('actual previous v4 functions are fenced after v5 without downgrade');
    const keys = await editor.evaluate(async () => {
        const first = probe.newManagedMediaKey('image'), second = probe.newManagedMediaKey('media');
        await probe.addManagedMedia(first, new Blob(['retained-original'], { type: 'image/png' }));
        await probe.addManagedMedia(second, new Blob(['unused-original'], { type: 'video/mp4' }));
        const docs = await probe.journalList('documents'), doc = docs.find((item) => item.id.endsWith('canvas_store'));
        await probe.saveDocument(doc.id, JSON.stringify({ version: 0, state: { projects: [], deletedProjects: [{ project: { id: 'trash', nodes: [{ metadata: { references: [first] } }] } }] } }), doc.revision);
        return { first, second };
    });
    const saved = (await raw(maintenance, 'documents')).find((item) => item.id.endsWith('canvas_store'));
    assert.deepEqual(saved.storageKeys, [keys.first]); check('controlled reference-only originals are protected by recoverable trash in document transaction');
    assert.equal(await editor.evaluate(async (id) => { try { await probe.addManagedMedia(id, new Blob(['overwrite'])); return false; } catch { return await (await probe.getManagedMediaBlob(id)).text() === 'retained-original'; } }, keys.first), true);
    check('duplicate controlled key cannot overwrite committed original bytes');
    assert.equal(await editor.evaluate(async () => {
        const docs = await probe.journalList('documents'), doc = docs.find((item) => item.id.endsWith('canvas_store'));
        try { await probe.saveDocument(doc.id, JSON.stringify({ version: 0, state: { projects: [{ metadata: { references: ['image:managed-v1:missing'] } }] } }), doc.revision); return false; } catch { return true; }
    }), true);
    assert.deepEqual((await raw(maintenance, 'documents')).find((item) => item.id.endsWith('canvas_store')), saved);
    check('saving a new missing original reference aborts without changing document');
    let current = await maintenance.evaluate(() => probe.readCleanupSnapshot());
    assert.equal(current.canDelete, false);
    assert.equal(await maintenance.evaluate(async () => { const current = await probe.readCleanupSnapshot(); try { await probe.deleteUnusedLocalMedia(current.files.filter((file) => !file.references.length)); return false; } catch { return true; } }), true);
    check('live editor prevents deletion of an unreferenced file, including unsaved work');
    await maintenance.evaluate((claim) => probe.probeStudioEditor(claim), claim);
    await maintenance.waitForFunction(() => probe.editorProbes().some((item) => item.state === 'occupied'));
    assert.equal((await raw(maintenance, 'local_media')).length, 2); check('native marker upgrade reports occupied and leaves original bytes intact');
    await editor.close(); await releaseClaims(maintenance);
    check('closing actual editor page lets pending native probe confirm connection release without timer');
    current = await maintenance.evaluate(() => probe.readCleanupSnapshot()); assert.equal(current.canDelete, true);
    assert.equal(await maintenance.evaluate(async () => { const current = await probe.readCleanupSnapshot(); try { await probe.deleteUnusedLocalMedia(current.files); return false; } catch { return true; } }), true);
    assert.equal((await raw(maintenance, 'local_media')).length, 2); check('mixed referenced/unreferenced deletion aborts entire batch');
    const removed = await maintenance.evaluate(async () => { const current = await probe.readCleanupSnapshot(); return probe.deleteUnusedLocalMedia(current.files.filter((file) => !file.references.length)); });
    assert.deepEqual(removed, { files: 1, bytes: 15 });
    assert.deepEqual((await raw(maintenance, 'local_media')).map((item) => item.id), [keys.first]);
    check('explicit deletion removes only unreferenced controlled original in one transaction', removed);
    const reload = await pageIn(context); await reload.evaluate(() => probe.ensureStudioEditor());
    await reload.reload(); await reload.addScriptTag({ path: path.join(temporary, 'probe.js') }); await reload.evaluate(() => probe.ensureStudioEditor());
    assert.equal((await raw(maintenance, 'editors')).length, 2);
    await maintenance.evaluate(async () => { (await probe.readCleanupSnapshot()).editors.forEach(probe.probeStudioEditor); });
    await maintenance.waitForFunction(async () => (await probe.readCleanupSnapshot()).editors.length === 1);
    await reload.evaluate(() => probe.leaveStudioEditor()); await releaseClaims(maintenance);
    assert.equal(await reload.evaluate(async () => { try { await probe.journalList('documents'); return false; } catch { return true; } }), true);
    check('reload stale claim is reclaimed by native proof; explicit leave fences further old-page writes');
    const unknown = { version: 1, id: 'editor-v1:missing-marker', markerDatabase: 'comfy-studio-editor:editor-v1:missing-marker', startedAt: 1 };
    await maintenance.evaluate((claim) => probe.rawJournalTransaction(['editors'], 'readwrite', (tx, result) => { tx.objectStore('editors').add(claim); result(); }), unknown);
    await maintenance.evaluate((claim) => probe.probeStudioEditor(claim), unknown);
    await maintenance.waitForFunction(() => probe.editorProbes().some((item) => item.state === 'uncertain'));
    assert.equal((await maintenance.evaluate(() => probe.readCleanupSnapshot())).canDelete, false);
    assert.equal(await maintenance.evaluate(async (name) => (await indexedDB.databases()).some((item) => item.name === name), unknown.markerDatabase), false);
    check('missing marker stays uncertain and no marker database is created');
    await maintenance.evaluate((id) => probe.rawJournalTransaction(['editors'], 'readwrite', (tx, result) => { tx.objectStore('editors').delete(id); result(); }), unknown.id);
    await maintenance.evaluate(() => probe.rawJournalTransaction(['intents'], 'readwrite', (tx, result) => { tx.objectStore('intents').add({ version: 99, id: 'unsupported' }); result(); }));
    assert.equal((await maintenance.evaluate(() => probe.readCleanupSnapshot())).canDelete, false);
    check('unknown task version conservatively blocks cleanup');
    await maintenance.evaluate(() => probe.rawJournalTransaction(['intents'], 'readwrite', (tx, result) => { tx.objectStore('intents').delete('unsupported'); result(); }));
    const otherMaintenance = await pageIn(context);
    // Two independent native clients cannot both delete a candidate from one old snapshot.
    const stagedEditor = await pageIn(context);
    const candidateId = await stagedEditor.evaluate(async () => { const id = probe.newManagedMediaKey('media'); await probe.addManagedMedia(id, new Blob(['race-original'])); await probe.leaveStudioEditor(); return id; });
    await releaseClaims(maintenance);
    const candidate = (await maintenance.evaluate(() => probe.readCleanupSnapshot())).files.find((file) => file.id === candidateId);
    const outcomes = await Promise.allSettled([maintenance, otherMaintenance].map((page) => page.evaluate((file) => probe.deleteUnusedLocalMedia([file]), candidate)));
    assert.equal(outcomes.filter((item) => item.status === 'fulfilled').length, 1);
    assert.equal(outcomes.filter((item) => item.status === 'rejected').length, 1);
    check('concurrent cleaners serialize; stale second delete refuses without partial deletion');
    const raceEditor = await pageIn(context);
    const raceKey = await raceEditor.evaluate(async () => { const id = probe.newManagedMediaKey('media'); await probe.addManagedMedia(id, new Blob(['registration-race'])); await probe.leaveStudioEditor(); return id; });
    await releaseClaims(maintenance); await raceEditor.close();
    const entrant = await pageIn(context), raceFile = (await maintenance.evaluate(() => probe.readCleanupSnapshot())).files.find((file) => file.id === raceKey);
    const race = await Promise.allSettled([
        entrant.evaluate(async (id) => { const blob = await probe.getManagedMediaBlob(id); if (!blob) return 'already-deleted'; const docs = await probe.journalList('documents'), doc = docs.find((item) => item.id.endsWith('asset_store')); await probe.saveDocument(doc.id, JSON.stringify({ version: 0, state: { assets: [{ id: 'race', data: { storageKey: id } }] } }), doc.revision); return 'saved-reference'; }, raceKey),
        maintenance.evaluate((file) => probe.deleteUnusedLocalMedia([file]), raceFile),
    ]);
    const after = await maintenance.evaluate(() => probe.readCleanupSnapshot());
    const assetDoc = (await raw(maintenance, 'documents')).find((item) => item.id.endsWith('asset_store'));
    assert(!(assetDoc.storageKeys.includes(raceKey) && !after.files.some((item) => item.id === raceKey)));
    assert.equal(race[0].status, 'fulfilled');
    check('new-editor registration versus deletion race leaves no saved dangling reference', race.map((item) => item.status === 'fulfilled' ? item.value : 'refused'));
    await entrant.close(); await releaseClaims(maintenance);
    const badContext = await contextIn(), bad = await pageIn(badContext); await seedV4(bad, true);
    assert.equal(await bad.evaluate(async () => { try { await probe.ensureStudioEditor(); return false; } catch { const dbs = await indexedDB.databases(); return dbs.find((item) => item.name === 'comfy-aggregator-studio:journal').version === 4; } }), true);
    check('unknown v4 store layout aborts upgrade and preserves version');
    const blockedContext = await contextIn(), holder = await pageIn(blockedContext), blocked = await pageIn(blockedContext); await seedV4(holder);
    await holder.evaluate(async () => { const request = indexedDB.open('comfy-aggregator-studio:journal', 4); window.held = await new Promise((resolve) => { request.onsuccess = () => resolve(request.result); }); window.held.onversionchange = () => undefined; });
    assert.equal(await blocked.evaluate(async () => { try { await probe.ensureStudioEditor(); return false; } catch (error) { return /阻塞/.test(error.message); } }), true);
    await holder.evaluate(() => window.held.close());
    await holder.waitForFunction(async () => (await indexedDB.databases()).find((item) => item.name === 'comfy-aggregator-studio:journal').version === 4);
    check('blocked journal upgrade reports refusal; abandoned late upgrade does not mutate v4');
    assert.deepEqual(report.api_requests, []); assert.deepEqual(report.page_errors, []); report.passed = true;
} catch (error) { report.error = { name: error.name, message: error.message, stack: error.stack }; }
finally {
    for (const context of contexts) await context.close(); if (browser) await browser.close(); if (server) await new Promise((resolve) => server.close(resolve));
    report.cleanup = { all_owned_contexts_closed: contexts.every((context) => !context.pages().length), browser_closed: !browser || !browser.isConnected(), fixture_server_closed: !server || !server.listening };
    const file = path.join(root, 'docs/research/studio-implementation/managed-cleanup-protocol-' + stamp + '.json'); await writeFile(file, JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({ file: path.relative(root, file).replaceAll('\\', '/'), passed: report.passed, checks: report.checks.length, cleanup: report.cleanup, error: report.error?.message }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean)) process.exitCode = 1;
