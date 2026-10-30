import test from "node:test";
import assert from "node:assert/strict";
import fixture from "./fixtures/catalog.json";
import type { Capability, CatalogModel } from "../src/services/api/aggregator/types";
import { buildComposerRequest, firstAllowedPreset, suggestComposerModel, type ReferenceChoice } from "../src/services/api/aggregator/composer";
import { schemaProperties, validateJob } from "../src/services/api/aggregator/capabilities";

const models = fixture.capabilities.map((item) => ({ ...item as unknown as Capability, usage: fixture.models.find((model) => model.id === item.id)!.usage })) as unknown as CatalogModel[];
for (const model of models) {
    for (const [index] of model.usage.presets.entries()) test(`composer ${model.id} preset ${index}: complete request from UI values`, async () => {
        const input = Object.fromEntries(Object.entries(model.usage.example_request.input).filter(([name]) => !["source_image", "mask", "references"].includes(name)));
        const references = Object.fromEntries(model.usage.references.slots.filter((slot) => slot.required).map((slot, position) => [slot.path, { assetId: `asset_fixture_${position}` }]));
        const request = buildComposerRequest(model, index, { input, references, parameters: {} });
        assert.deepEqual(await validateJob(model, request), []);
        assert.equal(request.parameters.n, 1);
        assert.equal("sample_rate" in request.parameters, "sample_rate" in schemaProperties(model.usage, "parameters"));
    });
    if (model.usage.references.slots.some((slot) => slot.required)) test(`composer ${model.id}: missing slots fail before submission`, () => {
        assert.throws(() => buildComposerRequest(model, 0, { input: {}, references: {}, parameters: {} }), /请添加/);
    });
}
test("composer: first frame and ordered references keep published roles", () => {
    const first = models.find((model) => model.id === "comfy-4090-video-h3-i2v")!;
    const refs = models.find((model) => model.id === "comfy-4090-video-h3-refs")!;
    const values = { input: { prompt: "test" }, parameters: {}, references: { "input.references[0]": { assetId: "asset_first_01" }, "input.references[1]": { assetId: "asset_second_02" } } };
    assert.equal((buildComposerRequest(first, 0, values).input.references as ReferenceChoice[] & { role: string }[])[0].role, "first_frame");
    const actual = buildComposerRequest(refs, 0, values).input.references as { asset_id: string; role: string }[];
    assert.deepEqual(actual.map((item) => item.asset_id), ["asset_first_01", "asset_second_02"]);
    assert.deepEqual(actual.map((item) => item.role), (refs.usage.example_request.input.references as { role: string }[]).map((item) => item.role));
});
test("composer: fixed preset, n and unknown input cannot be overwritten", () => {
    const model = models.find((item) => item.task_type === "text_to_image")!;
    for (const parameters of [{ n: 2 }, { size: "999x999" }, { unknown: true }]) assert.throws(() => buildComposerRequest(model, 0, { input: { prompt: "test" }, parameters, references: {} }));
    assert.throws(() => buildComposerRequest(model, 0, { input: { unexpected: "test" }, parameters: {}, references: {} }));
});

const policy = { scopes: fixture.scopes, limits: {} };
test("composer: selected image count picks a published input contract, not a model name", () => {
    assert.equal(suggestComposerModel(models, policy, undefined, "image", 1)?.task_type, "image_to_image");
    assert.equal(suggestComposerModel(models, policy, undefined, "image", 2)?.task_type, "references_to_image");
    assert.equal(suggestComposerModel(models, policy, undefined, "image", 0)?.task_type, "text_to_image");
});
test("composer: explicit model is respected and unavailable choices never silently switch", () => {
    const audio = models.find((model) => model.task_type === "text_to_audio")!;
    assert.equal(suggestComposerModel(models, policy, audio.id, undefined, 2)?.id, audio.id);
    assert.equal(suggestComposerModel([{ ...audio, unavailable_reason: "not available" }, ...models.filter((model) => model.id !== audio.id)], policy, audio.id), undefined);
    assert.equal(suggestComposerModel(models, policy, "unknown-model"), undefined);
});
test("composer: requested output type without a matching model stays unselected", () => {
    assert.equal(suggestComposerModel(models.filter((model) => model.task_type !== "text_to_audio"), policy, undefined, "audio"), undefined);
});
test("composer: blocked first preset is skipped; all blocked specifications cannot be selected", () => {
    const model = structuredClone(models.find((item) => item.task_type === "text_to_image")!);
    model.usage.presets = [{ ...model.usage.presets[0], quality: "draft" }, { ...model.usage.presets[0], quality: "standard" }];
    const standard = { ...policy, spec_limits: { allowed_qualities: ["standard"] } };
    assert.equal(firstAllowedPreset(model, standard), 1);
    assert.equal(suggestComposerModel([model], standard)?.id, model.id);
    const denied = { ...policy, spec_limits: { allowed_qualities: [] } };
    assert.equal(firstAllowedPreset(model, denied), -1);
    assert.equal(suggestComposerModel([model], denied), undefined);
});
