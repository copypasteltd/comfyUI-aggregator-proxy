import test from "node:test";
import assert from "node:assert/strict";
import { uploadCacheBlocker } from "../src/services/studio-upload-cache";
import { storageValueBytes } from "../src/services/local-storage-usage";
import type { InputUpload, Intent } from "../src/services/aggregator-journal";

const identity = { profileId: "profile", origin: "http://example.invalid", fingerprint: "a".repeat(64) };
const upload = (): InputUpload => ({ version: 1, id: "upload", identity, name: "参考.png", blob: new Blob(["original"]), sha256: "b".repeat(64), idempotencyKey: "original-upload", createdAt: 1, asset: { id: "asset", status: "ready", sha256: "b".repeat(64), bytes: 8, width: 1, height: 1, media_type: "image/png", expires_at: 1 } });
const intent = (input: unknown): Intent => ({ version: 1, identity, body: JSON.stringify({ input }) } as Intent);

test("acknowledged unreferenced upload is eligible even after cloud expiry", () => assert.equal(uploadCacheBlocker(upload(), []), null));
test("all task stages protect nested original reference IDs", () => {
    for (const stage of ["prepared", "submitting", "unknown", "accepted", "terminal"] as const) assert.match(uploadCacheBlocker(upload(), [{ ...intent({ references: [{ image: { asset_id: "asset" } }] }), stage }])!, /仍引用/);
});
test("source image and mask references are protected", () => {
    for (const role of ["source_image", "mask"]) assert.match(uploadCacheBlocker(upload(), [intent({ [role]: { asset_id: "asset" } })])!, /仍引用/);
});
test("another identity does not disclose or block unrelated references", () => assert.equal(uploadCacheBlocker(upload(), [{ ...intent({ asset_id: "asset" }), identity: { ...identity, fingerprint: "c".repeat(64) } }]), null));
test("pending and unknown uploads retain original bytes", () => assert.match(uploadCacheBlocker({ ...upload(), asset: undefined, error: "response lost" }, [])!, /未确认/));
test("mismatched acknowledgement retains original bytes", () => {
    for (const change of [{ sha256: "f".repeat(64) }, { bytes: 7 }, { status: "unknown" }]) assert.match(uploadCacheBlocker({ ...upload(), asset: { ...upload().asset!, ...change } } as InputUpload, [])!, /回执无法核对/);
});
test("corrupt and unknown-version task records prevent destructive cleanup", () => {
    for (const value of [{ ...intent({}), body: "{" }, { ...intent({}), body: "null" }, { ...intent({}), version: 2 }, { ...intent({}), identity: undefined }]) assert.ok(uploadCacheBlocker(upload(), [value as Intent]));
});
test("unrelated valid requests leave acknowledged cache eligible", () => assert.equal(uploadCacheBlocker(upload(), [intent({ references: [{ asset_id: "different" }], prompt: "asset" })]), null));
test("nested Blob and binary payloads contribute to storage estimates", () => {
    const blob = new Blob([new Uint8Array(4096)]);
    const value = { blob, nested: { image: new Blob([new Uint8Array(2048)]), samples: new Uint8Array(128) }, name: "参考图" };
    assert.ok(storageValueBytes(value) >= 4096 + 2048 + 128);
    assert.ok(storageValueBytes(value) > storageValueBytes({ ...value, blob: null }));
});
test("cyclic structured-clone records and collections can be metered without decoding media", () => {
    const value: Record<string, unknown> = { items: new Map([["image", new Blob([new Uint8Array(1024)])]]) }; value.self = value;
    assert.ok(storageValueBytes(value) >= 1024);
});
