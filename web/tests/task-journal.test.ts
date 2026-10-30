import test from "node:test";
import assert from "node:assert/strict";
import { sha256 } from "@noble/hashes/sha256";
import { bytesToHex } from "@noble/hashes/utils";
import fixture from "./fixtures/catalog.json";
import { createTaskJournal, readTaskJournal, taskSubmissionSignature } from "../src/services/task-journal-format";
import type { IdentityRef, Intent } from "../src/services/aggregator-journal";

const identity: IdentityRef = { profileId: "original-profile", origin: "http://journal.test:39443", fingerprint: "a".repeat(64) };
const task = (id = "original-intent"): Intent => ({ version: 1, id, identity, projectId: "original-project", nodeId: "original-node", body: ` { "model": "original-model", "task_type": "text_to_image", "input": {"prompt":"原文 \\n雪"}, "parameters": {"n":1}, "client_request_id":"${id}" } `, idempotencyKey: `studio-job-${id}`, releaseDigest: "b".repeat(64), stage: "unknown", createdAt: 1, updatedAt: 2, output: { mediaTypes: ["image/png"], maxBytes: 1024 } });
const rewrite = (source: string, change: (payload: any) => void) => {
    const envelope = JSON.parse(source);
    const payload = JSON.parse(envelope.payload);
    change(payload);
    envelope.payload = JSON.stringify(payload);
    envelope.sha256 = bytesToHex(sha256(new TextEncoder().encode(envelope.payload)));
    return JSON.stringify(envelope);
};
test("task journal keeps original JSON bytes and submission identity, omitting unrelated secrets/media/canvas", () => {
    const original = { ...task(), error: "private-error-secret", canvas: { node: { metadata: { apiKey: "private-canvas-key" } }, sourceIds: [] }, savedKey: "private-saved-key", secret: "private-extra" } as unknown as Intent;
    const source = createTaskJournal([original], identity);
    const restored = readTaskJournal(source, identity).tasks[0];
    assert.equal(restored.body, original.body);
    assert.equal(taskSubmissionSignature(restored), taskSubmissionSignature(original));
    assert.doesNotMatch(source, /private-|canvas|savedKey|blob|error/);
    assert.equal("canvas" in restored, false);
    assert.equal(original.error, "private-error-secret");
});
test("all seven task types round-trip without requiring their historical model to remain published", () => {
    for (const model of fixture.models) {
        const record = task();
        record.body = JSON.stringify({ ...model.usage.example_request, client_request_id: record.id });
        assert.equal(readTaskJournal(createTaskJournal([record], identity), identity).tasks[0].body, record.body);
    }
});
test("journal SHA, format/version, foreign identity and added configuration fields are refused", () => {
    const source = createTaskJournal([task()], identity);
    const corrupted = JSON.parse(source); corrupted.payload += " ";
    assert.throws(() => readTaskJournal(JSON.stringify(corrupted), identity), /SHA-256/);
    const unknown = JSON.parse(source); unknown.version = 99;
    assert.throws(() => readTaskJournal(JSON.stringify(unknown), identity));
    assert.throws(() => readTaskJournal(source, { ...identity, origin: "http://another.test" }), /原服务/);
    assert.throws(() => readTaskJournal(source, { ...identity, fingerprint: "b".repeat(64) }), /原API Key/);
    assert.throws(() => readTaskJournal(rewrite(source, (p) => { p.savedKey = "not-accepted"; }), identity));
});
test("duplicate IDs/nodes, unknown task version, body/key/digest and job association corruption are refused", () => {
    const source = createTaskJournal([task()], identity);
    for (const change of [
        (p: any) => { p.tasks.push(p.tasks[0]); },
        (p: any) => { const another = task("another"); p.tasks.push(another); },
        (p: any) => { p.tasks[0].version = 99; },
        (p: any) => { p.tasks[0].body = "{broken"; },
        (p: any) => { p.tasks[0].body = JSON.stringify({ ...JSON.parse(p.tasks[0].body), client_request_id: "changed" }); },
        (p: any) => { p.tasks[0].idempotencyKey = "changed-key"; },
        (p: any) => { p.tasks[0].releaseDigest = "no-digest"; },
        (p: any) => { p.tasks[0].identity.fingerprint = "f".repeat(64); },
        (p: any) => { p.tasks[0].output.maxBytes = 0; },
        (p: any) => { p.tasks[0].stage = "terminal"; },
        (p: any) => { p.tasks[0].job = { id: "job_wrong" }; },
        (p: any) => { p.tasks[0].identity.origin += "/v1"; },
        (p: any) => { p.tasks[0].createdAt = -1; },
    ]) assert.throws(() => readTaskJournal(rewrite(source, change), identity));
});
test("known job projection retains queryable IDs but omits transient diagnostics and extras", () => {
    const original = { ...task(), stage: "terminal", job: { id: `job_${"c".repeat(32)}`, object: "generation.job", model: "original-model", task_type: "text_to_image", status: "succeeded", updated_revision: 8, created_at: 1, completed_at: 2, artifacts: [{ id: `content_${"d".repeat(32)}`, extra: "private-artifact" }], error: { message: "private-job-error" }, internal: "private-internal" } } as Intent;
    const source = createTaskJournal([original], identity);
    const restored = readTaskJournal(source, identity).tasks[0];
    assert.equal(restored.job?.id, original.job!.id);
    assert.deepEqual(restored.job?.artifacts, [{ id: `content_${"d".repeat(32)}` }]);
    assert.doesNotMatch(source, /private-/);
    assert.throws(() => readTaskJournal(rewrite(source, (p) => { p.tasks[0].job.model = "forged"; }), identity));
    assert.throws(() => readTaskJournal(rewrite(source, (p) => { p.tasks[0].job.artifacts.push(p.tasks[0].job.artifacts[0]); }), identity));
});
test("credentials and prototype keys inside historical requests are rejected rather than silently rewriting bytes", () => {
    for (const name of ["apiKey", "authorization", "password", "__proto__", "constructor", "access_token"]) {
        const original = task();
        original.body = original.body.replace('"n":1', `"n":1,"${name}":"private"`);
        assert.throws(() => createTaskJournal([original], identity));
    }
    const literal = task(); literal.body = literal.body.replace("原文", "<script>globalThis.forbidden=1</script>");
    assert.equal(readTaskJournal(createTaskJournal([literal], identity), identity).tasks[0].body, literal.body);
});
