/** Probe the isolated baseline on a real, non-localhost HTTP origin. */
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, extname, resolve, sep } from 'node:path';
import { readFile, stat, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const dist = resolve(root, '.local/assessment-native/probe/web/dist');
const evidence = resolve(root, 'docs/research/fusion-assessment-2026-10-30');
const assets = resolve(root, 'docs/assets/fusion-assessment');
const require = createRequire(import.meta.url);
const { chromium } = require('C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const executablePath = 'C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
const observeMobile = process.argv.includes('--observe-mobile');
const normalEntry = process.argv.includes('--normal-entry');
const diagnostic = process.argv.includes('--diagnostic');
const closedIdbRun = process.argv.includes('--closed-idb');
const runLabel = process.argv.find((value) => value.startsWith('--run-label='))?.slice('--run-label='.length) || '';
if (runLabel && !/^[a-z0-9][a-z0-9-]{0,63}$/i.test(runLabel)) throw new Error('Use a short alphanumeric run label with hyphens.');
const suffix = (normalEntry ? '-normal' : observeMobile ? '-observation' : '') + (diagnostic ? '-diagnostic' : '') + (closedIdbRun ? '-closed-idb' : '') + (runLabel ? `-${runLabel}` : '');
const outputReport = resolve(evidence, `native-browser${suffix}.json`);
let outputExists = true;
try { await stat(outputReport); }
catch (error) { if (error.code === 'ENOENT') outputExists = false; else throw error; }
if (outputExists) {
  process.stderr.write('Evidence already exists; use a new --run-label=... to preserve the previous run.\n');
  process.exit(1);
}
const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2' };
const report = {
  started_at: new Date().toISOString(),
  scope: 'isolated patched upstream baseline on plain HTTP; no integration, real phone, production API or GPU execution',
  browser: executablePath,
  observation_mode: observeMobile,
  creation_entry: normalEntry ? 'library create button, without Agent mode query' : '/canvas?mode=new (upstream Agent entry)',
  idb_probe_closes_before_resolving: true,
  viewport_problems: [],
  reload_failures: [],
  pages: [], errors: [], blocked_external_urls: [],
  cleanup: { contexts_closed: false, browser_closed: false, server_closed: false },
};
let browser;
const contexts = [];
let server;
let failure;
let activePage;
let activeLabel;

async function snapshot(page, label) {
  const observation = await page.evaluate(() => {
    const rect = (node) => {
      if (!node) return null;
      const r = node.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
    };
    const toolbar = document.querySelector('[data-canvas-bottom-toolbar]');
    return {
      url: location.href, title: document.title,
      viewport: { width: innerWidth, height: innerHeight },
      is_secure_context: isSecureContext,
      features: {
        getRandomValues: typeof crypto?.getRandomValues === 'function',
        subtle: typeof crypto?.subtle !== 'undefined',
        randomUUID: typeof crypto?.randomUUID === 'function',
        storagePersist: typeof navigator.storage?.persist === 'function',
        indexedDB: typeof indexedDB !== 'undefined',
      },
      document_scroll_width: document.documentElement.scrollWidth,
      body_scroll_width: document.body.scrollWidth,
      page_horizontal_overflow: document.documentElement.scrollWidth > innerWidth,
      toolbar: rect(toolbar),
      toolbar_children: toolbar ? [...toolbar.children].map(rect) : [],
      laid_out_buttons: [...document.querySelectorAll('button')].filter((n) => n.getBoundingClientRect().height > 0).map((n) => ({ text: (n.innerText || n.getAttribute('aria-label') || n.title || '').slice(0, 80), ...rect(n) })).slice(0, 100),
      body_text: document.body.innerText.slice(0, 1800),
    };
  });
  const target = resolve(assets, `${label}${suffix}.png`);
  await page.screenshot({ path: target, fullPage: false, animations: 'disabled' });
  observation.screenshot = `docs/assets/fusion-assessment/${label}${suffix}.png`;
  observation.screenshot_sha256 = createHash('sha256').update(await readFile(target)).digest('hex');
  report.pages.push({ label, ...observation });
  if (observation.toolbar && (observation.toolbar.width <= 0 || observation.toolbar_children.some((r) => r.x < 0 || r.right > observation.viewport.width))) {
    report.viewport_problems.push({ label, reason: 'canvas toolbar has zero width or child controls extend outside the viewport', toolbar: observation.toolbar, children: observation.toolbar_children });
  }
  return observation;
}

try {
  await stat(resolve(dist, 'index.html'));
  await mkdir(evidence, { recursive: true });
  await mkdir(assets, { recursive: true });
  server = createServer(async (req, res) => {
    try {
      const path = decodeURIComponent(new URL(req.url || '/', 'http://probe.invalid').pathname);
      let target = resolve(dist, `.${path}`);
      if (target !== dist && !target.startsWith(dist + sep)) {
        res.writeHead(403).end(); return;
      }
      try { if (!(await stat(target)).isFile()) target = resolve(dist, 'index.html'); }
      catch { if (extname(path)) { res.writeHead(404).end(); return; } target = resolve(dist, 'index.html'); }
      const bytes = await readFile(target);
      res.writeHead(200, { 'Content-Type': `${mime[extname(target)] || 'application/octet-stream'}`, 'Cache-Control': 'no-store' });
      res.end(bytes);
    } catch (error) { res.writeHead(500).end(String(error)); }
  });
  await new Promise((yes, no) => { server.once('error', no); server.listen(0, '127.0.0.1', yes); });
  const origin = `http://canvas-probe.test:${server.address().port}`;
  report.origin = origin;
  browser = await chromium.launch({ executablePath, headless: true, args: ['--host-resolver-rules=MAP canvas-probe.test 127.0.0.1', '--no-proxy-server'] });
  for (const [label, viewport, touch] of [['desktop', { width: 1440, height: 1000 }, false], ['mobile', { width: 390, height: 844 }, true]]) {
    const context = await browser.newContext({ viewport, isMobile: touch, hasTouch: touch, locale: 'zh-CN', reducedMotion: 'reduce' });
    contexts.push(context);
    await context.route('**/*', async (route) => {
      const url = route.request().url();
      if (url.startsWith(origin + '/') || url.startsWith('blob:') || url.startsWith('data:')) await route.continue();
      else { report.blocked_external_urls.push(url); await route.abort('blockedbyclient'); }
    });
    const page = await context.newPage();
    activePage = page;
    activeLabel = label;
    page.on('pageerror', (error) => report.errors.push({ label, type: 'pageerror', message: String(error) }));
    page.on('console', (message) => { if (message.type() === 'error') report.errors.push({ label, type: 'console', message: message.text().slice(0, 700) }); });
    await page.goto(origin + '/canvas', { waitUntil: 'networkidle' });
    await page.locator('main').first().waitFor({ state: 'visible' });
    await snapshot(page, `${label}-library`);
    if (normalEntry) await page.getByRole('button', { name: '新建画布', exact: true }).first().click();
    else await page.goto(origin + '/canvas?mode=new', { waitUntil: 'networkidle' });
    await page.waitForURL(/\/canvas\/[^/?#]+/);
    await page.locator('[data-canvas-bottom-toolbar]').waitFor({ state: observeMobile && touch ? 'attached' : 'visible' });
    if (!(await page.locator('[data-canvas-bottom-toolbar]').isVisible())) {
      report.viewport_problems.push({ label, reason: 'bottom toolbar is present but not visible' });
    }
    const createdUrl = page.url();
    await snapshot(page, `${label}-canvas`);
    // Await the baseline's documented deferred save via observable IDB data,
    // rather than treating a fixed delay as evidence of persistence.
    const createdId = new URL(createdUrl).pathname.split('/').pop();
    await page.waitForFunction(async (projectId) => {
      return await new Promise((yes) => {
        const opening = indexedDB.open('infinite-canvas');
        opening.onerror = () => yes(false);
        opening.onsuccess = () => {
          const db = opening.result;
          if (!db.objectStoreNames.contains('app_state')) { db.close(); yes(false); return; }
          const tx = db.transaction('app_state', 'readonly');
          const read = tx.objectStore('app_state').get('infinite-canvas:canvas_store');
          let found = false;
          read.onsuccess = () => {
            try { found = JSON.parse(read.result).state.projects.some((project) => project.id === projectId); }
            catch { found = false; }
          };
          tx.oncomplete = () => { db.close(); yes(found); };
          tx.onabort = tx.onerror = () => { db.close(); yes(false); };
        };
      });
    }, createdId);
    await page.reload({ waitUntil: 'networkidle' });
    let toolbarRestored = false;
    try {
      await page.locator('[data-canvas-bottom-toolbar]').waitFor({ state: observeMobile && touch ? 'attached' : 'visible' });
      toolbarRestored = true;
    } catch (error) {
      if (!diagnostic) throw error;
      report.reload_failures.push({ label, url: page.url(), expected_url: createdUrl, error: String(error) });
      await snapshot(page, `${label}-reload-failure`);
    }
    const persistedAfterReload = await page.evaluate(async () => await new Promise((yes) => {
      const opening = indexedDB.open('infinite-canvas');
      opening.onerror = () => yes({ error: 'database_open_failed' });
      opening.onsuccess = () => {
        const db = opening.result;
        if (!db.objectStoreNames.contains('app_state')) { db.close(); yes({ error: 'app_state_missing' }); return; }
        const tx = db.transaction('app_state', 'readonly');
        const read = tx.objectStore('app_state').get('infinite-canvas:canvas_store');
        let value;
        read.onsuccess = () => { try { value = { project_ids: JSON.parse(read.result).state.projects.map((project) => project.id) }; } catch { value = { error: 'invalid_saved_state' }; } };
        tx.oncomplete = () => { db.close(); yes(value); };
        tx.onabort = tx.onerror = () => { db.close(); yes({ error: 'read_transaction_failed' }); };
      };
    }));
    report.pages.push({ label: `${label}-reload`, url: page.url(), same_project: page.url() === createdUrl, indexeddb_saved_project_found_before_reload: true, indexeddb_state_after_reload: persistedAfterReload, toolbar_restored: toolbarRestored });
    await page.goto(origin + '/config', { waitUntil: 'networkidle' });
    await page.locator('main').first().waitFor({ state: 'visible' });
    await snapshot(page, `${label}-config`);
    await page.close();
    await context.close();
  }
  report.baseline_routes_and_reload_passed = report.reload_failures.length === 0;
  report.all_viewport_controls_passed = report.viewport_problems.length === 0 && report.reload_failures.length === 0;
} catch (error) {
  failure = error;
  report.failure = { message: String(error), stack: error.stack };
  report.baseline_routes_and_reload_passed = false;
  if (activePage && !activePage.isClosed()) {
    try { await snapshot(activePage, `${activeLabel}-failure`); }
    catch (captureError) { report.failure.capture_error = String(captureError); }
  }
} finally {
  const closed = await Promise.allSettled(contexts.map((context) => context.close()));
  report.cleanup.contexts_closed = closed.every((item) => item.status === 'fulfilled');
  if (browser) { try { await browser.close(); report.cleanup.browser_closed = true; } catch (error) { report.cleanup.browser_error = String(error); } }
  if (server?.listening) {
    server.closeAllConnections();
    await new Promise((yes) => server.close(yes));
    report.cleanup.server_closed = true;
  }
  report.finished_at = new Date().toISOString();
  report.blocked_external_urls = [...new Set(report.blocked_external_urls)];
  await mkdir(evidence, { recursive: true });
  const target = outputReport;
  await writeFile(target, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ report: target, routes_and_reload: report.baseline_routes_and_reload_passed, controls_passed: report.all_viewport_controls_passed, pages: report.pages.length, viewport_problems: report.viewport_problems, reload_failures: report.reload_failures, page_error_count: report.errors.filter((e) => e.type === 'pageerror').length, console_error_count: report.errors.filter((e) => e.type === 'console').length, cleanup: report.cleanup, failure: report.failure }, null, 2) + '\n');
}
if (failure) process.exitCode = 1;
