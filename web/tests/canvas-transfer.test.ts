import test from "node:test";
import assert from "node:assert/strict";
import { withoutCredentials, validateCanvasArchive, importedCanvasSnapshot } from "../src/lib/canvas/canvas-transfer";
import type { CanvasProject } from "../src/stores/canvas/use-canvas-store";

const project = { id: "project-a", title: "local snapshot", createdAt: "", updatedAt: "", nodes: [
    { id: "image-a", title: "Image", type: "image", position: { x: 0, y: 0 }, width: 100, height: 100, metadata: { storageKey: "studio:identity:content_1", content: "blob:old-object-url", studioIntentId: "private-intent" } },
    { id: "task-a", title: "Task", type: "studio:task", position: { x: 100, y: 0 }, width: 100, height: 100, metadata: { studioIntentId: "unknown-submit", studioJobId: "job_original" } },
], connections: [{ id: "edge-a", fromNodeId: "image-a", toNodeId: "task-a" }], chatSessions: [], activeChatId: null, backgroundMode: "lines", showImageInfo: false, viewport: { x: 0, y: 0, k: 1 } } as CanvasProject;
const archive = { app: "infinite-canvas", version: 3, exportedAt: "", studio: { format_version: 1, semantics: "local-media-snapshot" }, projects: [{ project, files: [{ storageKey: "studio:identity:content_1", path: "projects/project-a/file.png", bytes: 10, mimeType: "image/png", sha256: "a".repeat(64) }] }] };

test("canvas export omits nested credentials and URL credential parameters without changing source", () => {
    const source = { apiKey: "secret", nested: [{ password: "pw", text: "prompt", access_token: "bearer" }], url: "http://user:pass@example.test/v1?apiKey=secret&format=png" };
    const result = withoutCredentials(source);
    assert.deepEqual(result.nested, [{ text: "prompt" }]);
    assert.equal("apiKey" in result, false);
    assert.equal(result.url, "http://example.test/v1?format=png");
    assert.equal(source.apiKey, "secret");
});
test("canvas archive rejects unknown formats, missing hash, invalid files and dangling connections", () => {
    assert.doesNotThrow(() => validateCanvasArchive(archive));
    for (const change of [
        { ...archive, version: 4 }, { ...archive, studio: { format_version: 2, semantics: "local-media-snapshot" } },
        { ...archive, projects: [{ project, files: [{ ...archive.projects[0].files[0], sha256: undefined }] }] },
        { ...archive, projects: [{ project, files: [{ ...archive.projects[0].files[0], path: "../private/file.png" }] }] },
        { ...archive, projects: [{ project, files: [] }] },
        { ...archive, projects: [{ project: { ...project, connections: [{ id: "bad", fromNodeId: "missing", toNodeId: "image-a" }] }, files: [] }] },
    ]) assert.throws(() => validateCanvasArchive(change));
});
test("import creates an offline copy with remapped bytes and never adopts or resubmits original tasks", () => {
    const copied = importedCanvasSnapshot(project, new Map([["studio:identity:content_1", "image:import-new"]]));
    assert.equal(copied.nodes[0].metadata?.storageKey, "image:import-new");
    assert.equal(copied.nodes[0].metadata?.content, "");
    assert.equal(copied.nodes[0].metadata?.studioIntentId, undefined);
    assert.notEqual(copied.nodes[1].type, "studio:task");
    assert.match(copied.nodes[1].metadata?.content || "", /不会重新提交/);
    assert.equal(project.nodes[0].metadata?.studioIntentId, "private-intent");
});

test("archive refuses malformed render state before any media or project is written", () => {
    const change = (patch: Record<string, unknown>) => ({ ...archive, projects: [{ project: { ...project, ...patch }, files: archive.projects[0].files }] });
    for (const patch of [
        { title: { unsafe: "not text" } }, { id: "" }, { nodes: [{ ...project.nodes[0], title: {} }] },
        { viewport: { x: 0, y: 0, k: 0 } }, { viewport: { x: 0, y: Infinity, k: 1 } },
        { backgroundMode: "unknown" }, { showImageInfo: "true" },
        { nodes: [{ ...project.nodes[0], metadata: { images: {}, content: "" } }] },
        { nodes: [{ ...project.nodes[0], metadata: { content: { html: "not text" } } }] },
    ]) assert.throws(() => validateCanvasArchive(change(patch)), JSON.stringify(patch));
});

test("archive refuses duplicate project, edge and file paths and prototype-shaped fields", () => {
    const item = archive.projects[0];
    for (const value of [
        { ...archive, projects: [item, item] },
        { ...archive, projects: [{ ...item, project: { ...project, connections: [project.connections[0], project.connections[0]] } }] },
        { ...archive, projects: [{ ...item, files: [item.files[0], { ...item.files[0], storageKey: "image:different" }] }] },
        JSON.parse(JSON.stringify(archive).replace('"title":"local snapshot"', '"title":"local snapshot","__proto__":{"polluted":true}')),
    ]) assert.throws(() => validateCanvasArchive(value));
    assert.equal(({} as Record<string, unknown>).polluted, undefined);
});

test("credential export covers cookies, header keys and URL fragment credentials while preserving prompt text", () => {
    const source = {
        headers: { Cookie: "private-cookie", "X-Api-Key": "private-header", Authorization: "private-bearer", Accept: "image/png" },
        nested: { sessionToken: "private-session", refreshToken: "private-refresh", prompt: "literal token=art and <script> stay text" },
        direct: "http://user:pass@example.test/v1?format=png#api_key=private-fragment&view=full",
        routed: "http://example.test/#/canvas?access_token=private-route&node=first",
        anchor: "https://example.test/art#section-one",
    };
    const result = withoutCredentials(source);
    assert.deepEqual(result.headers, { Accept: "image/png" });
    assert.deepEqual(result.nested, { prompt: source.nested.prompt });
    assert.equal(result.direct, "http://example.test/v1?format=png#view=full");
    assert.equal(result.routed, "http://example.test/#/canvas?node=first");
    assert.equal(result.anchor, source.anchor);
    assert.equal(source.headers.Cookie, "private-cookie");
});

test("archive requires originals referenced only by typed local-reference lists without treating prompt text or external URLs as local files", () => {
    const referenced = structuredClone(archive);
    const metadata = referenced.projects[0].project.nodes[0].metadata!;
    Object.assign(metadata, { prompt: "image:literal-prompt", references: ["image:history-only", "file:audio-only", "studio:identity:content_1", "https://example.test/reference.png", "image:history-only"] });
    for (const key of ["image:history-only", "file:audio-only"]) referenced.projects[0].files.push({ ...archive.projects[0].files[0], storageKey: key, path: `files/${key.replace(":", "-")}.bin` });
    assert.doesNotThrow(() => validateCanvasArchive(referenced));
    for (const missing of ["image:history-only", "file:audio-only"]) {
        const incomplete = structuredClone(referenced);
        incomplete.projects[0].files = incomplete.projects[0].files.filter((file) => file.storageKey !== missing);
        assert.throws(() => validateCanvasArchive(incomplete), missing);
    }
});

test("offline import remaps ordered local-reference keys including duplicates and retains prompt literals and ordinary URLs", () => {
    const source = structuredClone(project);
    Object.assign(source.nodes[0].metadata!, { prompt: "image:history-only", generationType: "edit", references: ["image:history-only", "file:audio-only", "studio:identity:content_1", "image:history-only", "https://example.test/reference.png", "blob:expired-reference"] });
    const copied = importedCanvasSnapshot(source, new Map([["image:history-only", "image:new-reference"], ["file:audio-only", "file:new-audio"], ["studio:identity:content_1", "image:new-result"]]));
    assert.deepEqual(copied.nodes[0].metadata?.references, ["image:new-reference", "file:new-audio", "image:new-result", "image:new-reference", "https://example.test/reference.png", ""]);
    assert.equal(copied.nodes[0].metadata?.prompt, "image:history-only");
    assert.equal(source.nodes[0].metadata?.references?.[0], "image:history-only");
});
