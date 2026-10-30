/* 137 real native browser UI; all API fixtures are synthetic and never call a GPU. */
import { createServer } from 'node:http';
import { readFile, readdir, mkdir, writeFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require('C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/, '$1')), '../..');
const root = decodeURIComponent(ROOT), evidence = path.join(root, 'aggregator-service/docs/evidence/studio-release-137');
const builds = (await readdir(evidence)).filter(name => name.startsWith('admin-build-')).sort();
const receipt = JSON.parse(await readFile(path.join(evidence, builds.at(-1)), 'utf8'));
assert(receipt.passed);
const dist = path.join(root, receipt.private_workspace, 'dist'), media = path.join(root, 'docs/media/studio-137');
const privateRoot = path.join(root, '.local/studio137-browser');
await mkdir(media, { recursive: true }); await mkdir(privateRoot, { recursive: true });
const sha = value => createHash('sha256').update(value).digest('hex');
for (const [name, digest] of Object.entries(receipt.dist_sha256)) assert.equal(sha(await readFile(path.join(dist, name))), digest);
const prefix = (kind, digit) => `${kind}_${digit.repeat(32)}`;
const project = prefix('prj', '2'), tenant = prefix('ten', '1'), release = prefix('wfr', '3'), workflow = prefix('wf', '4');
const gpu1 = prefix('gpu', '5'), gpu2 = prefix('gpu', '6'), modelId = prefix('mdl', '7');
const channels = [0, 1, 2].map((number) => ({ kind: 'channel', id: prefix('ch', String(number + 1)), name: ['合成设备 A · 主渠道', '合成设备 A · 同卡别名', '合成设备 B'][number], gpu_id: number < 2 ? gpu1 : gpu2, enabled: true, draining: false, revision: 1 }));
let model = { id: modelId, tenant_id: tenant, project_id: project, alias: 'synthetic-balanced-image', task_type: 'text_to_image', release_id: release, channel_ids: channels.map(row => row.id), defaults: { size: '1024x1024', quality: 'standard' }, revision: 1, published: false, documentation: {}, routing: { policy: 'balanced_ready', weights: { [gpu1]: 2, [gpu2]: 1 } } };
const scope = { tenant_id: tenant, tenant_name: '合成租户', tenant_enabled: true, project_id: project, project_name: '合成均衡验证', project_enabled: true };
const session = { user: { id: prefix('usr', '8'), username: 'synthetic-review', role: 'administrator', enabled: true, revision: 1 }, csrf_token: 'synthetic-only-no-real-credential', permissions: ['models:read', 'models:write', 'models:publish', 'jobs:read', 'scopes:read'], scopes: [scope], environment: 'test', storage_backend: 'local_development' };
let failPool = false, holdPool = false, held = [], writes = [], apiCalls = [];
const checks = [], errors = [], pages = [];
const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1');
    const name = decodeURIComponent(url.pathname).replace(/^\/admin\/?/, '');
    const candidate = path.resolve(dist, name);
    assert(candidate.startsWith(dist + path.sep) || candidate === dist);
    let file = candidate;
    try { if (!(await stat(file)).isFile()) file = path.join(dist, 'index.html'); } catch { file = path.join(dist, 'index.html'); }
    const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png' }[path.extname(file)] ?? 'application/octet-stream';
    response.writeHead(200, { 'Content-Type': type }); response.end(await readFile(file));
  } catch { response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser, context, videoPath;
try {
  browser = await chromium.launch({ executablePath: 'C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe', headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: privateRoot, size: { width: 1440, height: 1000 } }, reducedMotion: 'reduce' });
  await context.route('**/admin/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), endpoint = url.pathname, method = req.method();
    apiCalls.push({ path: endpoint, method });
    let body;
    if (endpoint === '/admin/api/v1/session') body = session;
    else if (endpoint === '/admin/api/v1/scopes/options') body = { data: [scope], page: 1, has_more: false };
    else if (endpoint === '/admin/api/v1/model-mapping-options') {
      const kind = url.searchParams.get('kind');
      const data = kind === 'channel' ? channels : kind === 'release' ? [{ kind, id: release, workflow_id: workflow, version: 1, task_type: 'text_to_image', validated: true }] : kind === 'workflow' ? [{ kind, id: workflow, name: '合成冻结 Workflow', task_type: 'text_to_image', archived: false }] : [{ kind: 'preset', id: 'preset_0', release_id: release, task_type: 'text_to_image', defaults: { n: 1, size: '1024x1024', quality: 'standard' }, usable: true }];
      body = { kind, project_id: project, data, snapshot_at: Math.floor(Date.now() / 1000), total: data.length, page: 1, has_more: false };
    } else if (endpoint === `/admin/api/v1/public-models/${modelId}/device-pool`) {
      if (holdPool) { held.push(route); return; }
      if (failPool) { failPool = false; await route.fulfill({ status: 503, json: { error: { message: '合成统计暂不可用', type: 'service_unavailable' } } }); return; }
      body = { model_id: modelId, model_revision: model.revision, policy: model.routing.policy, pool_id: model.routing.policy === 'balanced_ready' ? 'a'.repeat(64) : null, sequence: model.routing.policy === 'balanced_ready' ? 30 : 0, members: model.routing.policy === 'balanced_ready' ? [{ gpu_id: gpu1, weight: model.routing.weights[gpu1] ?? 1, assigned_count: 20, last_sequence: 30 }, { gpu_id: gpu2, weight: model.routing.weights[gpu2] ?? 1, assigned_count: 10, last_sequence: 29 }] : [] };
    } else if (endpoint === `/admin/api/v1/public-models/${modelId}` && method === 'PATCH') {
      assert.equal(req.headers()['x-csrf-token'], session.csrf_token);
      assert(req.headers()['idempotency-key']);
      body = req.postDataJSON(); writes.push(structuredClone(body));
      assert.equal(body.expected_revision, model.revision);
      model = { ...model, ...body, revision: model.revision + 1, published: false }; delete model.expected_revision; body = model;
    } else if (endpoint === '/admin/api/v1/public-models') body = { data: [model], page: 1, has_more: false, total: 1, snapshot_at: Math.floor(Date.now() / 1000) };
    else { errors.push(`unhandled:${method}:${endpoint}`); await route.fulfill({ status: 404, json: { error: { message: 'Unexpected synthetic endpoint' } } }); return; }
    await route.fulfill({ json: body });
  });
  const page = await context.newPage(); pages.push(page); page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin + '/admin/models');
  await page.getByRole('button', { name: '编辑映射', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '编辑模型映射', exact: true });
  await drawer.getByRole('spinbutton', { name: '设备权重 ' + gpu1 }).waitFor();
  assert.equal(await drawer.getByRole('spinbutton').count(), 2);
  assert.equal(await drawer.getByRole('spinbutton', { name: '设备权重 ' + gpu1 }).inputValue(), '2');
  await drawer.getByRole('spinbutton', { name: '设备权重 ' + gpu1 }).fill('3');
  await page.screenshot({ path: path.join(media, 'device-routing-desktop.png') });
  checks.push('three mapped channels render two physical GPU weight controls');
  await drawer.getByRole('button', { name: '保存映射', exact: true }).click();
  await drawer.waitFor({ state: 'hidden' });
  assert.deepEqual(writes[0].routing, { policy: 'balanced_ready', weights: { [gpu1]: 3, [gpu2]: 1 } });
  checks.push('graphical edit sends frozen policy, deduplicated weights and revision with CSRF');
  await page.getByRole('button', { name: '设备分配', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '设备分配统计', exact: true });
  await dialog.getByText('均衡池累计分配 30 次', { exact: false }).waitFor();
  await page.screenshot({ path: path.join(media, 'device-pool-desktop.png') });
  failPool = true;
  await dialog.getByRole('button', { name: '刷新统计', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  await dialog.getByRole('button', { name: '刷新统计', exact: true }).click();
  await dialog.getByRole('alert').waitFor({ state: 'hidden' });
  checks.push('read-only statistics failure and explicit recovery do not create generation requests');
  holdPool = true;
  await dialog.getByRole('button', { name: '刷新统计', exact: true }).click();
  await page.waitForFunction(() => true);
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  for (const route of held) { try { await route.fulfill({ json: { marker: 'stale' } }); } catch {} } held = []; holdPool = false;
  checks.push('closing statistics aborts owned read and rejects late result');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '编辑映射', exact: true }).click();
  await drawer.getByRole('spinbutton', { name: '设备权重 ' + gpu1 }).waitFor();
  await drawer.getByLabel('设备调度策略', { exact: true }).focus();
  await drawer.getByLabel('设备调度策略', { exact: true }).press('Enter');
  await page.getByRole('option', { name: '就绪设备领取（原策略）', exact: true }).click();
  assert.equal(await drawer.getByRole('spinbutton').count(), 0);
  await drawer.getByLabel('设备调度策略', { exact: true }).focus();
  await drawer.getByLabel('设备调度策略', { exact: true }).press('Enter');
  await page.getByRole('option', { name: '就绪设备加权均衡', exact: true }).click();
  await drawer.getByRole('spinbutton', { name: '设备权重 ' + gpu1 }).scrollIntoViewIfNeeded();
  const box = await drawer.boundingBox(); assert(box.width <= 391);
  await page.screenshot({ path: path.join(media, 'device-routing-390.png') });
  await drawer.getByRole('button', { name: '取消', exact: true }).click();
  checks.push('390px viewport supports policy switching and graphical weights; no physical phone claim');
  assert.equal(writes.length, 1);
  assert(!apiCalls.some(call => /\/v1\/(jobs|images|videos|audio)/.test(call.path)));
  assert.equal(errors.length, 0, errors.join('\n'));
  videoPath = await page.video().path();
  await context.close(); context = null;
  const result = { passed: true, at: new Date().toISOString(), scope: 'native Chromium, synthetic API fixtures; not physical phones or GPUs', checks, generated_dist_verified: true, admin_build_receipt: builds.at(-1), screenshots: Object.fromEntries(await Promise.all(['device-routing-desktop.png', 'device-pool-desktop.png', 'device-routing-390.png'].map(async name => [name, sha(await readFile(path.join(media, name)))]))), owned_browser_closed: true, owned_server_closed: true, private_video: path.relative(root, videoPath).replaceAll('\\', '/'), virtualization_used: false };
  await writeFile(path.join(evidence, 'admin-browser-1371.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify({ passed: true, checks: checks.length, screenshots: Object.keys(result.screenshots).length, private_video: result.private_video }));
} finally {
  for (const page of pages) if (!page.isClosed()) await page.close().catch(() => {});
  if (context) await context.close();
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
