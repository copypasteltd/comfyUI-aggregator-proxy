/** Bounded observation of real Web loading and native IndexedDB; no product/API changes. */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdir, readFile, realpath, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../../', import.meta.url)), web = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(web, 'package.json'));
const sdk = process.argv.find((value) => value.startsWith('--sdk='))?.slice(6) || 'C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright';
const playwright = require(sdk), engine = process.argv[2], ascii = process.argv.includes('--ascii'), persistent = process.argv.includes('--persistent');
assert(['chromium', 'firefox', 'webkit'].includes(engine));
const origin = 'http://43.165.167.179:39445', label = engine + '-' + (ascii ? 'ascii-' : 'unicode-') + (persistent ? 'persistent-' : '') + randomUUID();
const temporary = path.join(root, '.local/studio147', label), media = path.join(root, 'docs/media/studio-147', label);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
let runtimeDir = temporary;
if (ascii) {
    const junction = 'E:/dev/sandbox/.comfy-studio-native';
    assert.equal(await realpath(junction), await realpath(path.join(root, '.local')));
    runtimeDir = path.join(junction, 'studio147', label); assert.equal(await realpath(runtimeDir), await realpath(temporary));
}
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/studio-journal-db";', resolveDir: web }, bundle: true, platform: 'browser', format: 'esm', outfile: path.join(temporary, 'probe.mjs'), logLevel: 'warning' });
const report = { passed: false, scope: 'Read-only public Web startup plus synthetic native IndexedDB fixture; bounded diagnostic observation only, no generated task, product setting or API change.', browser_engine: engine, ascii_runtime_path: ascii, persistent_owned_profile: persistent, playwright_version: require(sdk + '/package.json').version, steps: [], page_errors: [], console_errors: [], requests: [], snapshots: [], cleanup: {} };
let browser, context; const pages = [];
async function observe(name, action) {
    console.log(JSON.stringify({ step: name, state: 'started', engine, ascii }));
    const start = Date.now(); let timer;
    try { const value = await Promise.race([action(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Diagnostic observation window ended: ' + name)), 20000); })]); report.steps.push({ name, passed: true, milliseconds: Date.now() - start, value }); return value; }
    catch (error) { report.steps.push({ name, passed: false, milliseconds: Date.now() - start, error: error.message }); throw error; }
    finally { clearTimeout(timer); }
}
async function pageIn(url) {
    const page = await context.newPage(); pages.push(page);
    page.on('pageerror', (error) => report.page_errors.push(error.message));
    page.on('console', (item) => { if (item.type() === 'error') report.console_errors.push(item.text()); });
    await page.goto(origin + url, { waitUntil: 'domcontentloaded' }); return page;
}
async function snapshot(page) {
    return page.evaluate(async () => ({ url: location.pathname, title: document.title, text: document.body.innerText.slice(0, 3000), events: window.nativeEvents || [], databases: typeof indexedDB.databases === 'function' ? await indexedDB.databases() : null }));
}
try {
    process.env.TEMP = runtimeDir; process.env.TMP = runtimeDir;
    const executablePath = { chromium: 'C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe', firefox: 'C:/Users/carzy/AppData/Local/ms-playwright/firefox-1543/firefox/firefox.exe', webkit: 'C:/Users/carzy/AppData/Local/ms-playwright/webkit-2359/Playwright.exe' }[engine];
    report.executable = executablePath;
    const options = { executablePath, headless: true, viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' };
    if (persistent) {
        context = await observe('launch owned persistent native browser', () => playwright[engine].launchPersistentContext(path.join(runtimeDir, 'profile'), options));
        browser = context.browser();
    } else {
        browser = await observe('launch owned native browser', () => playwright[engine].launch({ executablePath, headless: true }));
        context = await browser.newContext(options);
    }
    // Do not serialize browser/context objects in diagnostic steps.
    report.steps.at(-1).value = undefined; report.browser_version = browser?.version();
    context.setDefaultTimeout(10000); context.setDefaultNavigationTimeout(15000);
    await context.route('**/*', async (route) => {
        const url = new URL(route.request().url()); report.requests.push({ method: route.request().method(), origin: url.origin, path: url.pathname });
        if (url.origin !== origin || /\/(?:v1|api|admin|internal)\//.test(url.pathname)) return route.abort();
        if (url.pathname === '/native-probe.mjs') return route.fulfill({ status: 200, contentType: 'application/javascript', body: await readFile(path.join(temporary, 'probe.mjs')) });
        if (url.pathname === '/native-blank') return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><title>Owned Web browser diagnostic</title>' });
        return route.continue();
    });
    const probe = await observe('load owned blank page', () => pageIn('/native-blank')); report.steps.at(-1).value = undefined;
    await observe('read browser Web features', () => probe.evaluate(() => ({ secure: isSecureContext, indexedDB: typeof indexedDB, uuid: typeof crypto.randomUUID, random: typeof crypto.getRandomValues, userAgent: navigator.userAgent })));
    await probe.evaluate(() => {
        window.nativeEvents = [];
        const transaction = IDBDatabase.prototype.transaction;
        IDBDatabase.prototype.transaction = function (...args) {
            const tx = transaction.apply(this, args), names = [...tx.objectStoreNames];
            window.nativeEvents.push({ database: this.name, phase: 'transaction-open', mode: tx.mode, stores: names });
            for (const phase of ['complete', 'abort', 'error']) tx.addEventListener(phase, () => window.nativeEvents.push({ database: this.name, phase: 'transaction-' + phase, mode: tx.mode, stores: names, error: tx.error?.name }));
            return tx;
        };
        const add = IDBObjectStore.prototype.add;
        IDBObjectStore.prototype.add = function (...args) {
            const request = add.apply(this, args), store = this.name;
            window.nativeEvents.push({ store, phase: 'add-request' });
            for (const phase of ['success', 'error']) request.addEventListener(phase, () => window.nativeEvents.push({ store, phase: 'add-' + phase, error: phase === 'error' ? request.error?.name : undefined }));
            return request;
        };
        const native = indexedDB.open.bind(indexedDB);
        indexedDB.open = (...args) => {
            window.nativeEvents.push({ name: args[0], phase: 'open-request' });
            const request = native(...args);
            for (const phase of ['upgradeneeded', 'success', 'error', 'blocked']) request.addEventListener(phase, () => window.nativeEvents.push({ name: args[0], phase, error: phase === 'error' ? request.error?.name : undefined }));
            return request;
        };
    });
    await observe('import exact journal helper bundle', () => probe.evaluate(async () => { window.probe = await import('/native-probe.mjs'); return Object.keys(window.probe); }));
    await observe('commit native fixture documents and original', () => probe.evaluate(async () => {
        await window.probe.rawJournalTransaction(['documents', 'local_media'], 'readwrite', (tx, result) => {
            for (const [id, state] of [['infinite-canvas:canvas_store', { projects: [], deletedProjects: [] }], ['infinite-canvas:asset_store', { assets: [] }]]) tx.objectStore('documents').add({ version: 1, id, revision: 1, value: JSON.stringify({ version: 0, state }), storageKeys: [], legacyValue: null });
            const blob = new Blob(['synthetic-Web-browser-fixture']); tx.objectStore('local_media').add({ version: 1, id: 'file:managed-v1:engine-fixture', kind: 'media', blob, bytes: blob.size, mimeType: blob.type, createdAt: 1 }); result();
        }); return true;
    }));
    const first = await observe('load actual first Web library', () => pageIn('/studio/')); report.steps.at(-1).value = undefined;
    await observe('first Web navigation rendered', () => first.getByRole('link', { name: '连接服务', exact: true }).waitFor());
    const second = await observe('load actual second Web library', () => pageIn('/studio/')); report.steps.at(-1).value = undefined;
    await observe('second Web navigation rendered', () => second.getByRole('link', { name: '连接服务', exact: true }).waitFor());
    await observe('read actual native editor and document rows', () => probe.evaluate(() => window.probe.rawJournalTransaction(['editors', 'documents', 'local_media'], 'readonly', (tx, result) => {
        const values = {}; let count = 0;
        for (const name of ['editors', 'documents', 'local_media']) { const request = tx.objectStore(name).getAll(); request.onsuccess = () => { values[name] = request.result.map((row) => ({ ...row, blob: row.blob ? { bytes: row.blob.size } : undefined })); if (++count === 3) result(values); }; }
    })));
    report.passed = true;
} catch (error) { report.error = { name: error.name, message: error.message }; }
finally {
    for (const page of pages) if (!page.isClosed()) {
        try { report.snapshots.push(await observe('capture diagnostic page state', () => snapshot(page))); } catch {}
        try { const file = path.join(media, 'page-' + pages.indexOf(page) + '.png'); await page.screenshot({ path: file, timeout: 5000 }); report.snapshots.at(-1).screenshot = { file: path.relative(root, file).replaceAll('\\', '/'), bytes: (await stat(file)).size, sha256: createHash('sha256').update(await readFile(file)).digest('hex') }; } catch {}
    }
    report.close_errors = [];
    if (context) try { await context.close(); } catch (error) { report.close_errors.push(error.message); }
    if (browser) try { await browser.close(); } catch (error) { report.close_errors.push(error.message); }
    report.cleanup = { pages_closed: pages.every((page) => page.isClosed()), context_closed: !context || !context.pages().length, browser_closed: !browser || !browser.isConnected() };
    const file = path.join(root, 'docs/research/studio-implementation/Web-engine-diagnostic-' + label + '.json');
    await writeFile(file, JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({ passed: report.passed, report: path.relative(root, file).replaceAll('\\', '/'), error: report.error, cleanup: report.cleanup }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean)) process.exitCode = 1;
