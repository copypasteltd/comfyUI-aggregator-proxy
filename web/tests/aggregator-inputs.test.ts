import test from "node:test";
import assert from "node:assert/strict";
import fixture from "./fixtures/catalog-154.json";
import mixed from "./fixtures/multimodal-154.json";
import continuation from "./fixtures/usage-context-154.json";
import { AggregatorClient, blobHash } from "../src/services/api/aggregator/client";
import { joinCatalog, presetPolicyReason, validateJob } from "../src/services/api/aggregator/capabilities";
import { buildComposerRequest } from "../src/services/api/aggregator/composer";
import { composerSlots, contextReason, modelInputSummary, normalizeInputFile, slotAccepts, structuredInputFields, validateSlotOriginal } from "../src/services/api/aggregator/inputs";
import type { Capability, CatalogModel, ModelUsage, PublishedModel, RemoteJob } from "../src/services/api/aggregator/types";
import { exampleModel } from "../src/lib/studio-examples";

const models = fixture.models as unknown as PublishedModel[], capabilities = fixture.capabilities as unknown as Capability[];
const catalog = capabilities.map((capability) => ({ ...capability, usage: models.find((model) => model.id === capability.id)!.usage! }));
const mixedModel = { ...mixed.capability, usage: mixed.model.usage } as unknown as CatalogModel;
const contextModel = { ...catalog.find((model) => model.task_type === "text_to_video")!, id: continuation.example_request.model, usage: continuation as unknown as ModelUsage };
const values = (model: CatalogModel) => ({ input: Object.fromEntries(Object.entries(model.usage.example_request.input).filter(([name]) => !structuredInputFields.includes(name))), parameters: {}, references: Object.fromEntries(composerSlots(model.usage).filter((slot) => slot.required).map((slot, i) => [slot.path, { assetId: `asset_fixture_${i}` }])), texts: model.usage.example_request.input.texts as Record<string, string> | undefined, parentJobId: (model.usage.example_request.input.context as { job_id: string } | undefined)?.job_id });

test("all 45 authorized published models accept their actual 1.0/1.1 discovery contract", async () => {
    const joined = await joinCatalog(models, capabilities, fixture.scopes);
    assert.equal(joined.length, 45);
    assert.deepEqual(joined.filter((model) => model.unavailable_reason).map((model) => [model.id, model.unavailable_reason]), []);
});
for (const model of [...catalog, mixedModel]) for (const [index] of model.usage.presets.entries()) test(`published composer mapping: ${model.id} / preset ${index}`, async () => {
    const request = buildComposerRequest(model, index, values(model));
    assert.deepEqual(await validateJob(model, request), []);
    assert.deepEqual((request.input.media as { slot: string }[] | undefined)?.map((item) => item.slot) || [], (model.usage.input_slots || []).filter((slot) => slot.required).map((slot) => slot.name));
    if (!model.usage.request_schema.properties || !(model.usage.request_schema.properties as Record<string, { properties: object }>).input.properties.hasOwnProperty("prompt")) assert.equal("prompt" in request.input, false);
});

test("named input roles are never replaced by the legacy references array", () => {
    const request = buildComposerRequest(mixedModel, 0, values(mixedModel));
    assert.equal("references" in request.input, false);
    assert.deepEqual(Object.keys((request.input.media as object[])[0]).sort(), ["asset_id", "slot"]);
    for (const slot of composerSlots(mixedModel.usage)) {
        const input = values(mixedModel); delete input.references[slot.path];
        assert.throws(() => buildComposerRequest(mixedModel, 0, input), /请添加/);
    }
    assert.throws(() => buildComposerRequest(mixedModel, 0, { ...values(mixedModel), input: { media: [] } }), /输入字段/);
    assert.throws(() => buildComposerRequest(mixedModel, 0, { ...values(mixedModel), texts: { unexpected: "not advertised" } }), /文本字段/);
});

test("required named text is explicit, Unicode counted by schema, and validated before uploads", async () => {
    const input = values(mixedModel);
    assert.notDeepEqual(await validateJob(mixedModel, buildComposerRequest(mixedModel, 0, { ...input, texts: {} })), []);
    assert.deepEqual(await validateJob(mixedModel, buildComposerRequest(mixedModel, 0, { ...input, texts: { dialogue: "🎵".repeat(32000) } })), []);
    assert.notDeepEqual(await validateJob(mixedModel, buildComposerRequest(mixedModel, 0, { ...input, texts: { dialogue: "🎵".repeat(32001) } })), []);
});

test("slot MIME, kind and size use published limits; GLB header is checked without a renderer", async () => {
    const slots = composerSlots(mixedModel.usage);
    for (const slot of slots) {
        assert.equal(slotAccepts(slot, new Blob(["x"], { type: slot.mimeTypes[0] })), true);
        assert.equal(slotAccepts(slot, new Blob([], { type: slot.mimeTypes[0] })), false);
        assert.equal(slotAccepts(slot, new Blob(["x"], { type: "text/plain" })), false);
        assert.equal(slotAccepts({ ...slot, maxBytes: 1 }, new Blob(["xx"], { type: slot.mimeTypes[0] })), false);
    }
    const slot = slots.find((item) => item.kind === "model")!;
    await assert.rejects(validateSlotOriginal(new Blob(["invalid"], { type: "model/gltf-binary" }), slot), /GLB/);
    const bytes = new Uint8Array(12), header = new DataView(bytes.buffer); header.setUint32(0, 0x46546c67, true); header.setUint32(4, 2, true); header.setUint32(8, 12, true);
    await validateSlotOriginal(new Blob([bytes], { type: "model/gltf-binary" }), slot);
    const file = normalizeInputFile(new File([bytes], "object.glb", { type: "application/octet-stream" }));
    assert.equal(file.type, "model/gltf-binary"); assert.equal(await blobHash(file), await blobHash(new Blob([bytes])));
});

for (const kind of ["video", "audio", "model"] as const) test(`multipart ${kind} retains kind, bytes, digest and stable identity`, async () => {
    const mime = { video: "video/mp4", audio: "audio/flac", model: "model/gltf-binary" }[kind], blob = new Blob(["synthetic bytes"], { type: mime }), digest = await blobHash(blob);
    const client = new AggregatorClient("http://example.invalid", "synthetic-input-key", async (_url, init) => {
        assert.equal(init?.credentials, "omit");
        assert.equal(new Headers(init?.headers).get("Idempotency-Key"), "frozen-upload");
        const form = init?.body as FormData;
        assert.equal(form.get("kind"), kind); assert.equal(await blobHash(form.get("file") as Blob), digest);
        return Response.json({ id: "asset_fixture_typed", status: "ready", bytes: blob.size, sha256: digest, media_type: mime, kind, expires_at: 1 });
    });
    assert.equal((await client.upload(blob, "input", "frozen-upload", digest, undefined, kind)).kind, kind);
});

test("continuation sends only a successful compatible unexpired parent ID", async () => {
    const input = values(contextModel), job = { id: input.parentJobId, status: "succeeded", context: { available: true, family: contextModel.usage.context!.family, compatibility: contextModel.usage.context!.compatibility, expires_at: 2000 } } as RemoteJob;
    assert.equal(contextReason(contextModel, job, 1000000), undefined);
    for (const candidate of [{ ...job, status: "running" }, { ...job, context: { ...job.context!, available: false } }, { ...job, context: { ...job.context!, compatibility: "e".repeat(64) } }]) assert.ok(contextReason(contextModel, candidate, 1000000));
    assert.ok(contextReason(contextModel, job, 3000000));
    assert.deepEqual(await validateJob(contextModel, buildComposerRequest(contextModel, 0, input)), []);
    assert.throws(() => buildComposerRequest(contextModel, 0, { ...input, parentJobId: undefined }), /父任务/);
    assert.throws(() => buildComposerRequest(contextModel, 0, { ...input, parentJobId: "http://device/private-context" }), /父任务/);
});

test("simple editable cases exclude voice, mesh, named-script and continuation requirements", () => {
    const policy = { scopes: fixture.scopes, limits: {} };
    assert.equal(exampleModel({ taskType: mixedModel.task_type, references: 1 }, { policy, releaseGuard: true, models: [mixedModel] }), undefined);
    assert.match(modelInputSummary(mixedModel.usage), /视频/); assert.match(modelInputSummary(mixedModel.usage), /专用文本/);
    const multiview = catalog.find((model) => (model.usage.input_slots || []).filter((slot) => slot.kind === "image").length === 4)!;
    assert.ok(presetPolicyReason(multiview, multiview.usage.presets[0], { ...policy, spec_limits: { max_references: 3 } }));
    const source = catalog.find((model) => model.usage.schema_version === "1.0" && model.task_type === "image_to_image")!;
    assert.equal(presetPolicyReason(source, source.usage.presets[0], { ...policy, spec_limits: { max_references: 0 } }), undefined);
});
