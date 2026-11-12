/** Verify the actual default endpoint and read-only HTTPS catalog; close every owned browser resource. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { launchStudioWebBrowser } from './studio_web_browser.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const buildFile = path.resolve(root, process.argv[2]);
const build = JSON.parse(await readFile(buildFile, 'utf8'));
assert(build.passed && build.source_before === build.source_after);
const clean = JSON.parse(await readFile(path.resolve(root, build.clean_install_reused.file), 'utf8'));
const dist = path.resolve(root, clean.private_logs, 'repository/web/dist');
assert(dist.startsWith(path.join(root, '.local') + path.sep));
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
async function verifyBytes() {
    for (const [file, hash] of Object.entries(build.static_artifact_sha256)) assert.equal(sha(await readFile(path.join(dist, file))), hash);
}
const publicSite = process.argv.includes('--public'), api = 'https://gen.sub2share.xyz';
const stamp = new Date().toISOString().replaceAll(/[:.]/g, '-');
const temporary = path.join(root, '.local/studio156', stamp), media = path.join(root, 'docs/media/studio-156', stamp);
await mkdir(temporary, { recursive: true }); await mkdir(media, { recursive: true });
const report = { passed: false, scope: 'Default HTTPS connection, actual read-only business catalog and unchanged saved connection metadata; no generation or API configuration change', public_static_site: publicSite, build_report: path.relative(root, buildFile).replaceAll('\\', '/'), source_sha256: build.source_after, default_api_origin: api, requests: [], screenshots: [], checks: [], page_errors: [], cleanup: {}, virtualization_used: false };
let server, browser, site, secret = '';
const contexts = [], pages = [], forbidden = [];
try {
    await verifyBytes();
    if (publicSite) site = 'http://43.165.167.179:39445';
    else {
        const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' };
        server = createServer(async (req, res) => {
            try {
                const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
                let file = path.resolve(dist, '.' + pathname.replace(/^\/studio(?=\/|$)/, ''));
                assert(file.startsWith(dist + path.sep) || file === dist);
                if (!path.extname(pathname) || (await stat(file).catch(() => null))?.isDirectory()) file = path.join(dist, 'index.html');
                const bytes = await readFile(file); res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }); res.end(bytes);
            } catch { res.writeHead(404); res.end(); }
        });
        await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
        site = 'http://127.0.0.1:' + server.address().port;
    }
    browser = await launchStudioWebBrowser({ root, temporary, report, args: ['--no-proxy-server'] });
    async function open() {
        const context = await browser.newContext({ viewport: { width: 1440, height: 1024 } }); contexts.push(context);
        await context.addCookies([{ name: 'studio-https-probe', value: 'synthetic-cookie', url: api }]);
        const page = await context.newPage(); pages.push(page);
        page.on('pageerror', (error) => report.page_errors.push(error.message));
        await page.route('**/*', async (route) => {
            const request = route.request(), url = new URL(request.url());
            if (url.origin === api) {
                const headers = await request.allHeaders();
                report.requests.push({ url: url.origin + url.pathname, method: request.method(), cookie_present: !!headers.cookie });
                if (!['GET', 'OPTIONS'].includes(request.method()) || !url.pathname.startsWith('/v1/') || headers.cookie) { forbidden.push(url.origin + url.pathname); return route.abort(); }
            } else if (['http:', 'https:'].includes(url.protocol) && url.origin !== site) { forbidden.push(url.origin + url.pathname); return route.abort(); }
            return route.continue();
        });
        await page.goto(site + '/studio/config');
        await page.getByRole('heading', { name: '连接创作能力', exact: true }).waitFor();
        return page;
    }
    const page = await open();
    assert.equal(await page.getByPlaceholder('http://服务器:端口').inputValue(), api);
    assert.equal(report.requests.length, 0);
    report.checks.push('Fresh connection defaults to HTTPS without automatic business requests.');
    for (const theme of ['light', 'dark']) {
        if (theme === 'dark') await page.getByRole('button', { name: '切换到深色', exact: true }).click();
        const file = path.join(media, 'https-default-' + theme + '.png'); await page.screenshot({ path: file, fullPage: true });
        report.screenshots.push({ file: path.relative(root, file).replaceAll('\\', '/'), sha256: sha(await readFile(file)) });
    }
    const connection = JSON.parse(await readFile(path.join(root, '.local/studio-ops/canvas-154-connection.json'), 'utf8')); secret = connection.key;
    await page.getByPlaceholder('输入管理员提供的 API Key').fill(secret);
    await page.getByRole('button', { name: '连接并读取模型', exact: true }).click();
    await page.getByRole('button', { name: '刷新目录', exact: true }).waitFor();
    assert.equal(await page.getByPlaceholder('输入管理员提供的 API Key').inputValue(), '');
    assert.equal(await page.getByRole('link', { name: '打开管理后台', exact: true }).getAttribute('href'), api + '/admin/');
    await page.getByRole('link', { name: '模型', exact: true }).click();
    await page.locator('.studio-model-card').first().waitFor(); report.models_visible = await page.locator('.studio-model-card').count();
    for (const name of ['access-policy', 'models', 'capabilities']) assert(report.requests.some((request) => request.url === api + '/v1/' + name));
    report.checks.push('Unedited default connects to the real HTTPS catalog; all business requests omit cookies and use /v1/.');
    const connected = path.join(media, 'https-models.png'); await page.screenshot({ path: connected, fullPage: true });
    report.screenshots.push({ file: path.relative(root, connected).replaceAll('\\', '/'), sha256: sha(await readFile(connected)) });
    const saved = await open();
    await saved.evaluate(async () => {
        const request = indexedDB.open('comfy-aggregator-studio');
        const db = await new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        try {
            const tx = db.transaction('profiles', 'readwrite');
            tx.objectStore('profiles').put({ version: 1, profiles: [{ id: 'https-default-legacy-proof', name: '已有连接', origin: 'http://43.165.167.179:39443', fingerprint: 'a'.repeat(64) }], activeProfileId: 'https-default-legacy-proof' }, 'connections');
            await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
        } finally { db.close(); }
    });
    const before = report.requests.length; await saved.reload();
    await saved.getByRole('combobox', { name: '已保存的连接', exact: true }).click();
    await saved.getByText('已有连接 · aaaaaaaa', { exact: true }).last().click();
    assert.equal(await saved.getByPlaceholder('http://服务器:端口').inputValue(), 'http://43.165.167.179:39443');
    assert.equal(report.requests.length, before);
    report.checks.push('Saved legacy endpoint stays intact and is not contacted or silently migrated.');
    assert.deepEqual(forbidden, []); assert.deepEqual(report.page_errors, []);
    await verifyBytes(); report.static_bytes_unchanged = true; report.passed = true;
} catch (error) { report.failure = String(error.stack || error).split(secret || '\0').join('[redacted]'); }
finally {
    await Promise.allSettled(pages.map((page) => page.close()));
    const closed = await Promise.allSettled(contexts.map((context) => context.close()));
    if (browser) await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
    report.cleanup = { pages_closed: pages.every((page) => page.isClosed()), contexts_closed: closed.every((item) => item.status === 'fulfilled'), browser_closed: !browser?.isConnected(), static_server_closed: !server?.listening };
}
assert(!secret || !JSON.stringify(report).includes(secret));
const file = path.join(root, 'docs/research/studio-implementation', 'https-default-156-' + (publicSite ? 'public-' : 'local-') + stamp + '.json');
await writeFile(file, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ passed: report.passed, report: path.relative(root, file).replaceAll('\\', '/'), checks: report.checks, models: report.models_visible, cleanup: report.cleanup, failure: report.failure }));
if (!report.passed || !Object.values(report.cleanup).every(Boolean)) process.exitCode = 1;
