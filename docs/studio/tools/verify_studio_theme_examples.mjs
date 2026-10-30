/** Native owned browser: theme persistence and eight editable example flows. Synthetic API only. */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile, stat, copyFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url)), source = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(source, 'package.json')), sha = (data) => createHash('sha256').update(data).digest('hex');
const relative = (file) => path.relative(root, file).replaceAll('\\', '/');
const buildFile = path.resolve(root, process.argv[2]), build = JSON.parse(await readFile(buildFile, 'utf8'));
assert(build.passed && build.source_before === build.source_after);
const clean = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8'));
const dist = path.join(root, clean.private_logs, 'repository/web/dist'), publicMode = process.argv.includes('--public'), galleryMode = process.argv.includes('--gallery-only');
const stamp = `${galleryMode ? 'gallery-' : ''}${publicMode ? 'public-' : ''}${webEngine}-${new Date().toISOString().replaceAll(/[:.]/g, '-')}`;
const temporary = path.join(root, '.local/studio151', stamp), media = path.join(root, 'docs/media/studio-151', stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
const report = { passed: false, at: new Date().toISOString(), build_report: relative(buildFile), source_sha256: build.source_after, public_static_site: publicMode, checks: [], screenshots: [], requests: [], submissions: [], errors: [], page_errors: [], cleanup: {}, scope: 'Actual theme and editable example Web flows with synthetic API/media; no real GPU generation, fleet, physical touch or full browser acceptance.', api_transport: publicMode ? 'Explicit test routing to owned synthetic API; not production CORS/authentication proof' : 'Native ordinary HTTP on two different origins, no business interception', product_timeouts_changed: false };
const fixture = JSON.parse(await readFile(path.join(source, 'tests/fixtures/catalog.json'), 'utf8'));
const Ajv2020 = require('ajv/dist/2020'), ajv = new Ajv2020({ strict: false, allErrors: true });
const validators = new Map(fixture.models.map((model) => [model.id, ajv.compile(model.usage.request_schema)]));
const outputRows = JSON.parse(await readFile(path.join(root, 'docs/research/studio-implementation/Web-migration-fixtures-148.json'), 'utf8')).fixtures;
const outputs = new Map();
for (const mime of ['image/png', 'video/mp4', 'audio/flac', 'model/gltf-binary']) {
    const row = outputRows.find((item) => item.mime_type === mime); assert(row);
    const data = await readFile(path.join(root, row.file)); assert.equal(sha(data), row.sha256); outputs.set(mime, { ...row, data });
}
const assetRows = JSON.parse(await readFile(path.join(root, 'docs/research/studio-implementation/example-assets-151.json'), 'utf8')).files;
const assetsByHash = new Map(assetRows.map((row) => [row.sha256, row]));
const cases = [['product-photo','产品摄影','text_to_image',0], ['product-edit','换一个场景','image_to_image',1], ['reference-style','双图创作','references_to_image',2], ['video-scene','让场景动起来','text_to_video',0], ['video-first-frame','首帧动画','references_to_video',1], ['video-references','双参考视频','references_to_video',2], ['workflow-audio','创作一段配乐','text_to_audio',0], ['object-3d','物体转 3D','image_to_3d',1]];
const jobs = new Map(), contents = new Map(), uploads = new Map();
let browser, context, page, web, api, video, origin, apiOrigin, deny = false;
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const check = (name) => { report.checks.push(name); console.log(JSON.stringify({ passed: name })); };
const json = (res, value, headers = {}) => { res.writeHead(200, { 'Content-Type': 'application/json', ...headers }); res.end(JSON.stringify(value)); };
const waitFor = async (condition) => { const until = Date.now() + 20000; while (!condition()) { assert(Date.now() < until, 'Test observation window exceeded'); await new Promise((resolve) => setTimeout(resolve, 100)); } };
async function shot(name) { const file = path.join(media, name + '.png'); await page.screenshot({ path: file, fullPage: true, animations: 'disabled' }); report.screenshots.push({ name, file: relative(file), sha256: sha(await readFile(file)) }); }
async function darkState(dark) {
    await page.waitForFunction((expected) => document.documentElement.classList.contains('dark') === expected, dark);
    const colors = await page.evaluate(() => ({ body: getComputedStyle(document.body).backgroundColor, text: getComputedStyle(document.body).color, scheme: document.documentElement.style.colorScheme }));
    assert.equal(colors.scheme, dark ? 'dark' : 'light');
    assert.equal(colors.body, dark ? 'rgb(23, 25, 20)' : 'rgb(242, 241, 236)');
    assert.equal(colors.text, dark ? 'rgb(242, 241, 236)' : 'rgb(12, 12, 12)');
}
async function readable(selector, backgroundSelector) {
    const colors = await page.locator(selector).first().evaluate((element, backgroundSelector) => { const style = getComputedStyle(element); return { text: style.color, background: getComputedStyle(backgroundSelector ? document.querySelector(backgroundSelector) : element).backgroundColor }; }, backgroundSelector);
    const rgba = (value) => value.match(/[\d.]+/g).map(Number);
    const bg = rgba(colors.background), fg = rgba(colors.text);
    assert(bg.length === 3 || bg[3] === 1, 'Contrast sample must have a solid background');
    const luminance = (values) => values.slice(0,3).map((value) => value / 255).map((value) => value <= .04045 ? value/12.92 : ((value+.055)/1.055)**2.4).reduce((sum, value, index) => sum + value*[.2126,.7152,.0722][index], 0);
    const text = fg.slice(0,3).map((value,index) => value*(fg[3] ?? 1)+bg[index]*(1-(fg[3] ?? 1))), a = luminance(text), b = luminance(bg);
    assert((Math.max(a,b)+.05)/(Math.min(a,b)+.05) >= 4.5, `Text contrast too low: ${selector}`);
}
try {
    api = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://fixture.invalid'), pathname = url.pathname;
        res.setHeader('Access-Control-Allow-Origin', '*'); res.setHeader('Access-Control-Allow-Headers', req.headers['access-control-request-headers'] || '*'); res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS'); res.setHeader('Access-Control-Expose-Headers', 'X-Studio-Contract,X-Studio-Submission-Lookup');
        try {
            if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
            assert.equal(req.headers.authorization, 'Bearer synthetic-theme-example-key'); assert(!req.headers.cookie);
            const observed = { method: req.method, path: pathname, cookie_present: false }; report.requests.push(observed);
            if (pathname === '/v1/access-policy') return json(res, { scopes: fixture.scopes, limits: { rpm: { mode: 'unlimited', value: null } }, spec_limits: { allowed_qualities: deny ? [] : ['standard'] } }, { 'X-Studio-Contract': '1', 'X-Studio-Submission-Lookup': '1' });
            if (pathname === '/v1/models') return json(res, { data: fixture.models });
            if (pathname === '/v1/capabilities') return json(res, { data: fixture.capabilities });
            if (pathname === '/v1/assets' && req.method === 'POST') {
                const chunks = []; for await (const chunk of req) chunks.push(chunk);
                const form = await new Response(Buffer.concat(chunks), { headers: { 'Content-Type': req.headers['content-type'] } }).formData(), file = form.get('file'), data = Buffer.from(await file.arrayBuffer());
                const row = assetsByHash.get(sha(data)); assert(row); const token = req.headers['idempotency-key']; assert(token);
                const asset = uploads.get(token) || { id: 'asset_' + randomUUID().replaceAll('-', ''), bytes: data.length, sha256: row.sha256, media_type: 'image/png', status: 'ready', expires_at: Math.floor(Date.now()/1000)+28800, width: row.size[0], height: row.size[1] };
                uploads.set(token, asset); return json(res, asset);
            }
            if (pathname === '/v1/jobs' && req.method === 'POST') {
                let raw = ''; for await (const chunk of req) raw += chunk;
                const body = JSON.parse(raw), model = fixture.models.find((item) => item.id === body.model); assert(model);
                const validate = validators.get(body.model); assert(validate(body), JSON.stringify(validate.errors));
                assert(req.headers['idempotency-key']); assert.equal(req.headers['x-expected-release-digest'], fixture.capabilities.find((item) => item.id === body.model).release.digest);
                const output = outputs.get(model.usage.output.media_types[0]); assert(output);
                const id = 'job_' + randomUUID().replaceAll('-', ''), contentId = 'content_' + randomUUID().replaceAll('-', ''), now = Math.floor(Date.now()/1000);
                const job = { id, object: 'generation.job', model: body.model, task_type: body.task_type, status: 'succeeded', updated_revision: 1, created_at: now, completed_at: now, artifacts: [{ id: contentId }] };
                jobs.set(id, job); contents.set(contentId, { data: output.data, metadata: { id: contentId, bytes: output.data.length, sha256: output.sha256, media_type: output.mime_type, expires_at: now+28800, metadata: {} } });
                report.submissions.push({ body, job_id: id, content_id: contentId }); return json(res, job);
            }
            if (pathname.startsWith('/v1/jobs/')) return json(res, jobs.get(pathname.split('/')[3]));
            if (pathname.startsWith('/v1/content/')) {
                const content = contents.get(pathname.split('/')[3]); assert(content);
                if (pathname.endsWith('/download')) { observed.sha256 = sha(content.data); res.writeHead(200, { 'Content-Type': content.metadata.media_type, 'Content-Length': content.data.length }); res.end(content.data); return; }
                return json(res, content.metadata);
            }
            if (req.method === 'GET') return json(res, { data: [], has_more: false, next_cursor: null });
            throw new Error('Unexpected synthetic API operation');
        } catch (error) { report.errors.push(error.stack); if (!res.headersSent) { res.writeHead(500, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ error: { code: 'fixture_error', message: 'Synthetic assertion failed' } })); } else res.end(); }
    }); await new Promise((resolve) => api.listen(0, '192.168.31.95', resolve)); apiOrigin = 'http://192.168.31.95:' + api.address().port;
    if (publicMode) origin = 'http://43.165.167.179:39445';
    else {
        web = createServer(async (req, res) => {
            try {
                const pathname = new URL(req.url, 'http://fixture.invalid').pathname; assert(pathname.startsWith('/studio/'));
                let file = path.resolve(dist, decodeURIComponent(pathname.slice('/studio/'.length))); assert(file === dist || file.startsWith(dist + path.sep));
                try { if (!(await stat(file)).isFile()) file = path.join(dist, 'index.html'); } catch { file = path.join(dist, 'index.html'); }
                const worker = path.basename(file).startsWith('schema-worker-');
                res.writeHead(200, { 'Content-Type': ({ '.js':'application/javascript', '.html':'text/html', '.css':'text/css', '.json':'application/json', '.png':'image/png', '.svg':'image/svg+xml', '.woff':'font/woff', '.woff2':'font/woff2' })[path.extname(file)] || 'application/octet-stream', 'Content-Security-Policy': worker ? "default-src 'self'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
            } catch { res.writeHead(404); res.end(); }
        }); await new Promise((resolve) => web.listen(0, '192.168.31.95', resolve)); origin = 'http://192.168.31.95:' + web.address().port;
    }
    assert.notEqual(origin, apiOrigin); report.origins = { canvas: origin, synthetic_api: apiOrigin };
    const index = await fetch(origin + '/studio/'); assert.equal(sha(Buffer.from(await index.arrayBuffer())), build.static_artifact_sha256['index.html']);
    browser = await launchStudioWebBrowser({ root, temporary, report }); context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: temporary } });
    if (publicMode) await context.route('**/v1/**', async (route) => { const url = new URL(route.request().url()); assert.equal(url.origin, new URL(apiOrigin).origin); const response = await route.fetch(); await route.fulfill({ response }); });
    page = await context.newPage(); video = page.video(); page.on('pageerror', (error) => report.page_errors.push(error.message));
    if (galleryMode) {
        report.gallery_only = true; report.scope = 'Supplemental actual public/local gallery capture with synthetic catalog, fully decoded example covers and zero generation or upload requests; not real GPU evidence.';
        await page.goto(origin + '/studio/config');
        await page.getByPlaceholder('http://服务器:端口').fill(apiOrigin); await page.getByPlaceholder('输入管理员提供的 API Key').fill('synthetic-theme-example-key'); await page.getByRole('checkbox').check(); await page.getByRole('button', { name: '连接并读取模型', exact: true }).click(); await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor();
        await page.getByRole('link', { name: '案例', exact: true }).click(); await page.getByRole('heading', { name: '从一个案例开始', exact: true }).waitFor(); await page.locator('.studio-example-availability b').first().waitFor();
        for (const card of await page.locator('[data-example-id]').all()) await card.scrollIntoViewIfNeeded();
        await page.locator('.studio-example-cover img').evaluateAll((images) => Promise.all(images.map((image) => image.decode())));
        await page.getByRole('heading', { name: '从一个案例开始', exact: true }).scrollIntoViewIfNeeded(); check('gallery waits for catalog and all lazily loaded covers to finish decoding');
        await page.getByRole('button', { name: '切换到深色', exact: true }).click(); await darkState(true); await shot('examples-dark-desktop');
        await page.getByRole('button', { name: '切换到浅色', exact: true }).click(); await darkState(false); await shot('examples-light-desktop');
        await page.setViewportSize({ width: 390, height: 844 }); await shot('examples-light-390'); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.getByRole('button', { name: '切换到深色', exact: true }).click(); await darkState(true); await shot('examples-dark-390'); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        assert.equal(report.submissions.length, 0); assert(report.requests.every((request) => request.method === 'GET')); assert(!report.errors.length && !report.page_errors.length); check('four stable theme gallery captures with zero generation or upload requests'); report.passed = true;
    } else {
    await page.goto(origin + '/studio/config'); await page.getByRole('button', { name: '切换到深色', exact: true }).waitFor(); await darkState(false); check('fresh profile defaults to original Lime light theme');
    await page.getByRole('button', { name: '切换到深色', exact: true }).click(); await darkState(true); await page.reload(); await darkState(true); check('dark mode persists across reload and color-scheme matches');
    await readable('input.ant-input'); await readable('.ant-btn-primary'); await readable('.studio-connection-summary .studio-inline-link', '.studio-connection-summary');
    const panelLink = page.locator('.studio-connection-summary .studio-inline-link').first(); await panelLink.hover(); await readable('.studio-connection-summary .studio-inline-link', '.studio-connection-summary'); await panelLink.focus(); assert.equal(await panelLink.evaluate((element) => getComputedStyle(element).outlineColor), 'rgb(12, 12, 12)');
    check('dark input, primary action and Lime panel link text meet 4.5 contrast; panel keyboard focus remains dark'); await shot('connection-dark');
    const entry = [...(await readFile(path.join(dist, 'index.html'), 'utf8')).matchAll(/<script[^>]*type="module"[^>]*src="([^"]+)"/g)][0]?.[1]; assert(entry);
    await context.route(origin + entry, (route) => route.abort()); await page.reload();
    await page.waitForFunction(() => document.documentElement.classList.contains('dark') && document.documentElement.style.colorScheme === 'dark');
    await context.unroute(origin + entry); await page.reload(); await page.getByRole('button', { name: '切换到浅色', exact: true }).waitFor(); check('same-origin startup script restores dark mode before the application module under strict CSP');
    await page.getByRole('button', { name: '切换到浅色', exact: true }).focus(); await page.keyboard.press('Enter'); await darkState(false); await page.keyboard.press('Enter'); await darkState(true); check('keyboard Enter switches the theme in both directions');
    await page.getByRole('link', { name: '案例', exact: true }).click(); await page.getByRole('button', { name: '打开换一个场景案例', exact: true }).click(); await page.getByText('连接你的生成服务', { exact: true }).waitFor(); assert.equal(report.requests.length, 0); await shot('offline-example-dark'); await page.goto(origin + '/studio/config'); check('an unconnected user can open and save an editable image example without API requests');
    await page.getByPlaceholder('http://服务器:端口').fill(apiOrigin); await page.getByPlaceholder('输入管理员提供的 API Key').fill('synthetic-theme-example-key'); await page.getByRole('checkbox').check(); await page.getByRole('button', { name: '连接并读取模型', exact: true }).click(); await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor();
    for (const [label, pathname, heading] of [['模型','models','找到这次创作的模型'], ['任务','tasks','创作继续，任务也在继续'], ['素材','assets','留住每一次创作'], ['案例','library','从一个案例开始']]) { await page.getByRole('link', { name: label, exact: true }).click(); await page.waitForURL('**/studio/' + pathname); await page.getByRole('heading', { name: heading, exact: true }).waitFor(); await darkState(true); }
    const libraryReload = await page.reload(); assert.equal(libraryReload.status(), 200); await page.getByRole('heading', { name: '从一个案例开始', exact: true }).waitFor(); check('example library survives a direct reload on its independent business route');
    await page.locator('[data-example-id]').first().waitFor(); assert.equal(await page.locator('[data-example-id]').count(), 8); await readable('.studio-example-cover .ant-tag'); await shot('examples-dark-desktop'); check('all main pages keep dark palette; eight examples are discoverable and labels remain readable over reference images');
    for (const [id, title, taskType, count] of cases) {
        const previous = report.submissions.length, uploadCount = uploads.size;
        await page.getByRole('button', { name: `打开${title}案例`, exact: true }).click();
        await page.locator('.studio-composer').getByRole('combobox', { name: '生成模型', exact: true }).waitFor();
        await page.waitForFunction((expected) => document.querySelectorAll('[data-studio-reference-preview] img').length === expected && [...document.querySelectorAll('[data-studio-reference-preview] img')].every((image) => image.naturalWidth > 0), count);
        assert.equal(report.submissions.length, previous); assert.equal(uploads.size, uploadCount);
        await darkState(true); if (id === 'reference-style') { await shot('two-reference-composer-dark'); await page.getByRole('combobox', { name: '生成模型', exact: true }).click(); await page.locator('.ant-select-dropdown').last().waitFor();
            await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowUp');
            await page.locator('.ant-select-item-option-selected.ant-select-item-option-active').waitFor();
            await page.waitForFunction(() => { const option = document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-selected'); if (!option) return false; const style = getComputedStyle(option); return style.color === 'rgb(12, 12, 12)' && style.backgroundColor === 'rgb(210, 245, 60)'; });
            await readable('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-selected'); await shot('model-selector-dark'); check('selected and keyboard-active model option preserves Lime background and readable dark text'); await page.keyboard.press('Escape'); }
        await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click();
        await waitFor(() => report.submissions.length === previous + 1); const submission = report.submissions.at(-1); assert.equal(submission.body.task_type, taskType);
        const references = submission.body.input.references || (submission.body.input.source_image ? [submission.body.input.source_image] : []); assert.equal(references.length, count);
        if (taskType === 'image_to_3d') assert.deepEqual(Object.keys(submission.body.input), ['source_image']);
        await page.getByRole('button', { name: '载入产物', exact: true }).click();
        await waitFor(() => report.requests.some((request) => request.path === `/v1/content/${submission.content_id}/download`));
        check(`${id}: editable originals, no automatic POST, published form, one explicit synthetic task and result download`);
        if (taskType === 'image_to_3d') {
            await page.getByRole('button', { name: '交互预览', exact: true }).click(); await page.getByRole('group', { name: '三维视图容器', exact: true }).locator('canvas').waitFor(); await shot('glb-preview-dark'); await page.locator('.ant-modal-close').click();
            await page.getByRole('button', { name: '切换到浅色', exact: true }).click(); await darkState(false); await page.getByRole('button', { name: '交互预览', exact: true }).click(); await page.getByRole('group', { name: '三维视图容器', exact: true }).locator('canvas').waitFor(); await shot('glb-preview-light'); await page.locator('.ant-modal-close').click(); await page.getByRole('button', { name: '切换到深色', exact: true }).click(); await darkState(true); check('GLB preview modal and native WebGL view follow both themes');
        }
        await page.getByRole('link', { name: '案例', exact: true }).click(); await page.waitForURL('**/studio/library');
    }
    await page.getByRole('button', { name: '切换到浅色', exact: true }).click(); await darkState(false); await shot('examples-light-desktop'); await readable('.studio-example-cover .ant-tag');
    await page.setViewportSize({ width: 390, height: 844 }); await shot('examples-light-390'); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.getByRole('button', { name: '切换到深色', exact: true }).click(); await darkState(true); await shot('examples-dark-390'); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); check('light/dark desktop and 390px examples have no document horizontal overflow');
    await page.setViewportSize({ width: 1440, height: 1000 }); await page.getByRole('link', { name: '画布', exact: true }).click(); await page.getByRole('button', { name: '预设案例', exact: true }).waitFor(); await shot('canvas-library-dark'); check('example projects persist in the existing canvas library');
    await page.getByRole('heading', { name: '双图创作 · 案例', exact: true }).click(); await page.locator('[data-canvas-title]').waitFor(); await page.locator('main.studio-canvas-project > section').click({ position: { x: 850, y: 580 } }); await page.getByRole('button', { name: '新建创作', exact: true }).click(); await page.waitForFunction(() => document.querySelectorAll('[data-studio-reference-preview] img').length === 2); assert.equal(report.submissions.length, 8); await shot('saved-example-continues-with-original-references'); check('saved example reuses its marked original inputs and task family, excluding generated images'); await page.getByRole('button', { name: '关闭', exact: true }).click();
    deny = true; await page.goto(origin + '/studio/library'); await page.getByText('当前 Key 未发现匹配模型，仍可打开并编辑画布。', { exact: true }).first().waitFor(); assert.equal(await page.getByText('当前 Key 未发现匹配模型，仍可打开并编辑画布。', { exact: true }).count(), 8); await shot('examples-no-authorized-specification'); check('all-specification-denied Key shows truthful availability without a fallback model');
    await page.evaluate(() => localStorage.setItem('infinite-canvas:theme_store', JSON.stringify({ state: { theme: 'unknown' }, version: 0 }))); await page.reload(); await page.getByRole('button', { name: '切换到深色', exact: true }).waitFor(); await darkState(false); check('unknown stored theme falls back to supported light mode');
    assert.equal(report.errors.length, 0); assert.equal(report.page_errors.length, 0); report.passed = true;
    }
} catch (error) { report.failure = error.stack; if (page && !page.isClosed()) await shot('failure').catch(() => {}); }
finally {
    if (page) await page.close().catch(() => {}); if (context) await context.close().catch(() => {}); if (browser) await browser.close();
    for (const server of [web, api]) if (server) await new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); });
    if (video) { try { const file = path.join(temporary, 'complete-page.webm'); await copyFile(await video.path(), file); report.full_selected_page_recording = relative(file); } catch (error) { report.video_failure = error.message; } }
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: !context || !context.pages().length, browser_closed: !browser?.isConnected(), fixture_servers_closed: !web?.listening && !api?.listening };
}
for (const [name, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, name))), digest);
report.static_bytes_unchanged = true;
const output = path.join(root, 'docs/research/studio-implementation/theme-examples-' + stamp + '.json'); await writeFile(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ passed: report.passed, report: relative(output), checks: report.checks.length, submissions: report.submissions.length, screenshots: report.screenshots.length, cleanup: report.cleanup, failure: report.failure })); if (!report.passed) process.exitCode = 1;
