import test from "node:test";
import assert from "node:assert/strict";
import { inspectStudioGlb } from "../src/lib/studio-glb";

function glb(fields: object = {}): ArrayBuffer {
    const json = new TextEncoder().encode(JSON.stringify({ asset: { version: "2.0" }, ...fields })), padded = Math.ceil(json.length / 4) * 4;
    const data = new ArrayBuffer(20 + padded), view = new DataView(data);
    [0x46546c67, 2, data.byteLength, padded, 0x4e4f534a].forEach((value, index) => view.setUint32(index * 4, value, true));
    new Uint8Array(data).fill(32, 20); new Uint8Array(data).set(json, 20); return data;
}
test("self-contained glTF 2.0 with embedded texture and supported extensions passes without changing bytes", () => {
    const data = glb({ images: [{ bufferView: 0, mimeType: "image/png" }], extensionsRequired: ["KHR_materials_unlit"], extensionsUsed: ["KHR_materials_unlit"] }), before = data.slice(0);
    assert.deepEqual(inspectStudioGlb(data), []); assert.deepEqual(data, before);
});
test("embedded base64 resources pass while optional unknown extensions remain visible", () => {
    assert.deepEqual(inspectStudioGlb(glb({ buffers: [{ uri: "data:application/octet-stream;base64,AA==" }], images: [{ uri: "data:image/webp;base64,AA==" }], extensionsUsed: ["VENDOR_optional"] })), ["VENDOR_optional"]);
});
for (const uri of ["https://external.invalid/texture.png", "//external.invalid/a.bin", "relative.bin", "file:///C:/private", "javascript:alert(1)", "blob:http://canvas.invalid/a", "data:image/svg+xml;base64,AAAA"]) test(`GLB external or executable URI is blocked before loader: ${uri}`, () => {
    assert.throws(() => inspectStudioGlb(glb({ extras: { nested: { uri } } })), /外链/);
});
test("unknown required extensions and decoder-dependent compression cannot silently show incomplete models", () => {
    assert.throws(() => inspectStudioGlb(glb({ extensionsRequired: ["VENDOR_required"] })), /必要扩展/);
    assert.throws(() => inspectStudioGlb(glb({ extensionsRequired: ["KHR_materials_unlit"] })), /必要扩展/);
    for (const extension of ["KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_texture_basisu"]) assert.throws(() => inspectStudioGlb(glb({ extensionsUsed: [extension] })), /解码器/);
    assert.throws(() => inspectStudioGlb(glb({ meshes: [{ primitives: [{ extensions: { KHR_draco_mesh_compression: {} } }] }] })), /解码器/);
});
test("invalid asset version, URI value, image MIME and extension declarations fail clearly", () => {
    for (const fields of [{ asset: { version: "1.0" } }, { asset: { version: "2.0", minVersion: "3.0" } }, { images: [{ uri: null }] }, { images: [{ bufferView: 0, mimeType: "image/svg+xml" }] }, { extensionsUsed: "KHR_materials_unlit" }, { extensionsRequired: [4] }]) assert.throws(() => inspectStudioGlb(glb(fields)));
});
test("bad headers, byte count, JSON encoding and truncated chunks fail without mutating originals", () => {
    for (const offset of [0, 4, 8, 12, 16]) { const data = glb(); new DataView(data).setUint32(offset, 1, true); assert.throws(() => inspectStudioGlb(data)); }
    assert.throws(() => inspectStudioGlb(new ArrayBuffer(8)));
    const invalidJson = glb(); new Uint8Array(invalidJson)[20] = 0xff; assert.throws(() => inspectStudioGlb(invalidJson), /JSON/);
    const source = glb(), truncated = new ArrayBuffer(source.byteLength + 4); new Uint8Array(truncated).set(new Uint8Array(source)); new DataView(truncated).setUint32(8, truncated.byteLength, true); assert.throws(() => inspectStudioGlb(truncated), /数据块/);
});
