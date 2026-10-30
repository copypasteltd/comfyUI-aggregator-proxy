import test from "node:test";
import assert from "node:assert/strict";
import fixture from "./fixtures/catalog.json";
import { studioExamples, exampleModel } from "../src/lib/studio-examples";
import { buildComposerRequest, firstAllowedPreset } from "../src/services/api/aggregator/composer";
import { schemaProperties, validateJob } from "../src/services/api/aggregator/capabilities";
import type { Catalog } from "../src/services/api/aggregator/types";

const catalog = { models: fixture.capabilities.map((item) => ({ ...item, usage: fixture.models.find((model) => model.id === item.id)!.usage })), policy: { scopes: fixture.scopes, limits: {} }, releaseGuard: true } as unknown as Catalog;
for (const example of studioExamples) test(`example ${example.id}: published task, reference count, roles and schema match`, async () => {
    const model = exampleModel(example, catalog)!;
    assert.ok(model, "Fixture must publish a matching authorized model");
    assert.equal(model.task_type, example.taskType);
    const slots = model.usage.references.slots.filter((slot) => slot.path !== "input.mask");
    assert.equal(slots.length, example.references);
    const input = "prompt" in schemaProperties(model.usage, "input") ? { prompt: example.prompt } : {};
    const references = Object.fromEntries(slots.map((slot, index) => [slot.path, { assetId: `asset_example_${index}` }]));
    const request = buildComposerRequest(model, firstAllowedPreset(model, catalog.policy), { input, parameters: {}, references });
    assert.deepEqual(await validateJob(model, request), []);
    if (example.taskType === "image_to_3d") assert.deepEqual(Object.keys(request.input), ["source_image"]);
});

test("examples never substitute a different reference count or generation type", () => {
    const firstFrame = studioExamples.find((example) => example.id === "video-first-frame")!;
    const onlyMultiple = { ...catalog, models: catalog.models.filter((model) => model.usage.references.exact_reference_count !== 1) };
    assert.equal(exampleModel(firstFrame, onlyMultiple), undefined);
    assert.equal(exampleModel(studioExamples[0], { ...catalog, models: catalog.models.filter((model) => model.task_type !== "text_to_image") }), undefined);
});

test("examples reject unavailable models and keys without any allowed specification", () => {
    assert.equal(exampleModel(studioExamples[0], { ...catalog, models: catalog.models.map((model) => ({ ...model, unavailable_reason: "offline" })) }), undefined);
    assert.equal(exampleModel(studioExamples[0], { ...catalog, policy: { ...catalog.policy, spec_limits: { allowed_qualities: [] } } }), undefined);
});

test("simple image example cannot claim a model requiring an unprovided mask", () => {
    const example = studioExamples.find((example) => example.taskType === "image_to_image")!;
    const model = exampleModel(example, catalog)!;
    const requiringMask = { ...model, usage: { ...model.usage, references: { ...model.usage.references, slots: [...model.usage.references.slots.filter((slot) => slot.path !== "input.mask"), { path: "input.mask", required: true, description: "required mask", weight_supported: false }] } } };
    assert.equal(exampleModel(example, { ...catalog, models: [requiringMask] }), undefined);
});
