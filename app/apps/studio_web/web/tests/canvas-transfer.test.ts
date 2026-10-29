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
