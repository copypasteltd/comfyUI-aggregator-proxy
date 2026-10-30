/** One explicit real canvas generation through the original business API; never resubmit on uncertainty. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile, stat, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url)), source = path.join(root, 'infinite-canvas/web');
const require = createRequire(path.join(source, 'package.json')), sha = (data) => createHash('sha256').update(data).digest('hex');
const relative = (file) => path.relative(root, file).replaceAll('\\', '/');
const buildFile = path.resolve(root, process.argv[2]), build = JSON.parse(await readFile(buildFile, 'utf8'));
assert(build.passed && build.source_before === build.source_after);
const clean = JSON.parse(await readFile(path.join(root, build.clean_install_reused.file), 'utf8')), dist = path.join(root, clean.private_logs, 'repository/web/dist');
for (const [file, hash] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), hash);
const owned = JSON.parse(await readFile(path.join(root, '.local/studio150/live-business-key.json'), 'utf8'));
const catalog = JSON.parse(await readFile(path.join(root, '.local/studio150/live-catalog.json'), 'utf8'));
const candidates = catalog.models.data.filter((item) => item.usage.task_type === 'references_to_video' && item.usage.references.exact_reference_count === 2);
const choices = candidates.flatMap((model) => model.usage.presets.filter((preset) => catalog['access-policy'].spec_limits.allowed_qualities.includes(preset.quality)).map((preset) => ({ model, preset, work: preset.size.split('x').reduce((a, b) => Number(a) * Number(b)) * preset.seconds }))).sort((a, b) => a.work - b.work);
assert(choices.length); const { model, preset } = choices[0];
const stamp = new Date().toISOString().replaceAll(/[:.]/g, '-'), temporary = path.join(root, '.local/studio150/live-' + stamp), media = path.join(root, 'docs/media/studio-150/live-' + stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
const marker = path.join(root, '.local/studio150/live-submission-once.json');
await writeFile(marker, JSON.stringify({ at: new Date().toISOString(), private_runtime: relative(temporary), model: model.id, preset, instruction: 'One UI submission only. If incomplete, query the recorded original job; never run this creation script again.' }, null, 2) + '\n', { flag: 'wx' });
const report = { passed: false, at: new Date().toISOString(), build_report: relative(buildFile), scope: 'One real native Chromium canvas two-reference video request through the unchanged existing business API; not fleet, other media or complete browser acceptance.', model: model.id, preset, requests: [], checks: [], screenshots: [], page_errors: [], forbidden_requests: [], cleanup: {}, generation_posts: 0, product_timeouts_changed: false, test_observation_limit_minutes: 30 };
const check = (name, detail = true) => { report.checks.push({ name, detail }); console.log(JSON.stringify({ passed: name, detail })); };
const inputs = [];
for (const [i, name] of ['image.png', 'references-image.png'].entries()) { const file = path.join(root, 'docs/media/studio-131', name), data = await readFile(file); inputs.push({ key: 'image:managed-v1:live-reference-' + i, name: i ? '参考 B · 机器人造型' : '参考 A · 机器人主体', data: [...data], sha256: sha(data) }); }
const project = { id: 'live-reference-composer-150', title: '双参考机器人 · 真实视频生成', createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), viewport: { x: 0, y: 0, k: 0.8 }, backgroundMode: 'lines', showImageInfo: false, chatSessions: [], activeChatId: null, connections: [], nodes: inputs.map((input, i) => ({ id: 'live-reference-' + i, type: 'image', title: input.name, position: { x: 30 + i * 360, y: 80 }, width: 270, height: 360, metadata: { storageKey: input.key, status: 'success', mimeType: 'image/png' } })) };
await require('esbuild').build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export * from "./src/services/studio-local-media"; export {blobHash} from "./src/services/api/aggregator/client";', resolveDir: source }, bundle: true, platform: 'browser', format: 'esm', define: { 'import.meta.env.VITE_STUDIO_MODE': '"managed"' }, outfile: path.join(temporary, 'probe.mjs'), logLevel: 'warning' });
let web, browser, context, page, video, origin;
async function shot(name) { const file = path.join(media, name + '.png'); await page.screenshot({ path: file }); report.screenshots.push({ file: relative(file), bytes: (await stat(file)).size, sha256: sha(await readFile(file)) }); }
try {
    web = createServer(async (req, res) => { try {
        const pathname = new URL(req.url, 'http://owned.invalid').pathname;
        let file;
        if (pathname === '/probe.mjs') file = path.join(temporary, 'probe.mjs');
        else if (pathname === '/blank') { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end('<!doctype html><title>Owned real canvas test</title>'); }
        else { file = path.resolve(dist, decodeURIComponent(pathname.replace(/^\/studio\//, ''))); assert(pathname.startsWith('/studio/') && (file === dist || file.startsWith(dist + path.sep))); try { if (!(await stat(file)).isFile()) file = path.join(dist, 'index.html'); } catch { file = path.join(dist, 'index.html'); } }
        res.writeHead(200, { 'Content-Type': ({ '.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' })[path.extname(file)] || 'application/octet-stream', 'Content-Security-Policy': path.basename(file).startsWith('schema-worker-') ? "default-src 'self'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'" }); res.end(await readFile(file));
    } catch { res.writeHead(404); res.end(); } });
    await new Promise((resolve) => web.listen(0, '192.168.31.95', resolve)); origin = 'http://192.168.31.95:' + web.address().port;
    report.origins = { canvas: origin, existing_api: owned.origin };
    browser = await launchStudioWebBrowser({ root, temporary, report }); context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: temporary }, locale: 'zh-CN' }); page = await context.newPage(); video = page.video();
    page.on('pageerror', (error) => report.page_errors.push(error.message));
    page.on('request', (request) => { const url = new URL(request.url()); if (!['http:', 'https:'].includes(url.protocol)) return; if (![origin, owned.origin].includes(url.origin) || (url.origin === owned.origin && !url.pathname.startsWith('/v1/'))) report.forbidden_requests.push(url.origin + url.pathname); if (url.origin === owned.origin) { report.requests.push({ method: request.method(), path: url.pathname }); if (url.pathname === '/v1/jobs' && request.method() === 'POST') { report.generation_posts++; report.submitted_body = request.postDataJSON(); } } });
    await page.goto(origin + '/blank'); await page.evaluate(async ({ project, inputs }) => { const p = await import('/probe.mjs'); await p.initializeDocuments({ [p.documentNames[0]]: null, [p.documentNames[1]]: null }); for (const input of inputs) await p.addManagedMedia(input.key, new Blob([new Uint8Array(input.data)], { type: 'image/png' })); const doc = await p.journalGet('documents', p.documentNames[0]); await p.saveDocument(doc.id, JSON.stringify({ version: 0, state: { projects: [project], deletedProjects: [] } }), doc.revision); }, { project, inputs });
    await page.goto(origin + '/studio/config'); await page.getByPlaceholder('http://服务器:端口').fill(owned.origin); await page.getByPlaceholder('输入管理员提供的 API Key').fill(owned.secret); await page.getByRole('checkbox').check(); await page.getByRole('button', { name: '连接并读取模型', exact: true }).click(); await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor(); check('real original business API connection, policy and catalog');
    await page.goto(origin + '/studio/canvas/' + project.id); await page.waitForFunction(() => [...document.querySelectorAll('[data-node-id] img')].filter((img) => img.naturalWidth > 0).length === 2); await page.locator('main.studio-canvas-project > section').click({ position: { x: 850, y: 580 } }); await page.keyboard.press('Control+a'); await page.getByRole('button', { name: '新建创作', exact: true }).click();
    const select = page.getByRole('combobox', { name: '生成模型', exact: true }); await select.click(); await select.fill(model.usage.display_name); await page.locator('.ant-select-item-option').filter({ hasText: model.usage.display_name }).first().click();
    await page.getByRole('textbox', { name: '创作描述', exact: true }).fill('Two friendly toy robots inspired by the reference images stand together on a clean studio tabletop. Preserve their distinctive faces and orange accents. Both gently wave, subtle natural motion, fixed camera, soft consistent lighting, no text, no watermark.');
    await page.waitForFunction(() => document.querySelectorAll('[data-studio-reference-preview] img').length === 2 && [...document.querySelectorAll('[data-studio-reference-preview] img')].every((img) => img.naturalWidth > 0)); await shot('real-two-reference-form'); check('two existing canvas originals fill the published reference slots');
    const responsePromise = page.waitForResponse((response) => response.url() === owned.origin + '/v1/jobs' && response.request().method() === 'POST'); await page.getByRole('button', { name: '提交 1 项生成任务', exact: true }).click(); const response = await responsePromise, accepted = await response.json();
    await writeFile(path.join(temporary, 'accepted-original-job.json'), JSON.stringify({ status: response.status(), body: accepted }, null, 2) + '\n', { flag: 'wx' });
    assert(response.ok(), 'Original service did not accept the request; inspect the recorded original response without retrying'); report.job_id = accepted.id; check('one real generation accepted by the original API', { job_id: accepted.id, status: accepted.status }); await shot('real-task-accepted');
    let latest = accepted.status, state; const started = Date.now();
    while (Date.now() - started < 30 * 60 * 1000) {
        state = await page.evaluate(async (id) => { const p = await import('/probe.mjs'); const intent = (await p.journalList('intents')).find((item) => item.job?.id === id); return intent ? { nodeId: intent.nodeId, job: intent.job, sourceIds: intent.canvas.sourceIds, error: intent.error } : null; }, accepted.id);
        if (state?.job.status !== latest) { latest = state?.job.status; console.log(JSON.stringify({ original_job_status: latest, job_id: accepted.id })); }
        if (['succeeded', 'failed', 'cancelled', 'expired'].includes(latest)) break;
        await page.waitForTimeout(1000);
    }
    report.final_job_status = latest; assert.equal(latest, 'succeeded', 'Observe/query the known original job; never submit a replacement');
    assert.deepEqual(state.sourceIds, ['live-reference-0', 'live-reference-1']);
    await page.locator(`[data-node-id="${state.nodeId}"]`).getByRole('button', { name: '载入产物', exact: true }).click();
    await page.waitForFunction(async (id) => { const p = await import('/probe.mjs'); return (await p.journalList('results')).some((item) => item.jobId === id); }, accepted.id);
    const saved = await page.evaluate(async (id) => { const p = await import('/probe.mjs'); const result = (await p.journalList('results')).find((item) => item.jobId === id); return result ? { metadata: result.metadata, bytes: [...new Uint8Array(await result.blob.arrayBuffer())], sha256: await p.blobHash(result.blob) } : null; }, accepted.id); assert(saved);
    const data = Buffer.from(saved.bytes); assert.equal(sha(data), saved.metadata.sha256); assert.equal(data.length, saved.metadata.bytes); assert.equal(saved.metadata.media_type, 'video/mp4');
    const output = path.join(media, 'real-two-reference-original.mp4'); await writeFile(output, data, { flag: 'wx' }); report.original = { file: relative(output), bytes: data.length, sha256: sha(data), media_type: saved.metadata.media_type, content_id: saved.metadata.id };
    await page.waitForFunction(async ({ id, projectId }) => { const p = await import('/probe.mjs'); const doc = await p.journalGet('documents', p.documentNames[0]); return JSON.parse(doc.value).state.projects.find((item) => item.id === projectId).nodes.some((node) => node.metadata?.studioJobId === id && node.metadata?.storageKey); }, { id: accepted.id, projectId: project.id });
    await shot('real-video-returned-to-canvas'); check('real downloaded MP4 matches server length/hash and returns to the saved canvas with both source links', report.original);
    assert.equal(report.generation_posts, 1); assert.deepEqual(report.page_errors, []); assert.deepEqual(report.forbidden_requests, []); report.passed = true;
} catch (error) { report.failure = error.stack; if (page && !page.isClosed()) await shot('failure').catch(() => {}); }
finally {
    if (page) await page.close().catch(() => {}); if (context) await context.close().catch(() => {}); if (browser) await browser.close(); if (web) await new Promise((resolve) => { web.closeAllConnections(); web.close(resolve); });
    if (video) { try { const file = path.join(temporary, 'complete-page.webm'); await copyFile(await video.path(), file); report.full_selected_page_recording = relative(file); } catch (error) { report.video_failure = error.message; } }
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: !context || !context.pages().length, browser_closed: !browser?.isConnected(), native_static_server_closed: !web?.listening };
}
const target = path.join(root, 'docs/research/studio-implementation/composer-live-' + stamp + '.json'); await writeFile(target, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' }); console.log(JSON.stringify({ passed: report.passed, job_id: report.job_id, final_job_status: report.final_job_status, report: relative(target), cleanup: report.cleanup, failure: report.failure })); if (!report.passed) process.exitCode = 1;
