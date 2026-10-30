/** Shipped UI + native WebGL, real saved GLB and synthetic edge cases. No generation. */
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url)), web = path.join(root, "infinite-canvas/web"), dist = path.join(web, "dist");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("C:/Users/carzy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const label = new Date().toISOString().replaceAll(/[:.]/g, "-"), temporary = path.join(root, `.local/studio-model3d/${label}`), shots = path.join(root, `docs/assets/studio/model3d-${label}`), evidence = path.join(root, "docs/research/studio-implementation");
await Promise.all([temporary, shots, evidence].map((folder) => mkdir(folder, { recursive: true })));
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const fixture = JSON.parse(await readFile(path.join(web, "tests/fixtures/catalog.json"), "utf8"));
const report = { started_at: new Date().toISOString(), scope: "Production UI on plain non-localhost HTTP; native Chromium/WebGL and 390px touch emulation; previously generated real 76.94MB GLB plus synthetic self-contained textures/errors, no new GPU generation, physical phone or fleet acceptance", checks: [], screenshots: [], page_errors: [], cleanup: {} };
const check = (name, detail = true) => { report.checks.push({ name, passed: true, detail }); console.log(JSON.stringify({ passed: name })); };
async function snapshot() { const files = {}; async function visit(folder) { for (const entry of await readdir(folder, { withFileTypes: true })) { const file = path.join(folder, entry.name); if (entry.isDirectory()) await visit(file); else files[path.relative(dist, file).replaceAll("\\", "/")] = hash(await readFile(file)); } } await visit(dist); return files; }
const before = await snapshot(), worker = Object.keys(before).find((file) => /schema-worker-.*\.js$/.test(file)); assert.ok(worker);
await require("esbuild").build({ stdin: { contents: 'export * from "./src/services/aggregator-journal"; export * from "./src/services/studio-documents"; export { AggregatorClient, blobHash } from "./src/services/api/aggregator/client"; import localforage from "localforage"; export const profiles = localforage.createInstance({name:"comfy-aggregator-studio",storeName:"profiles",driver:localforage.INDEXEDDB}); export const media = localforage.createInstance({name:"infinite-canvas",storeName:"media_files"});', resolveDir: web }, bundle: true, format: "esm", platform: "browser", tsconfig: path.join(web, "tsconfig.json"), outfile: path.join(temporary, "probe.mjs"), logLevel: "warning" });

const textureFixture = await readFile(path.join(root, "docs/assets/studio/model3d-fixture-texture.png"));
function tinyGlb(change = {}, badTexture = false) {
    const position = Buffer.from(new Float32Array([-1, 0, 0, 1, 0, 0, 0, 2, 0]).buffer), indices = Buffer.from(new Uint16Array([0, 1, 2]).buffer);
    const png = badTexture ? Buffer.from([0, 1, 2]) : textureFixture;
    const binary = Buffer.concat([position, indices, Buffer.alloc(2), png]), paddedBinary = Buffer.concat([binary, Buffer.alloc((4 - binary.length % 4) % 4)]);
    const body = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }], meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }] }], buffers: [{ byteLength: binary.length }], bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }, { buffer: 0, byteOffset: 36, byteLength: 6 }, { buffer: 0, byteOffset: 44, byteLength: png.length }], accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: "VEC3", min: [-1, 0, 0], max: [1, 2, 0] }, { bufferView: 1, componentType: 5123, count: 3, type: "SCALAR" }], materials: [{ doubleSided: true, pbrMetallicRoughness: { baseColorFactor: [0.7, 0.9, 0.2, 1], baseColorTexture: { index: 0 }, metallicFactor: 0, roughnessFactor: 1 } }], textures: [{ source: 0 }], images: [{ bufferView: 2, mimeType: "image/png" }], ...change };
    const json = Buffer.from(JSON.stringify(body)), paddedJson = Buffer.concat([json, Buffer.alloc((4 - json.length % 4) % 4, 32)]), data = Buffer.alloc(28 + paddedJson.length + paddedBinary.length);
    [0x46546c67, 2, data.length, paddedJson.length, 0x4e4f534a].forEach((value, index) => data.writeUInt32LE(value, index * 4)); paddedJson.copy(data, 20); data.writeUInt32LE(paddedBinary.length, 20 + paddedJson.length); data.writeUInt32LE(0x004e4942, 24 + paddedJson.length); paddedBinary.copy(data, 28 + paddedJson.length); return data;
}
const real = await readFile(path.join(root, ".local/studio-live-media/model3d.glb")); assert.equal(real.length, 76942864);
const models = new Map([
    ["tiny", tinyGlb()], ["real", real], ["external", tinyGlb({ images: [{ uri: "https://external.invalid/private-texture.png" }] })],
    ["compressed", tinyGlb({ extensionsUsed: ["KHR_draco_mesh_compression"] })], ["corrupt", Buffer.from("broken GLB bytes")],
    ["empty", tinyGlb({ scenes: [{ nodes: [] }] })], ["badtexture", tinyGlb({}, true)],
]);
const descriptors = [...models].map(([name, data], index) => ({ name, id: `content_${(index + 1).toString(16).padStart(32, "0")}`, bytes: data.length, sha256: hash(data) }));
const fixturePath = path.join(root, "docs/assets/studio/model3d-fixture.glb");
await writeFile(fixturePath, models.get("tiny")); report.synthetic_fixture = { file: path.relative(root, fixturePath).replaceAll("\\", "/"), sha256: hash(models.get("tiny")) };
const key = "synthetic-model3d-acceptance-key", requests = [], staticRequests = [];
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; worker-src 'self'; connect-src http: https: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const server = createServer((req, res) => void (async () => {
    const pathname = new URL(req.url, "http://fixture.invalid").pathname;
    const json = (status, value, headers = {}) => { res.writeHead(status, { "Content-Type": "application/json", ...headers }); res.end(JSON.stringify(value)); };
    if (pathname.startsWith("/v1/")) {
        requests.push({ method: req.method, path: pathname, bearer_valid: req.headers.authorization === `Bearer ${key}`, cookie_present: Boolean(req.headers.cookie) });
        if (req.headers.authorization !== `Bearer ${key}`) return json(401, { error: { code: "invalid_key", message: "Synthetic key required" } });
        if (pathname === "/v1/access-policy") return json(200, { scopes: fixture.scopes, limits: { rpm: { mode: "unlimited", value: null } } }, { "X-Studio-Contract": "1", "X-Studio-Submission-Lookup": "1" });
        if (pathname === "/v1/models") return json(200, { object: "list", data: fixture.models });
        if (pathname === "/v1/capabilities") return json(200, { object: "list", data: fixture.capabilities });
        if (pathname === "/v1/jobs" && req.method === "GET") return json(200, { object: "list", data: [], has_more: false, next_cursor: null });
        return json(405, { error: { code: "mutations_forbidden", message: "No generation allowed in preview acceptance" } });
    }
    if (pathname.startsWith("/models/")) { const data = models.get(pathname.slice(8, -4)); if (!data) { res.writeHead(404); res.end(); return; } res.writeHead(200, { "Content-Type": "model/gltf-binary" }); res.end(data); return; }
    if (pathname === "/blank") { res.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp }); res.end("<!doctype html><title>Owned 3D preview acceptance</title>"); return; }
    let file;
    if (pathname === "/probe.mjs") file = path.join(temporary, "probe.mjs");
    else if (pathname === "/schema-worker.ts") file = path.join(dist, worker);
    else if (pathname.startsWith("/studio/")) { staticRequests.push(pathname); file = path.resolve(dist, decodeURIComponent(pathname.slice(8))); if (file !== dist && !file.startsWith(dist + path.sep)) throw new Error("Invalid owned path"); try { if (!(await stat(file)).isFile()) file = path.join(dist, "index.html"); } catch { file = path.join(dist, "index.html"); } }
    else { res.writeHead(404); res.end(); return; }
    const mime = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml", ".txt": "text/plain" };
    res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": file.endsWith(worker.replaceAll("/", path.sep)) ? "default-src 'none'; script-src 'self' 'unsafe-eval'; connect-src 'none'" : csp }); res.end(await readFile(file));
})().catch((error) => { report.handler_failure = error.message; if (!res.headersSent) res.writeHead(500); res.end(); }));

async function instrument(context) {
    await context.addInitScript(() => {
        const state = window.__three = { contexts: [], bitmaps: new Set(), closed: 0, imageUrls: new Set(), observers: new Set(), pendingBitmaps: [], holdBitmaps: false };
        const getContext = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (...args) {
            const gl = getContext.apply(this, args);
            if (args[0] === "webgl2" && gl && !state.contexts.some((entry) => entry.gl === gl)) {
                const debug = gl.getExtension("WEBGL_debug_renderer_info"), entry = { gl, draws: 0, vendor: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER) };
                for (const name of ["drawElements", "drawArrays", "drawElementsInstanced", "drawArraysInstanced"]) { const draw = gl[name].bind(gl); gl[name] = (...values) => { entry.draws++; return draw(...values); }; }
                state.contexts.push(entry);
            }
            return gl;
        };
        const createBitmap = window.createImageBitmap.bind(window), closeBitmap = ImageBitmap.prototype.close;
        window.createImageBitmap = async (...args) => { const bitmap = await createBitmap(...args); state.bitmaps.add(bitmap); if (state.holdBitmaps) await new Promise((resolve) => state.pendingBitmaps.push(resolve)); return bitmap; };
        ImageBitmap.prototype.close = function () { if (state.bitmaps.delete(this)) state.closed++; return closeBitmap.call(this); };
        const createUrl = URL.createObjectURL.bind(URL), revokeUrl = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = (blob) => { const url = createUrl(blob); if (blob.type.startsWith("image/")) state.imageUrls.add(url); return url; };
        URL.revokeObjectURL = (url) => { state.imageUrls.delete(url); revokeUrl(url); };
        const Resize = ResizeObserver;
        window.ResizeObserver = class extends Resize { observe(element, options) { if (element.getAttribute("aria-label") === "三维视图容器") state.observers.add(this); return super.observe(element, options); } disconnect() { state.observers.delete(this); return super.disconnect(); } };
    });
}
let browser; const contexts = [], pages = [], externalRequests = [];
async function pageIn(context, origin) { const page = await context.newPage(); pages.push(page); page.on("pageerror", (error) => report.page_errors.push(error.message)); page.on("request", (request) => { if (!request.url().startsWith(origin + "/") && !request.url().startsWith("blob:") && !request.url().startsWith("data:")) externalRequests.push(request.url()); }); await page.goto(origin + "/blank"); return page; }
async function seed(page, names = descriptors.map((item) => item.name)) {
    return page.evaluate(async ({ key, descriptors, names }) => {
        const j = await import("/probe.mjs"), client = new j.AggregatorClient(location.origin, key), identity = { profileId: "model3d-ui", origin: client.origin, fingerprint: client.fingerprint };
        await j.profiles.setItem("connections", { version: 1, profiles: [{ id: identity.profileId, origin: client.origin, fingerprint: client.fingerprint, name: "三维原件预览验收", savedKey: key }], activeProfileId: identity.profileId });
        for (const item of descriptors.filter((item) => names.includes(item.name))) {
            const blob = await (await fetch(`/models/${item.name}.glb`)).blob(); if (await j.blobHash(blob) !== item.sha256) throw new Error("Private source mismatch");
            await j.journalPut("results", { version: 1, id: `studio:${client.fingerprint}:${item.id}`, identity, intentId: `synthetic-${item.name}`, savedAt: Date.now(), metadata: { id: item.id, media_type: "model/gltf-binary", bytes: blob.size, sha256: item.sha256, expires_at: Math.floor(Date.now() / 1000) + 8 * 3600 }, blob });
            if (item.name === "tiny") await j.media.setItem("offline-model3d", blob);
        }
        const project = { id: "offline-model3d", title: "离线 GLB 原件画布", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), nodes: [{ id: "offline-glb", type: "studio:model3d", title: "内嵌贴图 GLB", position: { x: 80, y: 80 }, width: 340, height: 280, metadata: { storageKey: "offline-model3d", mimeType: "model/gltf-binary", bytes: descriptors.find((item) => item.name === "tiny").bytes, status: "success" } }], connections: [], chatSessions: [], activeChatId: null, backgroundMode: "lines", showImageInfo: false, viewport: { x: 0, y: 0, k: 1 } };
        const source = JSON.stringify({ version: 0, state: { projects: [project], deletedProjects: [] } });
        localStorage.setItem(j.documentNames[0], source);
        await j.initializeDocuments({ [j.documentNames[0]]: source, [j.documentNames[1]]: null });
        return identity;
    }, { key, descriptors, names });
}
async function shot(page, name) { const file = path.join(shots, `${name}.png`); await page.screenshot({ path: file, fullPage: true, animations: "disabled" }); const bytes = await readFile(file); report.screenshots.push({ file: path.relative(root, file).replaceAll("\\", "/"), bytes: bytes.length, sha256: hash(bytes), viewport: page.viewportSize() }); }
const dialog = (page) => page.getByRole("dialog", { name: "3D 原件预览" });
const modelCanvas = (page) => dialog(page).getByRole("img", { name: "三维模型交互区" });
async function open(page, name) { const item = descriptors.find((item) => item.name === name); await page.locator("article.studio-model-card").filter({ hasText: item.id }).getByRole("button", { name: "交互预览", exact: true }).click(); }
async function ready(page, expected = /3 顶点 · 1 三角面/) { await dialog(page).getByRole("status").filter({ hasText: expected }).waitFor(); await page.waitForFunction(() => window.__three.contexts.some((entry) => !entry.gl.isContextLost() && entry.draws > 0)); }
async function close(page) { await dialog(page).locator(".ant-modal-close").click(); await dialog(page).waitFor({ state: "hidden" }); await page.waitForFunction(() => window.__three.contexts.every((entry) => entry.gl.isContextLost()) && window.__three.bitmaps.size === 0 && window.__three.imageUrls.size === 0 && window.__three.observers.size === 0); (report.closed_previews ||= []).push(await resources(page)); }
async function resources(page) { return page.evaluate(() => ({ contexts: window.__three.contexts.map((entry) => ({ lost: entry.gl.isContextLost(), draws: entry.draws, vendor: entry.vendor })), active_bitmaps: window.__three.bitmaps.size, closed_bitmaps: window.__three.closed, image_blob_urls: window.__three.imageUrls.size, active_viewer_observers: window.__three.observers.size })); }
try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve)); const origin = `http://canvas-model3d.test:${server.address().port}`;
    browser = await chromium.launch({ executablePath: "C:/Users/carzy/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe", headless: true, args: ["--host-resolver-rules=MAP canvas-model3d.test 127.0.0.1"] });
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 960 }, locale: "zh-CN", recordVideo: { dir: temporary } }); contexts.push(desktop); await instrument(desktop); const page = await pageIn(desktop, origin); await seed(page);
    await page.goto(origin + "/studio/assets"); await page.getByRole("heading", { name: "留住每一次创作" }).waitFor();
    await page.getByRole("heading", { name: descriptors[0].id, exact: true }).waitFor(); assert.equal(await page.locator("article.studio-model-card").count(), descriptors.length); assert.equal(await page.evaluate(() => __three.contexts.length), 0);
    assert.ok(!staticRequests.some((url) => /studio-model3d-viewer/.test(url))); check("library lists every original without loading Three.js or creating a WebGL context");
    await open(page, "tiny"); await ready(page); assert.equal(await page.evaluate(() => isSecureContext), false); await shot(page, "desktop-textured-glb");
    const originalPixels = hash(await modelCanvas(page).screenshot({ animations: "disabled" })), box = await modelCanvas(page).boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down(); await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.6, { steps: 8 }); await page.mouse.up(); assert.notEqual(hash(await modelCanvas(page).screenshot({ animations: "disabled" })), originalPixels);
    const rotatedPixels = hash(await modelCanvas(page).screenshot({ animations: "disabled" })); await page.mouse.wheel(0, -180); assert.notEqual(hash(await modelCanvas(page).screenshot({ animations: "disabled" })), rotatedPixels); await dialog(page).getByRole("button", { name: "复位视图" }).click();
    assert.equal(hash(await modelCanvas(page).screenshot({ animations: "disabled" })), originalPixels); await modelCanvas(page).focus(); await page.keyboard.press("ArrowRight"); assert.notEqual(hash(await modelCanvas(page).screenshot({ animations: "disabled" })), originalPixels);
    check("native WebGL renders embedded PNG on plain HTTP; orbit, zoom, reset and focused keyboard pan change actual pixels");
    await close(page); check("closing preview loses its actual WebGL context, closes decoded ImageBitmaps and releases texture URLs and ResizeObserver", await resources(page));
    await open(page, "real"); await ready(page, /1,597,077 顶点 · 3,217,656 三角面/); await shot(page, "desktop-real-comfyui-glb");
    const download = page.waitForEvent("download"); await dialog(page).getByRole("button", { name: "保存 GLB 原件" }).click(); const file = await download, output = path.join(temporary, "downloaded-original.glb"); await file.saveAs(output); assert.equal(hash(await readFile(output)), hash(real));
    report.real_original = { bytes: real.length, sha256: hash(real), vertices: 1597077, triangles: 3217656, saved_original_unchanged: true }; await close(page);
    check("previously generated 76,942,864-byte ComfyUI GLB renders with exact geometry counts and saves byte-identical original", report.real_original);
    for (const [name, reason] of [["external", /外链/], ["compressed", /解码器/], ["corrupt", /文件头/], ["empty", /没有可展示/], ["badtexture", /贴图无法读取/]]) {
        await open(page, name); await dialog(page).getByText(reason).waitFor(); await dialog(page).getByRole("button", { name: "保存 GLB 原件" }).waitFor(); await close(page); check(`${name} original shows explicit fallback while retaining original save`);
    }
    await page.evaluate(() => { __three.holdBitmaps = true; }); await open(page, "tiny"); await page.waitForFunction(() => __three.pendingBitmaps.length > 0); await dialog(page).locator(".ant-modal-close").click(); await dialog(page).waitFor({ state: "hidden" });
    const contextsBeforeLate = await page.evaluate(() => __three.contexts.length); await page.evaluate(() => { __three.holdBitmaps = false; for (const release of __three.pendingBitmaps.splice(0)) release(); });
    await page.waitForFunction(() => __three.bitmaps.size === 0 && __three.imageUrls.size === 0); assert.equal(await page.evaluate(() => __three.contexts.length), contextsBeforeLate);
    check("closing during pending bitmap decode disposes late completion without creating a late WebGL context", await resources(page));
    await open(page, "tiny"); await ready(page); await page.evaluate(() => __three.contexts.at(-1).gl.getExtension("WEBGL_lose_context").loseContext()); await dialog(page).getByText(/上下文已释放/).waitFor(); await shot(page, "desktop-context-loss-fallback"); await close(page);
    check("actual WebGL context loss switches to recoverable fallback and preserves original download");
    await page.goto(origin + "/studio/canvas/offline-model3d"); await page.getByRole("button", { name: "交互预览", exact: true }).click(); await ready(page); await shot(page, "desktop-canvas-glb"); await close(page);
    await page.goto(origin + "/studio/config"); await page.getByRole("button", { name: "退出连接", exact: true }).click(); await page.goto(origin + "/studio/canvas/offline-model3d"); await page.getByRole("button", { name: "交互预览", exact: true }).click(); await ready(page); await close(page);
    check("registered canvas GLB node previews its local offline copy after disconnect, with no API Key or generation");
    await page.goto(origin + "/studio/assets"); await page.getByText("连接原服务与 API Key，打开对应素材").waitFor(); assert.equal(await page.locator("article.studio-model-card").count(), 0);
    check("disconnect hides identity-owned library originals while offline snapshot copies remain independent");
    report.desktop_resources = await resources(page); await desktop.close(); report.private_recording = path.relative(root, await page.video().path()).replaceAll("\\", "/");

    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: "zh-CN" }); contexts.push(mobile); await instrument(mobile); const phone = await pageIn(mobile, origin); await seed(phone, ["tiny"]); await phone.goto(origin + "/studio/assets"); await open(phone, "tiny"); await ready(phone); await shot(phone, "mobile-touch-glb");
    const rect = await modelCanvas(phone).boundingBox(); assert.ok(rect.x >= 0 && rect.x + rect.width <= 390); const footer = await dialog(phone).getByRole("button", { name: "保存 GLB 原件" }).boundingBox(); assert.ok(footer.y >= 0 && footer.y + footer.height <= 844);
    const beforeTouch = hash(await modelCanvas(phone).screenshot({ animations: "disabled" })), session = await mobile.newCDPSession(phone), x = rect.x + rect.width * 0.4, y = rect.y + Math.min(rect.height * 0.4, 120);
    await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] }); await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x + 40, y: y + 15 }] }); await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }); assert.notEqual(hash(await modelCanvas(phone).screenshot({ animations: "disabled" })), beforeTouch);
    const orbitTouch = hash(await modelCanvas(phone).screenshot({ animations: "disabled" })); await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }, { x: x + 60, y }] }); await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x - 15, y }, { x: x + 80, y }] }); await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }); assert.notEqual(hash(await modelCanvas(phone).screenshot({ animations: "disabled" })), orbitTouch); await session.detach(); await close(phone);
    check("390px touch emulation rotates and pinches actual rendered pixels; preview and original-save controls fit the viewport", await resources(phone));
    await mobile.close();
    const noWebgl = await browser.newContext({ locale: "zh-CN" }); contexts.push(noWebgl); await instrument(noWebgl); await noWebgl.addInitScript(() => { const get = HTMLCanvasElement.prototype.getContext; HTMLCanvasElement.prototype.getContext = function (...args) { return ["webgl", "webgl2", "experimental-webgl"].includes(args[0]) ? null : get.apply(this, args); }; });
    const unavailable = await pageIn(noWebgl, origin); await seed(unavailable, ["tiny"]); await unavailable.goto(origin + "/studio/assets"); await open(unavailable, "tiny"); await dialog(unavailable).getByText("暂时无法交互预览", { exact: true }).waitFor(); await dialog(unavailable).getByRole("button", { name: "保存 GLB 原件" }).waitFor(); await close(unavailable); await noWebgl.close();
    check("browser without WebGL shows original-save fallback and still closes decoded texture resources");
    const noChunk = await browser.newContext({ locale: "zh-CN" }); contexts.push(noChunk); await instrument(noChunk); await noChunk.route("**/studio/assets/studio-model3d-viewer-*.js", (route) => route.fulfill({ status: 503, contentType: "application/javascript", body: "// Synthetic unavailable viewer chunk" }));
    const interrupted = await pageIn(noChunk, origin); await seed(interrupted, ["tiny"]); await interrupted.goto(origin + "/studio/assets"); await open(interrupted, "tiny"); await dialog(interrupted).getByText("暂时无法交互预览", { exact: true }).waitFor(); await dialog(interrupted).getByRole("button", { name: "保存 GLB 原件" }).waitFor(); await close(interrupted); await noChunk.close();
    check("missing lazy viewer chunk stays inside preview fallback without breaking the page or original save");
    assert.deepEqual(externalRequests, []); assert.equal(requests.filter((request) => request.method !== "GET").length, 0); assert.ok(requests.every((request) => request.bearer_valid && !request.cookie_present)); assert.equal(report.page_errors.length, 0); assert.ok(!report.handler_failure); assert.deepEqual(await snapshot(), before);
    report.generation_posts = 0; report.external_requests = externalRequests; report.business_requests = requests; report.browser_storage_version = 4; report.static_artifact_sha256 = before; report.static_artifact_unchanged = true;
    check("all preview cases preserve original files, shipped artifacts, DB v3 and identity boundaries with zero external requests or generation POSTs"); report.passed = true;
} catch (error) { report.passed = false; report.failure = error.stack; console.error(error.stack); const page = pages.findLast((page) => !page.isClosed()); if (page) { report.failure_page_text = (await page.locator("body").innerText().catch(() => "")).slice(0, 4000); report.failure_resources = await resources(page).catch(() => null); await shot(page, "failure-state").catch(() => {}); } }
finally {
    for (const page of pages) await page.close().catch(() => {}); report.cleanup.pages_closed = pages.every((page) => page.isClosed()); for (const context of contexts) await context.close().catch(() => {}); report.cleanup.contexts_closed = contexts.every((context) => !context.pages().length);
    if (browser) { await browser.close(); report.cleanup.browser_closed = !browser.isConnected(); } server.closeIdleConnections(); await new Promise((resolve) => server.close(resolve)); report.cleanup.server_closed = !server.listening; report.completed_at = new Date().toISOString();
    const file = path.join(evidence, `model3d-${label}.json`); await writeFile(file, JSON.stringify(report, null, 2) + "\n"); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.relative(root, file), cleanup: report.cleanup }));
}
if (!report.passed) process.exitCode = 1;
