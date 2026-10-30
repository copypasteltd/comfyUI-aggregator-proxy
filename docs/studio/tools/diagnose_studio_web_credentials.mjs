/** Minimal native fetch observation. It never imports or changes product code. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { launchStudioWebBrowser, webEngine } from './studio_web_browser.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url));
const bindAddress = process.argv.find((value) => value.startsWith('--bind-address='))?.slice(15);
assert(bindAddress === '192.168.31.95');
const label = webEngine + '-' + randomUUID(), temporary = path.join(root, '.local/studio147/credentials-' + label);
await mkdir(temporary, { recursive: true });
const report = { passed: false, scope: 'Minimal synthetic two-port native fetch diagnostic, no product code or real API; server observes only synthetic-cookie presence.', cases: [], received: [], cleanup: {} };
let web, api, browser, context, page, canvasOrigin;
try {
    api = createServer((request, response) => {
        const url = new URL(request.url, 'http://fixture.invalid');
        report.received.push({ path: url.pathname, phase: url.searchParams.get('phase'), method: request.method, cookie_present: Boolean(request.headers.cookie), cookie_synthetic_only: !request.headers.cookie || request.headers.cookie === 'synthetic-cookie=owned-fixture', bearer_present: Boolean(request.headers.authorization) });
        response.writeHead(request.method === 'OPTIONS' ? 204 : 200, { 'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': canvasOrigin, 'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Headers': 'Authorization', 'Access-Control-Allow-Methods': 'GET,OPTIONS',
            ...(url.pathname === '/set-cookie' ? { 'Set-Cookie': 'synthetic-cookie=owned-fixture; Path=/; SameSite=Lax' } : {}),
        });
        response.end(request.method === 'OPTIONS' ? undefined : JSON.stringify({ cookie_present: Boolean(request.headers.cookie) }));
    }); await new Promise((resolve) => api.listen(0, bindAddress, resolve));
    const apiOrigin = 'http://' + bindAddress + ':' + api.address().port;
    web = createServer((request, response) => { response.writeHead(200, { 'Content-Type': 'text/html' }); response.end('<!doctype html><meta charset="utf-8"><title>Owned native fetch diagnostic</title>'); });
    await new Promise((resolve) => web.listen(0, bindAddress, resolve)); canvasOrigin = 'http://' + bindAddress + ':' + web.address().port;
    browser = await launchStudioWebBrowser({ root, temporary, report }); context = await browser.newContext({ viewport: { width: 900, height: 650 } });
    page = await context.newPage(); await page.goto(canvasOrigin);
    const run = async (phase, credentials, authorization) => {
        const result = await page.evaluate(async ({ url, credentials, authorization }) => {
            const options = { credentials, ...(authorization ? { headers: { Authorization: 'Bearer synthetic-fetch-diagnostic' } } : {}) };
            const response = await fetch(url, options); return { requested_credentials: options.credentials, status: response.status, body: await response.json() };
        }, { url: apiOrigin + '/echo?phase=' + phase, credentials, authorization });
        report.cases.push({ phase, ...result });
    };
    await run('empty-omit', 'omit', true);
    await context.addCookies([{ name: 'synthetic-cookie', value: 'owned-fixture', url: apiOrigin }]);
    await run('protocol-cookie-omit-simple', 'omit', false);
    await run('protocol-cookie-omit-bearer', 'omit', true);
    await run('protocol-cookie-include', 'include', true);
    await context.clearCookies();
    const setter = await context.newPage(); await setter.goto(apiOrigin + '/set-cookie'); await setter.close();
    await run('http-cookie-omit-simple', 'omit', false);
    await run('http-cookie-omit-bearer', 'omit', true);
    await run('http-cookie-include', 'include', true);
    report.omit_cookies_absent = report.cases.filter((item) => item.requested_credentials === 'omit').every((item) => !item.body.cookie_present);
    report.include_positive_controls = report.cases.filter((item) => item.requested_credentials === 'include').every((item) => item.body.cookie_present);
    report.product_code_loaded = false; report.production_api_requests = 0;
    report.passed = report.include_positive_controls && report.received.every((item) => item.cookie_synthetic_only);
} catch (error) { report.error = error.stack; }
finally {
    if (context) await context.close(); if (browser) await browser.close();
    for (const server of [web, api]) if (server) await new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); });
    report.cleanup = { pages_closed: !context?.pages().length, browser_closed: !browser?.isConnected(), fixture_servers_closed: !web?.listening && !api?.listening };
    const file = path.join(root, 'docs/research/studio-implementation/Web-credentials-diagnostic-' + label + '.json');
    await writeFile(file, JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({ passed: report.passed, omit_cookies_absent: report.omit_cookies_absent, report: path.relative(root, file).replaceAll('\\', '/'), cleanup: report.cleanup, error: report.error }));
}
if (!report.passed || !Object.values(report.cleanup).every(Boolean)) process.exitCode = 1;
