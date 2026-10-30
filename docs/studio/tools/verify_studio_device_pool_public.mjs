/* Real deployed Admin reads only; the owned session and browser always close. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFile, mkdir, writeFile, access } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const attempt = process.argv[2] ?? '1371';
assert(/^137[1-9]$/.test(attempt));
const reportPath = path.join(root, `aggregator-service/docs/evidence/studio-release-137/public-admin-${attempt}.json`);
try { await access(reportPath); throw new Error('Existing immutable report'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
const media = path.join(root, 'docs/media/studio-137');
await mkdir(media, { recursive: true });
const privateRoot = path.join(root, '.local/studio137-public');
await mkdir(privateRoot, { recursive: true });
const credentials = JSON.parse((await readFile(path.join(root, '.local/studio-ops/admin-access.json'), 'utf8')).replace(/^\uFEFF/, ''));
const origin = new URL(credentials.url).origin;
assert.equal(origin, 'http://43.165.167.179:39443');
let browser, context, csrf, passed = false;
let closedSession = false, closedContext = false, closedBrowser = false;
const checks = [], screenshots = [], pageErrors = [], blocked = [], poolReads = [];
let activityPosts = 0;
async function capture(page, name) {
  if (attempt !== '1371') name = name.replace('.png', `-${attempt}.png`);
  const file = path.join(media, name);
  await page.screenshot({ path: file });
  const bytes = await readFile(file);
  screenshots.push({ file: 'docs/media/studio-137/' + name, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
}
try {
  browser = await chromium.launch({ executablePath: 'C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe', headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    const sessionActivity = url.pathname === '/admin/api/v1/session/activity' && method === 'POST';
    if (sessionActivity) activityPosts += 1;
    if (url.origin !== origin || url.pathname.startsWith('/v1/') || (url.pathname.startsWith('/admin/api/') && !['GET', 'HEAD'].includes(method) && !sessionActivity)) {
      blocked.push({ path: url.pathname, method }); await route.abort(); return;
    }
    await route.continue();
  });
  const login = await context.request.post(origin + '/admin/api/v1/session', { headers: { Origin: origin }, data: { username: credentials.username, password: credentials.password } });
  assert.equal(login.status(), 200);
  csrf = (await login.json()).csrf_token;
  assert.equal(typeof csrf, 'string');
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.name));
  page.on('response', response => { if (new URL(response.url()).pathname.endsWith('/device-pool')) poolReads.push({ status: response.status() }); });
  await page.goto(origin + '/admin/models', { waitUntil: 'domcontentloaded' });
  const poolButton = page.getByRole('button', { name: '设备分配', exact: true }).first();
  await poolButton.waitFor({ state: 'visible', timeout: 30000 });
  await poolButton.click();
  const dialog = page.getByRole('dialog', { name: '设备分配统计' });
  await dialog.getByText('当前采用原领取策略。', { exact: false }).waitFor({ timeout: 15000 });
  await dialog.getByRole('button', { name: '刷新统计', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[role="dialog"] .el-loading-mask') == null, null, { timeout: 15000 });
  assert(poolReads.length >= 2 && poolReads.every(value => value.status === 200));
  await capture(page, 'live-device-pool-desktop.png');
  checks.push('real deployed desktop Admin reads legacy pool and refreshes with HTTP 200');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '编辑映射', exact: true }).first().click();
  const drawer = page.getByRole('dialog', { name: '编辑模型映射' });
  await drawer.getByLabel('设备调度策略', { exact: true }).waitFor({ state: 'visible', timeout: 15000 });
  await drawer.getByText('均衡按物理 GPU 分配', { exact: false }).waitFor();
  await capture(page, 'live-device-routing-desktop.png');
  checks.push('real model mapping loads graphical routing editor without saving or changing the model');
  await drawer.getByRole('button', { name: '取消', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '设备分配', exact: true }).first().click();
  await dialog.getByText('当前采用原领取策略。', { exact: false }).waitFor({ timeout: 15000 });
  await capture(page, 'live-device-pool-390.png');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  checks.push('390px deployed Admin opens and closes real pool statistics; physical phone not tested');
  assert.equal(pageErrors.length, 0);
  assert.equal(blocked.length, 0);
  passed = true;
} catch (error) {
  await writeFile(path.join(privateRoot, 'failure.txt'), String(error.stack));
} finally {
  if (context && csrf) {
    try {
      const response = await context.request.delete(origin + '/admin/api/v1/session', { headers: { Origin: origin, 'X-CSRF-Token': csrf } });
      closedSession = response.ok();
    } catch { closedSession = false; }
  }
  if (context) { await context.close(); closedContext = true; }
  if (browser) { await browser.close(); closedBrowser = true; }
}
const result = { passed: passed && closedSession && closedContext && closedBrowser, at: new Date().toISOString(), scope: 'real HTTP/Admin read-only pages; only session login/activity/logout writes, native desktop Chromium with 390px viewport, not physical GPU balancing or phone acceptance', checks, screenshots, pool_reads: poolReads, page_errors: pageErrors, blocked_mutations: blocked, session_activity_posts: activityPosts, generation_posts: 0, production_model_or_key_mutations: 0, cleanup: { admin_session_closed: closedSession, owned_context_closed: closedContext, owned_browser_closed: closedBrowser }, virtualization_used: false, new_feature_acceptance: 'pending-test' };
await writeFile(reportPath, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify(result));
if (!result.passed) process.exitCode = 1;
