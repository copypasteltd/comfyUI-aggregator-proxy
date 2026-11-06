/** Whole-round acceptance after implementation: real catalog mappings, owned Web UI, optional real GPU calls. */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile, stat, copyFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';
import { assertStudioArtifact } from './studio_artifact_assertions.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url)), webRoot = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(webRoot, 'package.json')), sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const relative = (file) => path.relative(root, file).replaceAll('\\', '/');
const buildFile = path.resolve(root, process.argv[2]), build = JSON.parse(await readFile(buildFile, 'utf8'));
assert(build.passed && build.source_before === build.source_after);
const clean = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8'));
const dist = path.join(root, clean.private_logs, 'repository/web/dist');
for (const [file, digest] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), digest);
const live = process.argv.includes('--live'), publicSite = process.argv.includes('--public');
const inspectOnly = process.argv.includes('--inspect-only'); assert(!inspectOnly || live);
const only = process.argv.find((arg) => arg.startsWith('--only='))?.slice(7);
const livePlanFile = process.argv.find((arg) => arg.startsWith('--live-plan='))?.slice(12);
const livePlan = livePlanFile ? JSON.parse(await readFile(path.resolve(root, livePlanFile), 'utf8')) : null;
if (livePlan) {
    assert(live && Array.isArray(livePlan) && livePlan.length);
    assert.equal(new Set(livePlan.map((item) => item.label)).size, livePlan.length);
    for (const item of livePlan) assert(/^[a-z0-9-]+$/.test(item.label) && typeof item.model === 'string');
}
const stamp = `${live ? 'live' : 'synthetic'}-${webEngine}-${new Date().toISOString().replaceAll(/[:.]/g, '-')}`;
const temporary = path.join(root, '.local/studio154', stamp), media = path.join(root, 'docs/media/studio-154', stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
const fixture = JSON.parse(await readFile(path.join(webRoot, 'tests/fixtures/catalog-154.json'), 'utf8'));
const mixed = JSON.parse(await readFile(path.join(webRoot, 'tests/fixtures/multimodal-154.json'), 'utf8'));
const continuation = JSON.parse(await readFile(path.join(webRoot, 'tests/fixtures/usage-context-154.json'), 'utf8'));
const privateCatalog = live ? JSON.parse(await readFile(path.join(root, '.local/studio-ops/canvas-154-catalog.json'), 'utf8')) : null;
const connection = live ? JSON.parse(await readFile(path.join(root, '.local/studio-ops/canvas-154-connection.json'), 'utf8')) : { key: 'synthetic-input-key' };
const policy = privateCatalog?.['access-policy'] || { scopes: fixture.scopes, limits: {}, spec_limits: { allowed_qualities: ['draft', 'standard', 'high'], max_references: 16 } };
const models = structuredClone(privateCatalog?.models.data || fixture.models), capabilities = structuredClone(privateCatalog?.capabilities.data || fixture.capabilities);
if (!live) {
    models.push(mixed.model, { id: continuation.example_request.model, usage: continuation }); capabilities.push(mixed.capability, { ...capabilities.find((item) => item.task_type === continuation.task_type), id: continuation.example_request.model, presets: continuation.presets, output: continuation.output });
}
const Ajv = require('ajv/dist/2020'), ajv = new Ajv({ strict: false, allErrors: true });
const validators = new Map(models.map((model) => [model.id, ajv.compile(model.usage.request_schema)]));
const report = { passed: false, at: new Date().toISOString(), source_sha256: build.source_after, build_report: relative(buildFile), real_business_api: live, public_static_site: publicSite, checks: [], screenshots: [], submissions: [], uploads: [], errors: [], page_errors: [], cleanup: {}, scope: live ? 'Explicit real browser submissions through the unchanged original API; not every model, fleet or browser acceptance.' : 'Owned native browser UI against an independent synthetic API using actual public model schemas plus labelled multimodal/continuation fixtures; no GPU execution.' };
report.inspect_only = inspectOnly;
if (inspectOnly) report.scope = 'Real unchanged business API authentication, catalog and form inspection on current static bytes. Files staged locally only; no business uploads or job submissions, no GPU output claim.';
const outputRows = JSON.parse(await readFile(path.join(root, 'docs/research/studio-implementation/Web-migration-fixtures-148.json'), 'utf8')).fixtures;
const sources = new Map();
for (const mime of ['image/png', 'video/mp4', 'video/webm', 'audio/flac', 'audio/wav', 'model/gltf-binary']) { const row = outputRows.find((item) => item.mime_type === mime); const bytes = await readFile(path.join(root, row.file)); assert.equal(sha(bytes), row.sha256); sources.set(mime, { bytes, name: row.name, path: path.join(root, row.file) }); }
// The published two-speaker workflow returns MP3; encode a labelled synthetic fixture on native Windows.
const mp3 = path.join(temporary, 'synthetic.mp3');
await promisify(execFile)(path.join(root, '../生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe'), ['-hide_banner', '-loglevel', 'error', '-i', sources.get('audio/wav').path, '-c:a', 'libmp3lame', mp3], { windowsHide: true });
sources.set('audio/mpeg', { bytes: await readFile(mp3), name: 'synthetic.mp3', path: mp3 });
if (live) { const file = path.join(root, 'docs/media/studio-131/image.png'); sources.set('image/png', { bytes: await readFile(file), name: 'robot-reference.png', path: file }); }
// A self-contained static triangle for the GLB input picker (no animation/external files).
const positions = Buffer.from(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]).buffer);
const meshDoc = { asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }], meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }], buffers: [{ byteLength: positions.length }], bufferViews: [{ buffer: 0, byteLength: positions.length }], accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }] };
let meshJson = Buffer.from(JSON.stringify(meshDoc)); meshJson = Buffer.concat([meshJson, Buffer.alloc((4 - meshJson.length % 4) % 4, 32)]);
const glb = Buffer.alloc(12 + 8 + meshJson.length + 8 + positions.length); glb.writeUInt32LE(0x46546c67, 0); glb.writeUInt32LE(2, 4); glb.writeUInt32LE(glb.length, 8); glb.writeUInt32LE(meshJson.length, 12); glb.writeUInt32LE(0x4e4f534a, 16); meshJson.copy(glb, 20); glb.writeUInt32LE(positions.length, 20 + meshJson.length); glb.writeUInt32LE(0x004e4942, 24 + meshJson.length); positions.copy(glb, 28 + meshJson.length);
await writeFile(path.join(temporary, 'static-input.glb'), glb); sources.set('model/gltf-binary', { bytes: glb, name: 'static-input.glb', path: path.join(temporary, 'static-input.glb') });
let browser, context, page, video, web, api, origin, apiOrigin, currentMarker, videoStartedAt;
let markerWrite = Promise.resolve();
const jobs = new Map(), contents = new Map(), observers = new Set();
const parentId = 'job_' + 'f'.repeat(32), check = (name) => { report.checks.push(name); console.log(JSON.stringify({ passed: name })); };
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const json = (res, data, status = 200, headers = {}) => { res.writeHead(status, { 'Content-Type': 'application/json', ...headers }); res.end(JSON.stringify(data)); };
const waitFor = async (predicate, limit = 20000) => { const until = Date.now() + limit; while (!predicate()) { assert.deepEqual(report.errors, [], 'Verification server/observer error'); assert(Date.now() < until, 'Observation deadline reached; original task stays intact'); await new Promise((resolve) => setTimeout(resolve, 200)); } };
const shot = async (name) => { const file = path.join(media, name + '.png'); await page.screenshot({ path: file, animations: 'disabled' }); report.screenshots.push({ name, file: relative(file), sha256: sha(await readFile(file)), recording_seconds: (Date.now() - videoStartedAt) / 1000 }); };
async function openModel(model) {
    await page.getByRole('link', { name: '模型', exact: true }).click();
    await page.getByLabel('搜索模型', { exact: true }).fill(model.id);
    await page.locator('.studio-model-card').filter({ has: page.getByText(model.id, { exact: true }) }).click();
    await page.getByRole('button', { name: '用此模型新建画布', exact: true }).click();
    await page.locator('[data-canvas-title]').waitFor();
    await page.locator('.studio-composer').getByRole('combobox', { name: '生成模型', exact: true }).waitFor();
}
async function fillForm(model, negative = false, files = {}) {
    const drawer = page.locator('.studio-composer'), usage = model.usage;
    if (usage.request_schema.properties.input.properties.prompt) await drawer.getByRole('textbox', { name: '创作描述', exact: true }).fill('A friendly small lime-green toy robot, clean white background, soft studio light, no text.');
    for (const slot of usage.text_slots || []) {
        const text = slot.name === 'lyrics' ? '[instrumental]' : slot.name === 'style' ? 'A warm gentle instrumental piece, piano and soft percussion, no vocals.' : String(usage.example_request.input.texts?.[slot.name] || '合成台词：你好，这是一段画布功能验证。');
        await drawer.locator(`[data-studio-text="${slot.name}"]`).fill(text);
        const label = drawer.locator(`label:has([data-studio-text="${slot.name}"])`);
        const countBox = await label.locator('.ant-input-data-count').boundingBox(), descriptionBox = await label.locator('.studio-note').boundingBox();
        assert(countBox && descriptionBox && countBox.y + countBox.height <= descriptionBox.y, 'Text count must not overlap the field description');
    }
    if (usage.context?.required) { await drawer.getByRole('textbox', { name: '父任务编号', exact: true }).fill(parentId); await drawer.getByRole('button', { name: '核对父任务', exact: true }).click(); await drawer.getByText(/父任务兼容/).waitFor(); }
    const slots = [...usage.references.slots.map((slot) => ({ ...slot, mime_types: usage.references.media_types, kind: 'image' })), ...(usage.input_slots || []).map((slot) => ({ ...slot, path: 'input.media.' + slot.name }))];
    if (negative && slots.length) {
        const before = report.uploads.length;
        await drawer.locator(`[data-studio-slot="${slots[0].path}"] input[type=file]`).setInputFiles({ name: 'wrong.txt', mimeType: 'text/plain', buffer: Buffer.from('wrong') });
        await drawer.getByText(/类型或文件大小不符合/).waitFor(); assert.equal(report.uploads.length, before); check('wrong file kind is rejected locally before any upload');
    }
    for (const slot of slots.filter((slot) => slot.required)) {
        const declared = files[slot.path];
        let source = sources.get(slot.mime_types[0]) || sources.get([...sources.keys()].find((mime) => slot.mime_types.includes(mime))), mime;
        if (declared) {
            assert(live && slot.mime_types.includes(declared.mime_type));
            const file = path.resolve(root, declared.file); assert(!relative(file).startsWith('../'));
            const bytes = await readFile(file); assert.equal(sha(bytes), declared.sha256);
            source = { bytes, name: path.basename(file), path: file }; mime = declared.mime_type;
            (report.staged_source_inputs ||= []).push({ model: model.id, slot: slot.path, file: relative(file), mime_type: mime, bytes: bytes.length, sha256: declared.sha256 });
        }
        assert(source, slot.name || slot.path);
        const field = drawer.locator(`[data-studio-slot="${slot.path}"]`);
        await field.locator('input[type=file]').setInputFiles({ name: source.name, mimeType: slot.kind === 'model' ? '' : mime || [...sources.entries()].find(([, row]) => row === source)[0], buffer: source.bytes });
        await field.getByText(/已选用/).waitFor();
    }
}
try {
    if (!live) {
        api = createServer(async (req, res) => { try {
            res.setHeader('Access-Control-Allow-Origin', '*'); res.setHeader('Access-Control-Allow-Headers', 'Authorization,Content-Type,Idempotency-Key,X-Expected-Release-Digest'); res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS'); res.setHeader('Access-Control-Expose-Headers', 'X-Studio-Contract,X-Studio-Submission-Lookup,Retry-After');
            if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
            assert.equal(req.headers.cookie, undefined); assert.equal(req.headers.authorization, 'Bearer synthetic-input-key');
            const pathname = new URL(req.url, 'http://fixture.invalid').pathname;
            if (pathname === '/v1/access-policy') return json(res, policy, 200, { 'X-Studio-Contract': '1', 'X-Studio-Submission-Lookup': '1' });
            if (pathname === '/v1/models') return json(res, { data: models });
            if (pathname === '/v1/capabilities') return json(res, { data: capabilities });
            if (pathname === '/v1/assets' && req.method === 'POST') {
                const chunks = []; for await (const chunk of req) chunks.push(chunk);
                const form = await new Request('http://fixture.invalid', { method: 'POST', headers: req.headers, body: Buffer.concat(chunks) }).formData();
                const file = form.get('file'), bytes = Buffer.from(await file.arrayBuffer()), kind = form.get('kind') || 'image'; assert.equal(sha(bytes), sha(sources.get(file.type).bytes));
                const record = { id: 'asset_' + randomUUID().replaceAll('-', ''), status: 'ready', kind, bytes: bytes.length, sha256: sha(bytes), media_type: file.type, expires_at: Math.floor(Date.now() / 1000) + 86400 };
                report.uploads.push({ kind, bytes: bytes.length, media_type: file.type, id: record.id }); return json(res, record);
            }
            if (pathname === '/v1/jobs' && req.method === 'POST') {
                const chunks = []; for await (const chunk of req) chunks.push(chunk); const body = JSON.parse(Buffer.concat(chunks));
                assert(validators.get(body.model)(body), JSON.stringify(validators.get(body.model).errors));
                const model = models.find((item) => item.id === body.model), source = sources.get(model.usage.output.media_types[0]); assert(source);
                const id = 'job_' + randomUUID().replaceAll('-', ''), contentId = 'content_' + randomUUID().replaceAll('-', '');
                const job = { id, object: 'generation.job', model: body.model, task_type: body.task_type, status: 'succeeded', updated_revision: 2, created_at: Math.floor(Date.now() / 1000), artifacts: [{ id: contentId }], error: null };
                contents.set(contentId, { bytes: source.bytes, metadata: { id: contentId, bytes: source.bytes.length, sha256: sha(source.bytes), media_type: model.usage.output.media_types[0], expires_at: Math.floor(Date.now() / 1000) + 28800, metadata: {} } }); jobs.set(id, job); report.submissions.push({ body, job_id: id }); return json(res, job);
            }
            if (pathname === '/v1/jobs/' + parentId) return json(res, { id: parentId, object: 'generation.job', model: 'synthetic-parent', task_type: 'text_to_video', status: 'succeeded', updated_revision: 1, created_at: 1, context: { available: true, family: continuation.context.family, compatibility: continuation.context.compatibility, expires_at: Math.floor(Date.now() / 1000) + 28800 } });
            if (pathname.startsWith('/v1/jobs/')) return json(res, jobs.get(pathname.split('/')[3]));
            if (pathname.startsWith('/v1/content/')) { const output = contents.get(pathname.split('/')[3]); assert(output); if (pathname.endsWith('/download')) { res.writeHead(200, { 'Content-Type': output.metadata.media_type, 'Content-Length': output.bytes.length }); return res.end(output.bytes); } return json(res, output.metadata); }
            return json(res, { data: [], has_more: false, next_cursor: null });
        } catch (error) { report.errors.push(error.message); if (!res.headersSent) return json(res, { error: { code: 'fixture_assertion', message: 'Synthetic API assertion failed' } }, 500); res.end(); } });
        await new Promise((resolve) => api.listen(0, '127.0.0.1', resolve)); apiOrigin = 'http://127.0.0.1:' + api.address().port;
    } else apiOrigin = connection.origin;
    if (publicSite) origin = 'http://43.165.167.179:39445';
    else {
        web = createServer(async (req, res) => { try {
            const pathname = new URL(req.url, 'http://fixture.invalid').pathname; assert(pathname.startsWith('/studio/'));
            let file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); assert(file === dist || file.startsWith(dist + path.sep));
            try { if (!(await stat(file)).isFile()) file = path.join(dist, 'index.html'); } catch { file = path.join(dist, 'index.html'); }
            res.writeHead(200, { 'Content-Type': ({ '.js': 'application/javascript', '.html': 'text/html', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' })[path.extname(file)] || 'application/octet-stream', 'Content-Security-Policy': path.basename(file).startsWith('schema-worker-') ? "default-src 'self'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
        } catch { res.writeHead(404); res.end(); } });
        await new Promise((resolve) => web.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + web.address().port;
    }
    const index = await fetch(origin + '/studio/'); assert.equal(sha(Buffer.from(await index.arrayBuffer())), build.static_artifact_sha256['index.html']);
    browser = await launchStudioWebBrowser({ root, temporary, report }); context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: temporary } });
    if (!live) await context.addCookies([{ name: 'admin_cookie_must_not_leak', value: 'synthetic-cookie', url: apiOrigin }]);
    page = await context.newPage(); video = page.video(); videoStartedAt = Date.now();
    page.on('pageerror', (error) => report.page_errors.push(error.message));
    if (inspectOnly) page.on('request', (request) => { if (new URL(request.url()).origin === apiOrigin && new URL(request.url()).pathname.startsWith('/v1/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) report.errors.push('Unexpected mutating business API request during read-only form inspection'); });
    if (live) page.on('request', (request) => {
        if (!currentMarker || request.method() !== 'POST' || request.url() !== apiOrigin + '/v1/jobs') return;
        const headers = request.headers(), marker = currentMarker;
        markerWrite = markerWrite.then(() => writeFile(marker, JSON.stringify({ state: 'original submission observed; never create a replacement', body: request.postData(), idempotency_key: headers['idempotency-key'], release_digest: headers['x-expected-release-digest'], temporary: relative(temporary) })));
        const pending = markerWrite.catch((error) => report.errors.push(error.message)); observers.add(pending); pending.finally(() => observers.delete(pending));
    });
    if (live) page.on('response', (response) => {
        if (new URL(response.url()).origin !== apiOrigin || !new URL(response.url()).pathname.startsWith('/v1/jobs')) return;
        const marker = currentMarker;
        const pending = (async () => { const data = await response.json(); if (!data?.id) return; jobs.set(data.id, data); if (response.request().method() === 'POST') { report.submissions.push({ job_id: data.id, model: data.model, status: data.status }); if (marker) { await markerWrite; const original = JSON.parse(await readFile(marker, 'utf8')); await writeFile(marker, JSON.stringify({ ...original, job_id: data.id, model: data.model, status: data.status })); } } })().catch((error) => report.errors.push(error.message)); observers.add(pending); pending.finally(() => observers.delete(pending));
    });
    report.cold_start_form_observation_ms = 60000;
    await page.goto(origin + '/studio/config', { timeout: 60000 }); console.log(JSON.stringify({ phase: 'connection page opened', browser: webEngine }));
    await page.getByPlaceholder('http://服务器:端口').fill(apiOrigin, { timeout: 60000 }); await page.getByPlaceholder('输入管理员提供的 API Key').fill(connection.key); await page.getByRole('button', { name: '连接并读取模型', exact: true }).click(); await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor();
    assert.equal(await page.getByPlaceholder('输入管理员提供的 API Key').inputValue(), ''); check('business connection discovers actual usage 1.0 and 1.1 without exposing the Key');
    await page.getByRole('link', { name: '模型', exact: true }).click(); await page.locator('.studio-model-card').first().waitFor(); assert.equal(await page.locator('.studio-model-card').count(), models.length); assert.equal(await page.locator('.studio-model-warning').count(), 0); await shot('models-light'); check(`all ${models.length} discovered models have usable published descriptions`);
    const cases = livePlan ? livePlan.map((item) => [item.label, models.find((model) => model.id === item.model), item.inputs]) : live ? [['text-image', models.find((item) => item.id.includes('11-lustify-v10'))], ['named-edit', models.find((item) => item.id.includes('34-lustify'))], ['named-music', models.find((item) => item.id.includes('01-yue2-full-bf16-abc-music'))]] : [['named-edit', models.find((item) => item.id.includes('34-lustify'))], ['named-voice', models.find((item) => item.id.includes('03-soulx-cafe'))], ['four-views', models.find((item) => item.id.includes('mv-sam3d'))], ['mesh-input', models.find((item) => item.id.includes('ultrashape1'))], ['all-media', mixed.model], ['continuation', models.find((item) => item.id === continuation.example_request.model)]];
    for (const theme of live ? ['light'] : ['light', 'dark']) {
        if (theme === 'dark') await page.getByRole('button', { name: '切换到深色', exact: true }).click();
        for (const [label, model, files] of cases.filter(([name]) => !only || name === only)) {
            assert(model, label); const before = report.submissions.length;
            await openModel(model);
            if (!live && label === 'all-media') { const uploads = report.uploads.length; await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click(); await page.getByText('操作尚未完成', { exact: true }).waitFor(); assert.equal(report.uploads.length, uploads); assert.equal(report.submissions.length, before); check('incomplete form makes zero uploads and zero job submissions'); }
            await fillForm(model, !live && label === 'all-media', files); await shot(label + '-' + theme);
            if (inspectOnly) { await page.locator('.studio-composer').getByRole('button', { name: '关闭', exact: true }).click(); await page.locator('.studio-composer').waitFor({ state: 'hidden' }); check(`${label}: actual published form inspected without remote upload or generation`); continue; }
            if (live) { currentMarker = path.join(root, '.local/studio-ops/canvas-154-once-' + label + '.json'); await writeFile(currentMarker, JSON.stringify({ model: model.id, temporary: relative(temporary), state: 'prepared; rerun forbidden, inspect original browser journal or job' }), { flag: 'wx' }); }
            await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click();
            if (live) {
                await waitFor(() => report.submissions.length === before + 1, 45000); const accepted = report.submissions.at(-1); let last;
                await waitFor(() => { const job = jobs.get(accepted.job_id); if (job?.status !== last) { last = job?.status; console.log(JSON.stringify({ model: model.id, job_id: accepted.job_id, status: last })); } return ['succeeded', 'failed', 'cancelled', 'expired'].includes(job?.status); }, 600000);
                const job = jobs.get(accepted.job_id); report.submissions.at(-1).final_status = job.status; report.submissions.at(-1).error = job.error; assert.equal(job.status, 'succeeded', JSON.stringify(job.error));
            } else await waitFor(() => report.submissions.length === before + 1);
            await page.getByRole('button', { name: '载入产物', exact: true }).click();
            (report.local_artifacts ||= []).push(await assertStudioArtifact(page, report.submissions.at(-1).job_id, model.usage.output.media_types));
            await shot(label + '-result-' + theme); check(`${label} ${theme}: explicit submission validates and artifact returns to canvas`);
            currentMarker = undefined;
        }
    }
    assert.deepEqual(report.page_errors, []); assert.deepEqual(report.errors, []); report.passed = true;
} catch (error) { report.failure = String(error.stack).split(connection.key).join('[redacted]'); if (page && !page.isClosed()) { report.last_page_title = await page.title().catch(() => 'unavailable'); await shot('failure').catch((reason) => { report.screenshot_failure = reason.message; }); } }
finally {
    if (page) await page.close().catch(() => {}); if (context) await context.close().catch(() => {}); if (browser) await browser.close(); await Promise.allSettled([...observers]);
    for (const server of [web, api]) if (server) await new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); });
    if (video) try { const target = path.join(temporary, 'complete.webm'); await copyFile(await video.path(), target); report.recording_private = relative(target); } catch (error) { report.video_error = error.message; }
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: !context || !context.pages().length, browser_closed: !browser?.isConnected(), web_closed: !web?.listening, synthetic_api_closed: !api?.listening };
}
report.static_bytes_unchanged = true;
for (const [file, digest] of Object.entries(build.static_artifact_sha256)) if (sha(await readFile(path.join(dist, file))) !== digest) report.static_bytes_unchanged = false;
if (!report.static_bytes_unchanged || report.resource_close_errors?.length) report.passed = false;
const target = path.join(root, 'docs/research/studio-implementation/inputs-154-' + stamp + '.json'); await writeFile(target, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' }); console.log(JSON.stringify({ passed: report.passed, report: relative(target), failure: report.failure, cleanup: report.cleanup })); if (!report.passed) process.exitCode = 1;
