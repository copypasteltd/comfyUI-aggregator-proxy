/** Owned native Web acceptance profiles; diagnostic bounds never change product behavior. */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, realpath } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';

export const webEngine = process.argv.find((value) => value.startsWith('--browser='))?.slice(10) || 'chromium';
assert(['chromium', 'firefox', 'webkit'].includes(webEngine));
export const webEngineArgument = (value) => value === '--browser=' + webEngine || value.startsWith('--sdk=');

export async function launchStudioWebBrowser({ root, temporary, report, args = [] }) {
    const require = createRequire(path.join(root, 'infinite-canvas/web/package.json'));
    const sdk = process.argv.find((value) => value.startsWith('--sdk='))?.slice(6) || 'D:/dev/copypasted/戴登移民/dev/node_modules/playwright-core';
    const playwright = require(sdk), metadataBytes = await readFile(path.join(sdk, 'browsers.json'));
    const metadata = JSON.parse(metadataBytes).browsers.find((item) => item.name === webEngine);
    const executablePath = {
        chromium: 'C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe',
        firefox: 'C:/Users/carzy/AppData/Local/ms-playwright/firefox-1543/firefox/firefox.exe',
        webkit: 'C:/Users/carzy/AppData/Local/ms-playwright/webkit-2359/Playwright.exe',
    }[webEngine];
    assert(executablePath.includes('/' + webEngine + '-' + metadata.revision + '/'), 'SDK metadata must match installed native browser revision');
    const localRoot = await realpath(path.join(root, '.local')), junction = 'E:/dev/sandbox/.comfy-studio-native';
    assert.equal(await realpath(junction), localRoot);
    const actual = await realpath(temporary), relative = path.relative(localRoot, actual);
    assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative));
    const runtime = path.join(junction, relative); assert.equal(await realpath(runtime), actual);
    process.env.TEMP = runtime; process.env.TMP = runtime;
    const contexts = [], browsers = [];
    report.browser_engine = webEngine;
    report.native_browser = { engine: webEngine, playwright_version: require(sdk + '/package.json').version,
        sdk_browser_metadata_sha256: createHash('sha256').update(metadataBytes).digest('hex'),
        native_revision: metadata.revision, executable: executablePath,
        persistent_owned_profiles: true, ascii_runtime_verified: true, isolated_profiles: 0,
        test_observation_timeout_ms: 20000, product_timeouts_changed: false };
    return {
        async newContext(options) {
            const profile = path.join(runtime, 'profile-' + randomUUID()); await mkdir(profile);
            const context = await playwright[webEngine].launchPersistentContext(profile, {
                executablePath, headless: true, ...options,
                ...(webEngine === 'chromium' ? { args } : {}),
            });
            contexts.push(context); const browser = context.browser(); if (browser) browsers.push(browser);
            report.browser_version = browser?.version(); report.native_browser.isolated_profiles++;
            context.setDefaultTimeout(20000); context.setDefaultNavigationTimeout(20000);
            // Firefox needs a live window until the first test page exists.
            const initial = context.pages(), newPage = context.newPage.bind(context);
            context.newPage = async (...parameters) => {
                const page = await newPage(...parameters);
                for (const blank of initial) if (!blank.isClosed()) await blank.close();
                return page;
            };
            return context;
        },
        async close() {
            const settled = await Promise.allSettled(contexts.map((context) => context.close()));
            const stopped = await Promise.allSettled(browsers.map((browser) => browser.close()));
            report.resource_close_errors = [...settled, ...stopped].filter((item) => item.status === 'rejected').map((item) => item.reason.message);
        },
        isConnected() { return browsers.some((browser) => browser.isConnected()); },
        version() { return browsers[0]?.version(); },
    };
}
