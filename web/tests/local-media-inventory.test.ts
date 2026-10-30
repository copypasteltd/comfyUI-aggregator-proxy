import test from "node:test";
import assert from "node:assert/strict";
import { buildLocalMediaInventory } from "../src/services/local-media-inventory";

test("inventory deduplicates reference sources without treating preview bytes as an original", () => {
    const value = buildLocalMediaInventory([{ key: "image:a", store: "image_files", bytes: 10, mediaType: "image/png" }, { key: "image:a", store: "image_previews", bytes: 3, mediaType: "image/webp" }], [{ label: "历史参考", keys: ["image:a", "image:a"] }, { label: "当前画布", keys: ["image:a"] }]);
    assert.equal(value.originalBytes, 10); assert.equal(value.previewRecords, 1); assert.equal(value.files.length, 1);
    assert.deepEqual(value.files[0].references, ["历史参考", "当前画布"]); assert.deepEqual(value.missing, []);
});
test("missing originals remain visible even when their previews exist; generated results belong to the separate library", () => {
    const value = buildLocalMediaInventory([{ key: "image:missing", store: "image_previews", bytes: 3, mediaType: "image/webp" }], [{ label: "已保存画布", keys: ["image:missing", "file:missing", "video:missing", "audio:missing", "studio:separate"] }]);
    assert.deepEqual(value.missing.map((item) => item.key), ["image:missing", "file:missing", "video:missing", "audio:missing"]);
    assert.equal(value.files.length, 0); assert.equal(value.originalBytes, 0);
});
test("unconfirmed files and unreadable records remain distinct, with warnings and no deletion plan", () => {
    const value = buildLocalMediaInventory([{ key: "file:unknown", store: "media_files", bytes: null, mediaType: "" }, { key: "image:staged", store: "image_files", bytes: 7, mediaType: "image/png" }], [], ["引用未完整核对", "引用未完整核对"]);
    assert.equal(value.files[0].bytes, null); assert.deepEqual(value.files[1].references, []); assert.equal(value.originalBytes, 7);
    assert.deepEqual(value.warnings, ["引用未完整核对"]); assert.equal("deletable" in value.files[1], false);
});
