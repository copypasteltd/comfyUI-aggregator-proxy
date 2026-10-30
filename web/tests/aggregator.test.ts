import test from "node:test";
import "./canvas-transfer.test";
import "./studio-result-ownership.test";
import "./studio-document-references.test";
import "./studio-focus.test";
import "./aggregator-composer.test";
import "./task-journal.test";
import "./aggregator-batch.test";
import "./studio-glb.test";
import "./studio-glb-playback.test";
import "./local-media-inventory.test";
import "./studio-local-media-cleanup.test";
import "./studio-upload-cache.test";
import "./studio-task-access.test";
import assert from "node:assert/strict";
import fixture from "./fixtures/catalog.json";
import { assertLocalSchema, joinCatalog, presetParameters, presetPolicyReason, schemaProperties, validateJob } from "../src/services/api/aggregator/capabilities";
import { AggregatorClient, GatewayError, blobHash, identityFingerprint, normalizeOrigin, parseRetryAfter } from "../src/services/api/aggregator/client";
import type { Capability, ContentMetadata, JobRequest, PublishedModel } from "../src/services/api/aggregator/types";

const models = fixture.models as unknown as PublishedModel[];
const capabilities = fixture.capabilities as unknown as Capability[];
const catalog = capabilities.map((capability) => ({ ...capability, usage: models.find((model) => model.id === capability.id)!.usage! }));
const clone = <T>(value: T): T => structuredClone(value);
test("Key policy restricts declared presets without introducing independent limits", () => {
    const policy = { scopes: [], limits: {}, spec_limits: { allowed_qualities: ["standard"], image_max_pixels: 1024 * 1024, video_max_pixels: 1024 * 1024, video_max_milliseconds: 15000, audio_max_milliseconds: 600000, max_references: 4 } };
    const image = catalog.find((item) => item.task_type === "text_to_image")!;
    assert.match(presetPolicyReason(image, { quality: "draft", size: "512x512" }, policy) || "", /质量/);
    assert.match(presetPolicyReason(image, { quality: "standard", size: "2048x2048" }, policy) || "", /尺寸/);
    assert.equal(presetPolicyReason(image, { quality: "standard", size: "1024x1024" }, policy), undefined);
    const audio = catalog.find((item) => item.task_type === "text_to_audio")!;
    assert.equal(presetPolicyReason(audio, { quality: "standard", seconds: 30 }, policy), undefined);
    const mesh = catalog.find((item) => item.task_type === "image_to_3d")!;
    assert.equal(presetPolicyReason(mesh, { quality: "standard" }, policy), undefined);
});
test("native browser fetch is invoked with its global receiver", async () => {
    const fetcher: typeof fetch = async function (this: typeof globalThis) {
        assert.equal(this, globalThis);
        return Response.json({ error: { code: "synthetic_auth", message: "synthetic" } }, { status: 401 });
    };
    await assert.rejects(new AggregatorClient("http://fixture.invalid", "synthetic-key", fetcher).catalog(), /synthetic/);
});
for (const model of catalog) {
    test(`${model.id}: declared usage and example`, async () => {
        assert.equal((await joinCatalog(models, [model], fixture.scopes))[0].unavailable_reason, undefined);
        assert.deepEqual(await validateJob(model, clone(model.usage.example_request)), []);
    });
    model.usage.presets.forEach((preset, index) => test(`${model.id}: full preset ${index}`, async () => {
        const request = clone(model.usage.example_request);
        request.parameters = presetParameters(model.usage, preset);
        assert.deepEqual(await validateJob(model, request), []);
        for (const name of Object.keys(request.parameters)) assert.ok(name in schemaProperties(model.usage, "parameters"));
    }));
    const changes: Record<string, (request: JobRequest) => void> = {
        "wrong model": (request) => { request.model = "other"; },
        "unknown input": (request) => { request.input.unknown_option = true; },
        "batch count": (request) => { request.parameters.n = 2; },
    };
    if (model.usage.example_request.input.source_image) changes["missing source image"] = (request) => { delete request.input.source_image; };
    if (model.task_type === "text_to_audio") changes["output codec as parameter"] = (request) => { request.parameters.audio_format = "mp3"; };
    if (model.task_type === "image_to_3d") changes["undeclared 3d prompt"] = (request) => { request.input.prompt = "text"; };
    if (model.usage.references.exact_reference_count) {
        changes["missing reference"] = (request) => { (request.input.references as unknown[]).pop(); };
        if (model.usage.references.slots.every((slot) => !slot.weight_supported)) changes["unsupported weight"] = (request) => { (request.input.references as Record<string, unknown>[])[0].weight = 0.5; };
        if (model.task_type === "references_to_video" && model.usage.references.exact_reference_count === 1) changes["first frame role"] = (request) => { (request.input.references as Record<string, unknown>[])[0].role = "reference_0"; };
    }
    for (const [name, change] of Object.entries(changes)) test(`${model.id}: rejects ${name}`, async () => {
        const request = clone(model.usage.example_request);
        change(request);
        assert.ok((await validateJob(model, request)).length);
    });
}

test("unknown schema, external references and insufficient scopes cannot generate", async () => {
    assert.throws(() => assertLocalSchema({ $schema: "https://example.test/schema" }));
    assert.throws(() => assertLocalSchema({ $schema: "https://json-schema.org/draft/2020-12/schema", properties: { value: { $ref: "https://example.test/remote" } } }));
    assert.ok((await joinCatalog(models, capabilities, []))[0].unavailable_reason);
    const altered = clone(models);
    altered[0].usage!.schema_version = "2.0" as "1.0";
    assert.ok((await joinCatalog(altered, capabilities, fixture.scopes))[0].unavailable_reason);
});

test("plain HTTP identity hashing and origin normalization", () => {
    assert.equal(normalizeOrigin("http://example.test:18080/v1/"), "http://example.test:18080");
    for (const value of ["javascript:alert(1)", "http://u:p@example.test", "http://example.test/?apiKey=x", "http://example.test/other"]) assert.throws(() => normalizeOrigin(value));
    const fingerprint = identityFingerprint("http://example.test", "synthetic-key-A");
    assert.match(fingerprint, /^[a-f0-9]{64}$/);
    assert.notEqual(fingerprint, identityFingerprint("http://example.test", "synthetic-key-B"));
    assert.notEqual(fingerprint, identityFingerprint("http://other.test", "synthetic-key-A"));
});

test("Retry-After seconds/date are respected without an invented retry deadline", () => {
    const now = Date.parse("2026-10-30T00:00:00Z");
    assert.equal(parseRetryAfter("2", now), now + 2000);
    assert.equal(parseRetryAfter("Fri, 30 Oct 2026 00:00:12 GMT", now), now + 12000);
    assert.equal(parseRetryAfter("invalid", now), undefined);
    assert.equal(parseRetryAfter(null, now), undefined);
});

const job = { id: `job_${"1".repeat(32)}`, object: "generation.job", model: catalog[0].id, task_type: catalog[0].task_type, status: "queued", updated_revision: 1, created_at: 1 };
const contentId = `content_${"2".repeat(32)}`;
const respond = (data: unknown, init?: ResponseInit) => new Response(JSON.stringify(data), { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });

test("exact immutable submission, fixed origin, business bearer and release precondition", async () => {
    const body = JSON.stringify(catalog[0].usage.example_request);
    const client = new AggregatorClient("http://example.test:18080", "synthetic-key", (async (url, init) => {
        assert.equal(url, "http://example.test:18080/v1/jobs");
        assert.equal(init?.credentials, "omit");
        assert.equal(init?.redirect, "error");
        assert.equal(init?.body, body);
        const headers = new Headers(init?.headers);
        assert.equal(headers.get("Authorization"), "Bearer synthetic-key");
        assert.equal(headers.get("Idempotency-Key"), "original-intent");
        assert.equal(headers.get("X-Expected-Release-Digest"), "a".repeat(64));
        return respond(job, { status: 202, headers: { "Retry-After": "2" } });
    }) as typeof fetch);
    assert.equal((await client.createJob(body, "original-intent", "a".repeat(64))).data.id, job.id);
    await assert.rejects(client.job("https://foreign.test/job"));
    await assert.rejects(client.cancel("../admin"));
});

test("catalog combines usage/capabilities/permissions and requires explicit service contract", async () => {
    const client = new AggregatorClient("http://example.test", "synthetic-key", (async (url) => {
        if (String(url).endsWith("access-policy")) return respond({ scopes: fixture.scopes, limits: {} }, { headers: { "X-Studio-Contract": "1" } });
        if (String(url).endsWith("capabilities")) return respond({ data: capabilities });
        return respond({ data: models });
    }) as typeof fetch);
    const result = await client.catalog();
    assert.equal(result.models.length, 11);
    assert.equal(result.releaseGuard, true);
    assert.ok(result.models.every((model) => !model.unavailable_reason));
});

test("server errors preserve retry hints and correlation while redacting the key", async () => {
    const client = new AggregatorClient("http://example.test", "synthetic-key", (async () => respond({ error: { code: "rate_limited", message: "synthetic-key reached budget" } }, { status: 429, headers: { "Retry-After": "3", "X-Request-ID": "synthetic-correlation" } })) as typeof fetch);
    await assert.rejects(client.job(job.id), (error: unknown) => error instanceof GatewayError && error.status === 429 && error.code === "rate_limited" && !error.message.includes("synthetic-key") && error.requestId === "synthetic-correlation" && typeof error.retryAt === "number");
});

test("job detail cannot replace the requested job with another identity", async () => {
    const client = new AggregatorClient("http://example.test", "synthetic-key", (async () => respond({ ...job, id: `job_${"3".repeat(32)}` })) as typeof fetch);
    await assert.rejects(client.job(job.id), /编号/);
});

test("submission lookup sends the original bytes and headers to the fixed read-only route", async () => {
    const body = ' { "model":"synthetic-image", "task_type":"text_to_image", "input":{"prompt":"原文"}, "parameters":{}, "client_request_id":"original" } ';
    const client = new AggregatorClient("http://example.test", "synthetic-key", (async (url, init) => {
        assert.equal(String(url), "http://example.test/v1/jobs/lookup");
        assert.equal(init?.method, "POST"); assert.equal(init?.body, body);
        assert.equal(init?.credentials, "omit"); assert.equal(init?.redirect, "error");
        const headers = new Headers(init?.headers);
        assert.equal(headers.get("Authorization"), "Bearer synthetic-key");
        assert.equal(headers.get("Idempotency-Key"), "studio-original-token");
        assert.equal(headers.get("X-Expected-Release-Digest"), "a".repeat(64));
        return respond({ object: "generation.submission_lookup", outcome: "found", job });
    }) as typeof fetch);
    const found = await client.lookupSubmission(body, "studio-original-token", "a".repeat(64));
    assert.equal(found.data.outcome, "found");
    assert.equal(found.data.job?.id, job.id);
});

test("not observed is represented explicitly and never converted into a create request", async () => {
    let calls = 0;
    const client = new AggregatorClient("http://example.test", "synthetic-key", (async (url) => {
        calls++; assert.equal(String(url), "http://example.test/v1/jobs/lookup");
        return respond({ object: "generation.submission_lookup", outcome: "not_observed", job: null });
    }) as typeof fetch);
    assert.deepEqual((await client.lookupSubmission("{}", "studio-original-token", "a".repeat(64))).data, { object: "generation.submission_lookup", outcome: "not_observed", job: null });
    assert.equal(calls, 1);
});

for (const invalid of [null, {}, { object: "generation.submission_lookup", outcome: "found", job: null }, { object: "generation.submission_lookup", outcome: "not_observed", job }, { object: "generation.submission_lookup", outcome: "retry_create", job: null }, { object: "generation.submission_lookup", outcome: "found", job: { ...job, id: "https://foreign.test/job" } }]) {
    test(`invalid submission lookup response stays an error: ${JSON.stringify(invalid)}`, async () => {
        const client = new AggregatorClient("http://example.test", "synthetic-key", (async () => respond(invalid)) as typeof fetch);
        await assert.rejects(client.lookupSubmission("{}", "studio-original-token", "a".repeat(64)));
    });
}

for (const advertised of [true, false]) {
    test(`submission lookup is available only after explicit capability advertisement: ${advertised}`, async () => {
        const client = new AggregatorClient("http://example.test", "synthetic-key", (async (url) => {
            if (String(url).endsWith("access-policy")) return respond({ scopes: fixture.scopes, limits: {} }, { headers: { "X-Studio-Contract": "1", ...(advertised ? { "X-Studio-Submission-Lookup": "1" } : {}) } });
            return respond({ data: String(url).endsWith("models") ? models : capabilities });
        }) as typeof fetch);
        assert.equal((await client.catalog()).submissionLookup, advertised);
    });
}

async function downloadFixture(bytes: Uint8Array, mediaType = "image/png") {
    const blob = new Blob([bytes], { type: mediaType });
    const metadata: ContentMetadata = { id: contentId, bytes: bytes.length, sha256: await blobHash(blob), media_type: mediaType, expires_at: 9999999999, metadata: {} };
    const client = new AggregatorClient("http://example.test", "synthetic-key", (async (_url, init) => {
        assert.equal(init?.redirect, "error");
        return new Response(blob, { headers: { "Content-Type": mediaType, "Content-Length": String(bytes.length) } });
    }) as typeof fetch);
    return { client, metadata };
}

test("downloads verify declared bytes and SHA-256 before returning a blob", async () => {
    const { client, metadata } = await downloadFixture(new Uint8Array([1, 2, 3, 4]));
    assert.equal((await client.download(metadata)).size, 4);
    await assert.rejects(client.download({ ...metadata, sha256: "0".repeat(64) }), /校验/);
    await assert.rejects(client.download({ ...metadata, bytes: 9 }), /长度/);
    await assert.rejects(client.download({ ...metadata, media_type: "audio/wav" }), /媒体类型/);
});

test("GLB header/version/length checked independently of a matching hash", async () => {
    const bytes = new Uint8Array(12);
    const header = new DataView(bytes.buffer);
    header.setUint32(0, 0x46546c67, true); header.setUint32(4, 2, true); header.setUint32(8, 12, true);
    const valid = await downloadFixture(bytes, "model/gltf-binary");
    assert.equal((await valid.client.download(valid.metadata)).type, "model/gltf-binary");
    bytes[0] = 0;
    const invalid = await downloadFixture(bytes, "model/gltf-binary");
    await assert.rejects(invalid.client.download(invalid.metadata), /GLB/);
});

test("uploads use a stable multipart identity and verify the stored source", async () => {
    const file = new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
    const digest = await blobHash(file);
    const asset = { id: `asset_${"4".repeat(32)}`, status: "ready", bytes: file.size, sha256: digest, media_type: "image/png", width: 1, height: 1, expires_at: 9999999999 };
    const client = new AggregatorClient("http://example.test", "synthetic-key", (async (_url, init) => {
        assert.ok(init?.body instanceof FormData);
        assert.equal(new Headers(init?.headers).get("Idempotency-Key"), "original-upload");
        assert.equal(new Headers(init?.headers).get("Content-Type"), null);
        return respond(asset);
    }) as typeof fetch);
    assert.equal((await client.upload(file, "reference.png", "original-upload", digest)).id, asset.id);
    await assert.rejects(client.upload(file, "reference.png", "original-upload", "0".repeat(64)), /完整性/);
});
