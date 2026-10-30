import test from "node:test";
import assert from "node:assert/strict";
import { describeCleanupSnapshot } from "../src/services/studio-local-media-cleanup";
import { documentNames, documentReferences, readDocument } from "../src/services/studio-documents";
import { validManagedMedia } from "../src/services/studio-local-media-types";

const key = "image:managed-v1:original";
const identity = { profileId: "synthetic", origin: "http://fixture.invalid", fingerprint: "synthetic" };
const doc = (name: typeof documentNames[number], state: unknown) => {
    const value = JSON.stringify({ version: 0, state });
    return { version: 1 as const, id: name, revision: 1, value, storageKeys: documentReferences(name, value), legacyValue: null };
};
const snapshot = () => ({ editors: [] as unknown[], local_media: [{ version: 1, id: key, kind: "image", blob: new Blob(["original"], { type: "image/png" }), bytes: 8, mimeType: "image/png", createdAt: 1 }], documents: [doc(documentNames[0], { projects: [], deletedProjects: [] }), doc(documentNames[1], { assets: [] })], intents: [] as unknown[], uploads: [] as unknown[], results: [] as unknown[] });

test("controlled originals share full document references including recoverable trash", () => {
    const value = snapshot();
    value.documents[0] = doc(documentNames[0], { projects: [], deletedProjects: [{ project: { nodes: [{ metadata: { references: [key, key], prompt: "file:managed-v1:literal" } }] } }] });
    const current = describeCleanupSnapshot(value);
    assert.deepEqual(current.files[0].references, ["画布及可恢复回收站"]);
    assert.deepEqual(value.documents[0].storageKeys, [key]);
});

test("every live editor prevents cleanup, including an unrecognized claim", () => {
    const value = snapshot();
    assert.equal(describeCleanupSnapshot(value).canDelete, true);
    value.editors.push({ version: 999, id: "unrecognized" });
    const current = describeCleanupSnapshot(value);
    assert.equal(current.canDelete, false); assert.match(current.issues.join(""), /未知页面/);
});

test("frozen request references and upload records protect controlled originals", () => {
    const value = snapshot();
    value.intents.push({ version: 1, id: "task", identity, stage: "terminal", body: JSON.stringify({ inputs: { references: [key] } }), idempotencyKey: "fixed" });
    value.uploads.push({ version: 1, id: "upload", identity, blob: new Blob(), sha256: "a".repeat(64), idempotencyKey: "upload-fixed", source: { storageKey: key } });
    assert.deepEqual(describeCleanupSnapshot(value).files[0].references, ["冻结的原请求", "参考缓存"]);
});

test("unknown or damaged documents, references, tasks, uploads and originals refuse the whole cleanup", () => {
    const cases = [
        (value: ReturnType<typeof snapshot>) => { value.documents.pop(); },
        (value: ReturnType<typeof snapshot>) => { value.documents[0].storageKeys = ["studio:corrupt-index"]; },
        (value: ReturnType<typeof snapshot>) => { value.documents[0] = doc(documentNames[0], { projects: [{ references: [42] }] }); },
        (value: ReturnType<typeof snapshot>) => { value.intents.push({ version: 99 }); },
        (value: ReturnType<typeof snapshot>) => { value.uploads.push({ version: 1, blob: new Blob() }); },
        (value: ReturnType<typeof snapshot>) => { value.local_media[0].bytes++; },
    ];
    for (const corrupt of cases) { const value = snapshot(); corrupt(value); const current = describeCleanupSnapshot(value); assert.equal(current.canDelete, false); assert.ok(current.issues.length); }
});

test("exact previous studio-only index is derived from intact body without changing it", () => {
    const value = doc(documentNames[0], { projects: [{ nodes: [{ metadata: { references: [key, "studio:history"] } }] }] });
    value.storageKeys = ["studio:history"];
    const repaired = readDocument(value, documentNames[0]);
    assert.deepEqual(repaired.storageKeys, [key, "studio:history"]); assert.equal(repaired.value, value.value); assert.deepEqual(value.storageKeys, ["studio:history"]);
});

test("controlled media schema verifies immutable bytes, kind and declared MIME", () => {
    const value = snapshot().local_media[0]; assert.equal(validManagedMedia(value), true);
    assert.equal(validManagedMedia({ ...value, id: "image:legacy" }), false);
    assert.equal(validManagedMedia({ ...value, kind: "media" }), false);
    assert.equal(validManagedMedia({ ...value, mimeType: "image/jpeg" }), false);
});
