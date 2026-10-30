/** Exercise actual canvas selection, published forms and existing API bindings. Synthetic media only. */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile, stat, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url)), source = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(source, 'package.json'));
const relative = (file) => path.relative(root, file).replaceAll('\\', '/');
const sha = (data) => createHash('sha256').update(data).digest('hex');
const publicMode = process.argv.includes('--public'), baseline = process.argv.includes('--baseline');
assert(!baseline || publicMode);
const buildFile = path.resolve(root, process.argv[2]), build = JSON.parse(await readFile(buildFile, 'utf8'));
assert(build.passed && build.source_before === build.source_after);
const clean = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8'));
const dist = path.join(root, clean.private_logs, 'repository/web/dist');
const stamp = webEngine + '-' + new Date().toISOString().replaceAll(/[:.]/g, '-');
const temporary = path.join(root, '.local/studio150/composer-' + stamp), media = path.join(root, 'docs/media/studio-150/composer-' + stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
const report = { passed: false, at: new Date().toISOString(), build_report: relative(buildFile), public_static_site: publicMode, baseline_reproduction: baseline,
    scope: 'Actual frozen Web UI, native owned profiles, canvas selection and form/request/result binding for 11 published examples. All API responses and media are synthetic; not GPU generation, physical touch, production authorization or complete browser acceptance.',
    api_transport: publicMode ? 'Explicit Playwright forwarding to the owned synthetic HTTP server; public UI acceptance only, not native CORS proof' : 'Two real native non-loopback HTTP origins; no business route interception',
    checks: [], api_requests: [], submissions: [], screenshots: [], server_errors: [], page_errors: [], forbidden_requests: [], cleanup: {}, product_timeouts_changed: false };
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name })); };
const fixture = JSON.parse(await readFile(path.join(source, 'tests/fixtures/catalog.json'), 'utf8'));
const Ajv2020 = require('ajv/dist/2020');
const ajv = new Ajv2020({ allErrors: true, strict: false });
const validators = new Map(fixture.models.map((model) => [model.id, ajv.compile(model.usage.request_schema)]));
// Reorder existing published presets, without inventing a specification. Standard-only Key must skip a draft first entry.
for (const model of fixture.models) {
    model.usage.presets.sort((a, b) => Number(b.quality === 'draft') - Number(a.quality === 'draft'));
    fixture.capabilities.find((item) => item.id === model.id).presets = model.usage.presets;
}
const originals = JSON.parse(await readFile(path.join(root, 'docs/research/studio-implementation/Web-migration-fixtures-148.json'), 'utf8')).fixtures;
const inputFiles = JSON.parse(await readFile(path.join(root, 'docs/research/studio-implementation/media-actions-fixtures-149.json'), 'utf8')).fixtures.filter((item) => ['original.jpg', 'variant.webp'].includes(item.name));
for (const [index, item] of inputFiles.entries()) { item.data = [...await readFile(path.join(root, item.file))]; item.key = 'image:managed-v1:composer-source-' + index; assert.equal(sha(Buffer.from(item.data)), item.sha256); }
const outputs = new Map();
for (const mime of ['image/png', 'audio/flac', 'video/mp4', 'model/gltf-binary']) {
    const item = originals.find((entry) => entry.mime_type === mime); assert(item, 'Existing verified synthetic fixture for ' + mime);
    const data = await readFile(path.join(root, item.file)); assert.equal(sha(data), item.sha256); outputs.set(mime, { ...item, data });
}
const projects = fixture.models.map((model, index) => ({ id: 'composer-project-' + index, title: '参考创作 · ' + model.usage.display_name, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), viewport: { x: 0, y: 0, k: 0.8 }, backgroundMode: 'lines', showImageInfo: false, chatSessions: [], activeChatId: null, connections: [], nodes: inputFiles.map((file, i) => ({ id: 'reference-' + i, type: 'image', title: i ? '参考 B · 光线与配色' : '参考 A · 主体与构图', position: { x: 30 + i * 330, y: 80 }, width: 270, height: 203, metadata: { storageKey: file.key, status: 'success', mimeType: file.mime_type, naturalWidth: 16, naturalHeight: 12 } })) }));
const initialProject = { ...projects[0], id: 'composer-project-initial', title: '参考创作 · 预检与恢复' };
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-local-media"; export {rawJournalTransaction} from "./src/services/studio-journal-db"; export {blobHash} from "./src/services/api/aggregator/client";', resolveDir: source }, bundle: true, platform: 'browser', format: 'esm', define: { 'import.meta.env.VITE_STUDIO_MODE': '"managed"' }, outfile: path.join(temporary, 'probe.mjs'), logLevel: 'warning' });
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
let browser, context, page, web, api, video, origin, apiOrigin;
let failNextUpload = false, denySpecifications = false;
let holdRestoringCatalog = false;
const pendingCatalog = [];
const uploads = new Map(), jobs = new Map(), contents = new Map();
const json = (res, status, value, extra = {}) => { res.writeHead(status, { 'Content-Type': 'application/json', ...extra }); res.end(JSON.stringify(value)); };
async function verifyStatic() { if (!baseline) for (const [file, hash] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), hash); }
async function shot(name) { const file = path.join(media, name + '.png'); await page.screenshot({ path: file }); report.screenshots.push({ file: relative(file), bytes: (await stat(file)).size, sha256: sha(await readFile(file)) }); }
const assetPosts = () => report.api_requests.filter((item) => item.path === '/v1/assets' && item.method === 'POST');
async function records() { return page.evaluate(async () => { const p = await import('/probe.mjs'); return { intents: await p.journalList('intents'), uploads: (await p.journalList('uploads')).map(({ blob, ...item }) => item) }; }); }
async function waitCommitted(condition, argument) {
    const deadline = Date.now() + 20000;
    do { if (await page.evaluate(condition, argument) === true) return; await page.waitForTimeout(100); } while (Date.now() < deadline);
    assert.fail('Native database condition did not commit within the existing 20s test observation bound');
}
async function selectModel(model) {
    const control = page.getByRole('combobox', { name: '生成模型', exact: true }); await control.click(); await control.fill(model.usage.display_name);
    await page.locator('.ant-select-item-option').filter({ hasText: model.usage.display_name }).first().click();
}
async function openFromSelection(project) {
    await page.goto(origin + '/studio/canvas/' + project.id);
    await page.locator('[data-canvas-title]').waitFor({ timeout: 60000 });
    await page.waitForFunction(() => [...document.querySelectorAll('[data-node-id] img')].filter((img) => img.naturalWidth > 0).length >= 2);
    await page.locator('main.studio-canvas-project > section').click({ position: { x: 850, y: 580 } });
    await page.keyboard.press('Control+a');
    await page.getByRole('button', { name: '新建创作', exact: true }).click();
    if (holdRestoringCatalog) await page.getByText('连接你的生成服务', { exact: true }).waitFor();
    else await page.getByRole('combobox', { name: '生成模型', exact: true }).waitFor();
}
try {
    await verifyStatic();
    api = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://fixture.invalid'), pathname = url.pathname;
        const observed = { method: req.method, path: pathname, bearer_present: req.headers.authorization === 'Bearer synthetic-composer-key', cookie_present: Boolean(req.headers.cookie) };
        report.api_requests.push(observed);
        res.setHeader('Access-Control-Allow-Origin', '*'); res.setHeader('Access-Control-Allow-Headers', req.headers['access-control-request-headers'] || '*'); res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS'); res.setHeader('Access-Control-Expose-Headers', 'X-Studio-Contract,X-Studio-Submission-Lookup,Retry-After');
        try {
            if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
            assert(observed.bearer_present && !observed.cookie_present, 'Explicit business identity only');
            if (pathname === '/v1/access-policy') return json(res, 200, { scopes: fixture.scopes, limits: { rpm: { mode: 'unlimited', value: null } }, spec_limits: { allowed_qualities: denySpecifications ? [] : ['standard'] } }, { 'X-Studio-Contract': '1', 'X-Studio-Submission-Lookup': '1' });
            if (pathname === '/v1/models') { if (holdRestoringCatalog) await new Promise((resolve) => pendingCatalog.push(resolve)); return json(res, 200, { data: fixture.models }); }
            if (pathname === '/v1/capabilities') return json(res, 200, { data: fixture.capabilities });
            if (pathname === '/v1/assets' && req.method === 'POST') {
                const chunks = []; for await (const chunk of req) chunks.push(chunk);
                const form = await new Response(Buffer.concat(chunks), { headers: { 'Content-Type': req.headers['content-type'] } }).formData(); const file = form.get('file'), data = Buffer.from(await file.arrayBuffer()), hash = sha(data);
                const token = req.headers['idempotency-key']; assert(token);
                const previous = uploads.get(token); if (previous) assert.equal(previous.sha256, hash);
                const asset = previous || { id: 'asset_' + randomUUID().replaceAll('-', ''), bytes: data.length, sha256: hash, media_type: file.type, status: 'ready', expires_at: Math.floor(Date.now() / 1000) + 28800, width: 16, height: 12 };
                uploads.set(token, asset); Object.assign(observed, { idempotency_sha256: sha(token), asset_id: asset.id, sha256: hash });
                if (failNextUpload) { failNextUpload = false; return json(res, 503, { error: { code: 'synthetic_upload_uncertain', message: '合成夹具：原上传结果暂未确认' } }); }
                return json(res, 200, asset);
            }
            if (pathname === '/v1/jobs' && req.method === 'POST') {
                let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw), model = fixture.models.find((item) => item.id === body.model);
                assert(model && body.task_type === model.usage.task_type && body.parameters.quality === 'standard');
                const validate = validators.get(model.id); assert(validate(body), JSON.stringify(validate.errors));
                assert(req.headers['idempotency-key'] && req.headers['x-expected-release-digest'] === fixture.capabilities.find((item) => item.id === model.id).release.digest);
                const output = outputs.get(model.usage.output.media_types[0]); assert(output);
                const id = 'job_' + randomUUID().replaceAll('-', ''), contentId = 'content_' + randomUUID().replaceAll('-', ''), now = Math.floor(Date.now() / 1000);
                const job = { id, object: 'generation.job', model: model.id, task_type: body.task_type, status: 'succeeded', updated_revision: 1, created_at: now, completed_at: now, artifacts: [{ id: contentId }] };
                const metadata = { id: contentId, bytes: output.data.length, sha256: output.sha256, media_type: output.mime_type, expires_at: now + 28800, metadata: { width: output.width || output.media?.width || 16, height: output.height || output.media?.height || 12, duration_milliseconds: Math.round((output.duration_seconds || 1) * 1000) } };
                jobs.set(id, job); contents.set(contentId, { metadata, data: output.data }); report.submissions.push({ job_id: id, body, idempotency_sha256: sha(req.headers['idempotency-key']), release_digest: req.headers['x-expected-release-digest'] });
                return json(res, 200, job);
            }
            if (pathname.startsWith('/v1/jobs/')) return json(res, 200, jobs.get(pathname.split('/')[3]));
            if (pathname.startsWith('/v1/content/')) {
                const value = contents.get(pathname.split('/')[3]); assert(value);
                if (pathname.endsWith('/download')) { res.writeHead(200, { 'Content-Type': value.metadata.media_type, 'Content-Length': value.data.length }); res.end(value.data); return; }
                return json(res, 200, value.metadata);
            }
            json(res, 404, {});
        } catch (error) { report.server_errors.push(error.stack); if (!res.headersSent) json(res, 500, { error: { code: 'fixture_error', message: 'Synthetic fixture assertion failed' } }); else res.end(); }
    }); await new Promise((resolve) => api.listen(0, '192.168.31.95', resolve)); apiOrigin = 'http://192.168.31.95:' + api.address().port;
    if (publicMode) origin = 'http://43.165.167.179:39445';
    else {
        web = createServer(async (req, res) => {
            try {
                const pathname = new URL(req.url, 'http://fixture.invalid').pathname;
                if (pathname === '/probe.mjs') { res.writeHead(200, { 'Content-Type': 'application/javascript' }); res.end(await readFile(path.join(temporary, 'probe.mjs'))); return; }
                if (pathname === '/blank') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end('<!doctype html><title>Owned composer fixture</title>'); return; }
                let target = path.resolve(dist, decodeURIComponent(pathname.replace(/^\/studio\//, ''))); assert(pathname.startsWith('/studio/') && (target === dist || target.startsWith(dist + path.sep)));
                try { if (!(await stat(target)).isFile()) target = path.join(dist, 'index.html'); } catch { target = path.join(dist, 'index.html'); }
                const worker = path.basename(target).startsWith('schema-worker-');
                res.writeHead(200, { 'Content-Type': ({ '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' })[path.extname(target)] || 'application/octet-stream', 'Content-Security-Policy': worker ? "default-src 'self'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(target));
            } catch { res.writeHead(404); res.end(); }
        }); await new Promise((resolve) => web.listen(0, '192.168.31.95', resolve)); origin = 'http://192.168.31.95:' + web.address().port;
    }
    report.origins = { canvas: origin, synthetic_api: apiOrigin }; assert.notEqual(origin, apiOrigin);
    if (!baseline) { const index = await fetch(origin + '/studio/'); assert(index.ok); assert.equal(sha(Buffer.from(await index.arrayBuffer())), build.static_artifact_sha256['index.html']); }
    browser = await launchStudioWebBrowser({ root, temporary, report }); context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: temporary }, locale: 'zh-CN' });
    if (publicMode) await context.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if (url.origin === origin && url.pathname === '/probe.mjs') return route.fulfill({ contentType: 'application/javascript', body: await readFile(path.join(temporary, 'probe.mjs')) });
        if (url.origin === origin && url.pathname === '/blank') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Owned public UI fixture</title>' });
        if (url.origin === apiOrigin) return route.fulfill({ response: await route.fetch({ maxRetries: 0, maxRedirects: 0, timeout: 20000 }) });
        if (['http:', 'https:'].includes(url.protocol) && (url.origin !== origin || /^\/(admin|v1|internal|api)(\/|$)/.test(url.pathname))) { report.forbidden_requests.push(url.origin + url.pathname); return route.abort(); }
        return route.continue();
    });
    page = await context.newPage(); video = page.video(); page.on('pageerror', (error) => report.page_errors.push(error.message));
    page.on('request', (req) => { const url = new URL(req.url()); if (['http:', 'https:'].includes(url.protocol) && ![origin, apiOrigin].includes(url.origin)) report.forbidden_requests.push(url.origin + url.pathname); });
    await page.goto(origin + '/blank');
    await page.evaluate(async ({ projects, inputFiles }) => { const p = await import('/probe.mjs'); await p.initializeDocuments({ [p.documentNames[0]]: null, [p.documentNames[1]]: null }); for (const file of inputFiles) await p.addManagedMedia(file.key, new Blob([new Uint8Array(file.data)], { type: file.mime_type })); const doc = await p.journalGet('documents', p.documentNames[0]); await p.saveDocument(doc.id, JSON.stringify({ version: 0, state: { projects, deletedProjects: [] } }), doc.revision); }, { projects: [...projects, initialProject], inputFiles });
    await page.goto(origin + '/studio/config'); await page.getByPlaceholder('http://服务器:端口').fill(apiOrigin); await page.getByPlaceholder('输入管理员提供的 API Key').fill('synthetic-composer-key'); await page.getByRole('checkbox').check(); await page.getByRole('button', { name: '连接并读取模型', exact: true }).click(); await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor();
    check('existing API catalog and standard-only policy load through actual connection UI');
    if (!baseline) holdRestoringCatalog = true;
    await openFromSelection(initialProject);
    if (!baseline) {
        assert(await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).isDisabled());
        holdRestoringCatalog = false; for (const release of pendingCatalog.splice(0)) release();
    }
    if (baseline) {
        assert.equal(await page.locator('[data-studio-reference-preview]').count(), 0); assert.equal(await page.locator('fieldset .ant-select-selection-item').count(), 0); await shot('baseline-selected-images-not-bound'); check('149 baseline: selected images are not populated into reference slots'); report.passed = true;
    } else {
        await page.waitForFunction(() => document.querySelectorAll('[data-studio-reference-preview] img').length === 2 && [...document.querySelectorAll('[data-studio-reference-preview] img')].every((img) => img.naturalWidth > 0));
        check('a creation opened during native catalog restoration initializes once after the original catalog arrives');
        assert((await page.locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '生成模型', exact: true }) }).innerText()).includes(fixture.models.find((item) => item.usage.task_type === 'references_to_image').usage.display_name));
        check('selected canvas images suggest the published two-reference model and populate ordered visible previews');
        await shot('selected-canvas-references');
        await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click(); await page.getByText('操作尚未完成', { exact: true }).waitFor();
        assert.equal(assetPosts().length, 0); assert.equal(report.submissions.length, 0); assert.equal((await records()).uploads.length, 0); check('invalid prompt is rejected before local staging or remote upload');
        await page.getByRole('textbox', { name: '创作描述', exact: true }).fill('Synthetic UI contract check: preserve the two references in their published order.');
        const missing = inputFiles[1]; await page.evaluate(async (id) => { const p = await import('/probe.mjs'); await p.rawJournalTransaction(['local_media'], 'readwrite', (tx, done) => { tx.objectStore('local_media').delete(id); done(); }); }, missing.key);
        await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click(); await page.getByText('所选画布图片的本机原件不可读取，请重新选择', { exact: true }).waitFor(); assert.equal(assetPosts().length, 0); assert.equal(report.submissions.length, 0); check('unreadable second original sends neither the first upload nor a generation request');
        await page.evaluate(async (file) => { const p = await import('/probe.mjs'); await p.addManagedMedia(file.key, new Blob([new Uint8Array(file.data)], { type: file.mime_type })); }, missing);
        failNextUpload = true; await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click(); await page.getByText('合成夹具：原上传结果暂未确认', { exact: true }).first().waitFor(); assert.equal(assetPosts().length, 1); assert.equal(report.submissions.length, 0); check('unconfirmed upload retains staged references and sends no generation');
        await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click(); await page.getByRole('button', { name: '载入产物', exact: true }).waitFor(); assert.equal(assetPosts().length, 3); assert.equal(assetPosts()[0].idempotency_sha256, assetPosts()[1].idempotency_sha256); assert.equal(assetPosts()[0].asset_id, assetPosts()[1].asset_id);
        const first = report.submissions[0], expectedRoles = fixture.models.find((model) => model.id === first.body.model).usage.example_request.input.references.map((item) => item.role);
        assert.deepEqual(first.body.input.references.map((item) => item.role), expectedRoles); assert.deepEqual(first.body.input.references.map((item) => item.asset_id), [assetPosts()[1].asset_id, assetPosts()[2].asset_id]); check('explicit retry reuses original upload identity and ordered asset-role bindings');
        await page.getByRole('button', { name: '载入产物', exact: true }).click(); await waitCommitted(async () => { const p = await import('/probe.mjs'); return (await p.journalList('results')).length === 1; });
        const originalIntent = (await records()).intents[0]; assert.deepEqual(originalIntent.canvas.sourceIds, ['reference-0', 'reference-1']); check('downloaded original returns to a canvas image with the actual selected-source connections');
        await shot('reference-result-returned-to-canvas');
        for (const [index, model] of fixture.models.entries()) {
            console.log(JSON.stringify({ verifying_published_model: model.id }));
            await openFromSelection(projects[index]); await selectModel(model);
            const prompt = page.getByRole('textbox', { name: '创作描述', exact: true }); if (await prompt.count()) await prompt.fill('Synthetic published model form verification.');
            const previewCount = model.usage.references.slots.filter((slot) => slot.path !== 'input.mask').length;
            assert.equal(await page.locator('[data-studio-reference-preview]').count(), previewCount);
            if (model.usage.presets[0].quality === 'draft') assert(!(await page.locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '完整规格', exact: true }) }).innerText()).includes('草稿'));
            if (index === 6) for (const [slot, title] of [[1, '参考 B'], [2, '参考 A']]) {
                const choice = page.getByRole('combobox', { name: `参考图 ${slot}选择`, exact: true }); await choice.click(); await choice.fill(title);
                await page.locator('.ant-select-item-option').filter({ hasText: title }).first().click();
            }
            if (index === 4) await shot('published-video-form-and-reference-choice');
            const before = report.submissions.length, received = page.waitForResponse((response) => response.url() === apiOrigin + '/v1/jobs' && response.request().method() === 'POST');
            await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click(); const accepted = await (await received).json();
            await waitCommitted(async (id) => { const p = await import('/probe.mjs'); return (await p.journalList('intents')).some((item) => item.job?.id === id); }, accepted.id);
            assert.equal(report.submissions.length, before + 1); const submission = report.submissions.at(-1); assert.equal(submission.body.model, model.id);
            const intent = (await records()).intents.find((item) => item.job?.id === submission.job_id); assert(intent);
            const resultButton = page.locator(`[data-node-id="${intent.nodeId}"]`).getByRole('button', { name: '载入产物', exact: true }); await resultButton.waitFor();
            if (index === 6) {
                assert.deepEqual(submission.body.input.references.map((reference) => [...uploads.values()].find((asset) => asset.id === reference.asset_id).sha256), [inputFiles[1].sha256, inputFiles[0].sha256]);
                assert.deepEqual((await records()).intents.find((intent) => intent.job?.id === submission.job_id).canvas.sourceIds, ['reference-1', 'reference-0']);
                check('explicitly exchanging the two canvas images preserves published roles and links the actual input sources');
            }
            if (model.usage.task_type === 'image_to_3d') assert.deepEqual(Object.keys(submission.body.input), ['source_image']);
            if (model.usage.task_type === 'text_to_audio') assert(!('size' in submission.body.parameters) && !('sample_rate' in submission.body.parameters) && !('audio_format' in submission.body.parameters));
            await resultButton.click();
            await waitCommitted(async (id) => { const p = await import('/probe.mjs'); return (await p.journalList('results')).some((item) => item.jobId === id); }, submission.job_id);
            await waitCommitted(async ({ projectId, jobId }) => { const p = await import('/probe.mjs'); const doc = await p.journalGet('documents', p.documentNames[0]); return Boolean(JSON.parse(doc.value).state.projects.find((project) => project.id === projectId).nodes.some((node) => node.metadata?.studioJobId === jobId && node.metadata?.storageKey)); }, { projectId: projects[index].id, jobId: submission.job_id });
            const saved = await page.evaluate(async (id) => { const p = await import('/probe.mjs'); const file = (await p.journalList('results')).find((item) => item.jobId === id); return { bytes: file.blob.size, mime: file.blob.type, sha256: await p.blobHash(file.blob) }; }, submission.job_id);
            const expected = outputs.get(model.usage.output.media_types[0]); assert.deepEqual(saved, { bytes: expected.data.length, mime: expected.mime_type, sha256: expected.sha256 });
            check('published form, single generation binding and verified result: ' + model.id, { task_type: model.usage.task_type, output: saved.mime });
        }
        await shot('glb-result-returned-to-canvas');
        await openFromSelection(projects[2]); await page.setViewportSize({ width: 390, height: 844 }); await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth + 1);
        await shot('390-reference-composer'); check('390px Web drawer keeps reference choices and submission controls inside the viewport');
        await page.setViewportSize({ width: 1440, height: 1000 }); await page.getByRole('button', { name: '关闭', exact: true }).last().click();
        denySpecifications = true; await page.goto(origin + '/studio/config'); await page.getByRole('button', { name: '刷新目录', exact: true }).click(); await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor({ state: 'visible' });
        await page.goto(origin + '/studio/models'); await page.locator('.studio-model-card').first().click(); assert(await page.getByRole('button', { name: '用此模型新建画布', exact: true }).isDisabled()); await page.getByText('当前 Key 没有可用规格，请选择其他模型或联系管理员调整权限。', { exact: true }).waitFor(); check('model detail refuses creation when the current Key has no allowed specifications');
        assert.deepEqual(report.server_errors, []); assert.deepEqual(report.page_errors, []); assert.deepEqual(report.forbidden_requests, []); check('all business calls stay within the configured existing v1 protocol; no Admin, Worker, ComfyUI or agent requests'); report.passed = true;
    }
} catch (error) { report.failure = error.stack; if (page && !page.isClosed()) {
    await shot('failure').catch(() => {}); let observation;
    try { report.failure_state = await Promise.race([page.evaluate(async () => { const p = await import('/probe.mjs'); return { intents: (await p.journalList('intents')).map(({ body, ...item }) => item), results: (await p.journalList('results')).map(({ blob, ...item }) => item), text: document.body.innerText }; }).catch(() => null), new Promise((resolve) => { observation = setTimeout(() => resolve({ native_state_not_observed_within_test_bound: true }), 20000); })]); }
    finally { clearTimeout(observation); }
} }
finally {
    holdRestoringCatalog = false; for (const release of pendingCatalog.splice(0)) release();
    if (page) await page.close().catch(() => {}); if (context) await context.close().catch(() => {}); if (browser) await browser.close();
    for (const server of [web, api]) if (server) await new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); });
    if (video) { try { const file = path.join(temporary, 'complete-page.webm'); await copyFile(await video.path(), file); report.full_selected_page_recording = relative(file); } catch (error) { report.video_failure = error.message; } }
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: !context || !context.pages().length, browser_closed: !browser?.isConnected(), fixture_servers_closed: !web?.listening && !api?.listening };
}
await verifyStatic(); report.static_bytes_unchanged = !baseline;
const target = path.join(root, 'docs/research/studio-implementation/composer-' + (publicMode ? 'public-' : '') + (baseline ? 'baseline-' : '') + stamp + '.json');
await writeFile(target, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, submissions: report.submissions.length, report: relative(target), cleanup: report.cleanup, failure: report.failure }));
if (!report.passed) process.exitCode = 1;
