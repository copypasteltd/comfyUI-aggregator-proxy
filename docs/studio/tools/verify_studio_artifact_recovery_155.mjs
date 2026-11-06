/** Resume an exact, closed, owned profile; inspect its original successful jobs without submitting again. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';
import { assertStudioArtifact } from './studio_artifact_assertions.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url)), relative = (value) => path.relative(root, value).replaceAll('\\', '/');
const priorFile = path.resolve(root, process.argv[2]); assert(priorFile.startsWith(path.join(root, 'docs/research/studio-implementation') + path.sep));
const prior = JSON.parse(await readFile(priorFile, 'utf8'));
assert(prior.real_business_api && prior.public_static_site && prior.submissions.length && allClosed(prior));
assert.equal(prior.browser_engine, webEngine, 'An existing profile must stay with its original browser engine');
assert(/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(process.argv[3] || ''));
const originals = prior.submissions, connection = JSON.parse(await readFile(path.join(root, '.local/studio-ops/canvas-154-connection.json'), 'utf8'));
const catalog = JSON.parse(await readFile(path.join(root, '.local/studio-ops/canvas-154-catalog.json'), 'utf8')).models.data;
const marker = JSON.parse(await readFile(path.join(root, '.local/studio-ops/canvas-154-once-' + process.argv[3] + '.json'), 'utf8'));
assert(originals.some((item) => item.job_id === marker.job_id));
const originalTemporary = path.resolve(root, marker.temporary); assert(originalTemporary.startsWith(path.join(root, '.local/studio154') + path.sep));
const profiles = (await readdir(originalTemporary, { withFileTypes: true })).filter((item) => item.isDirectory() && /^profile-[a-f0-9-]{36}$/.test(item.name)); assert.equal(profiles.length, 1);
const stamp = new Date().toISOString().replaceAll(/[:.]/g, '-'), temporary = path.join(root, '.local/studio-recovery-155', 'artifacts-' + stamp), media = path.join(root, 'docs/media/studio-155', 'recovery-' + stamp);
await mkdir(temporary); await mkdir(media, { recursive: true });
const report = { passed: false, at: new Date().toISOString(), source_sha256: prior.source_sha256, original_browser_report: relative(priorFile), real_business_api: true, public_static_site: true, inspect_only: false, submissions: [], new_generation_posts: 0, local_artifacts: [], recovered_jobs: [], screenshots: [], errors: [], page_errors: [], cleanup: {}, scope: 'Original successful jobs and original owned browser profile only. Verify rendered media, committed Blob length/MIME/SHA and reload with the explicitly remembered test connection. Remove its remembered Key before closing. No replacement generation, upload, storage reset or API change.' };
const origin = 'http://43.165.167.179:39445';
let browser, context, page, video, started;
function allClosed(value) { return Object.values(value.cleanup || {}).every(Boolean) && value.cleanup?.browser_closed; }
async function shot(name) {
    const file = path.join(media, name + '.png'); await page.screenshot({ path: file, animations: 'disabled' });
    report.screenshots.push({ name, file: relative(file), sha256: createHash('sha256').update(await readFile(file)).digest('hex'), recording_seconds: (Date.now() - started) / 1000 });
}
try {
    browser = await launchStudioWebBrowser({ root, temporary: originalTemporary, report, existingProfile: path.join(originalTemporary, profiles[0].name) });
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', recordVideo: { dir: temporary } });
    page = await context.newPage(); video = page.video(); started = Date.now();
    page.on('pageerror', (error) => report.page_errors.push(error.message));
    page.on('request', (request) => {
        const url = new URL(request.url());
        if (url.origin === connection.origin && url.pathname.startsWith('/v1/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) report.errors.push('Unexpected mutating business request during original-job recovery');
    });
    await page.goto(origin + '/studio/config', { timeout: 60000 });
    await page.getByPlaceholder('http://服务器:端口').fill(connection.origin, { timeout: 60000 });
    await page.getByPlaceholder('输入管理员提供的 API Key').fill(connection.key);
    await page.getByRole('checkbox', { name: '在此浏览器保存 API Key（仅限可信设备）', exact: true }).check();
    await page.getByRole('button', { name: '连接并读取模型', exact: true }).click(); await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor();
    assert.equal(await page.getByPlaceholder('输入管理员提供的 API Key').inputValue(), '');
    await page.getByRole('link', { name: '模型', exact: true }).press('Enter'); await page.waitForURL('**/studio/models'); await page.locator('.studio-model-card').first().waitFor(); await shot('models-light');
    for (const original of originals) {
        assert(/^job_[a-f0-9]{32}$/.test(original.job_id));
        const model = catalog.find((item) => item.id === original.model); assert(model);
        await page.getByRole('link', { name: '任务', exact: true }).press('Enter'); await page.waitForURL('**/studio/tasks');
        const card = page.locator('.studio-task-card').filter({ has: page.getByText(original.job_id, { exact: true }) }); await card.waitFor();
        const [response] = await Promise.all([page.waitForResponse((item) => item.request().method() === 'GET' && item.url() === connection.origin + '/v1/jobs/' + original.job_id, { timeout: 45000 }), card.getByRole('button', { name: '查询原任务', exact: true }).click()]);
        const confirmed = await response.json(); assert.equal(confirmed.id, original.job_id); assert.equal(confirmed.status, 'succeeded');
        await card.getByText('已完成', { exact: true }).waitFor();
        await card.getByRole('link', { name: '返回画布', exact: true }).press('Enter'); await page.locator('[data-canvas-title]').waitFor();
        if (await page.getByText('载入产物', { exact: true }).count()) await page.getByRole('button', { name: '载入产物', exact: true }).click();
        const artifact = await assertStudioArtifact(page, original.job_id, model.usage.output.media_types); report.local_artifacts.push(artifact);
        report.recovered_jobs.push({ job_id: original.job_id, model: original.model, final_status: confirmed.status });
        await shot(original.model + '-original-result'); console.log(JSON.stringify({ original_job: original.job_id, committed_and_rendered: true, bytes: artifact.bytes, media_type: artifact.media_type }));
    }
    await page.reload(); await page.locator('.studio-connection.connected').waitFor(); await page.locator('[data-canvas-title]').waitFor();
    const last = originals.at(-1), model = catalog.find((item) => item.id === last.model);
    assert.deepEqual(await assertStudioArtifact(page, last.job_id, model.usage.output.media_types), report.local_artifacts.at(-1));
    report.remembered_connection_reload_checked = true; await shot('original-after-reload');
    assert.deepEqual(report.errors, []); assert.deepEqual(report.page_errors, []); report.passed = true;
} catch (error) {
    report.failure = String(error.stack).split(connection.key).join('[redacted]');
    if (page && !page.isClosed()) await shot('failure').catch(() => {});
} finally {
    if (page && !page.isClosed()) {
        try {
            await page.goto(origin + '/studio/config');
            const disconnect = page.getByRole('button', { name: '退出连接', exact: true });
            await disconnect.waitFor(); await disconnect.click(); await disconnect.waitFor({ state: 'detached' });
            report.persisted_test_key_removed = true;
        } catch { report.persisted_test_key_removed = false; }
    }
    if (page) await page.close().catch(() => {}); if (context) await context.close().catch(() => {}); if (browser) await browser.close();
    if (video) { const target = path.join(temporary, 'complete.webm'); await copyFile(await video.path(), target); report.recording_private = relative(target); }
    report.cleanup = { page_closed: !page || page.isClosed(), context_closed: !context || !context.pages().length, browser_closed: !browser?.isConnected() };
}
if (!allClosed(report) || report.resource_close_errors?.length || !report.persisted_test_key_removed) report.passed = false;
const target = path.join(root, 'docs/research/studio-implementation/artifact-recovery-155-' + webEngine + '-' + stamp + '.json');
await writeFile(target, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ passed: report.passed, report: relative(target), failure: report.failure, cleanup: report.cleanup }));
if (!report.passed) process.exitCode = 1;
