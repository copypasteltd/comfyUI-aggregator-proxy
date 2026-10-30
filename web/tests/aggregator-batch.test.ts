import test from "node:test";
import assert from "node:assert/strict";
import { sha256 } from "@noble/hashes/sha256";
import { bytesToHex } from "@noble/hashes/utils";
import { assertBatchMembers, assertBatchRef, batchSummary } from "../src/services/aggregator-batch";
import { createTaskJournal, readTaskJournal, taskSubmissionSignature } from "../src/services/task-journal-format";
import type { Intent } from "../src/services/aggregator-journal";

const owner = { profileId: "batch-owner", origin: "http://batch.test:39443", fingerprint: "a".repeat(64) };
const task = (index = 0): Intent => ({ version: 1, id: `member-${index}`, identity: owner, projectId: "batch-project", nodeId: `batch-node-${index}`, body: JSON.stringify({ model: "image", task_type: "text_to_image", input: { prompt: "原始批次" }, parameters: { n: 1, seed: 42 }, client_request_id: `member-${index}` }), idempotencyKey: `studio-job-member-${index}`, releaseDigest: "b".repeat(64), stage: "prepared", createdAt: 1, updatedAt: 1, output: { mediaTypes: ["image/png"], maxBytes: 1024 }, batch: { id: "original-batch", index, total: 3 } });
const known = (index: number, status: string): Intent => ({ ...task(index), stage: ["succeeded", "failed", "cancelled", "expired"].includes(status) ? "terminal" : "accepted", job: { id: `job_${String(index).padStart(32, "0")}`, object: "generation.job", model: "image", task_type: "text_to_image", status, updated_revision: 2, created_at: 1 } });
const rewrite = (source: string, change: (envelope: any, payload: any) => void) => {
    const envelope = JSON.parse(source), payload = JSON.parse(envelope.payload);
    change(envelope, payload); envelope.payload = JSON.stringify(payload); envelope.sha256 = bytesToHex(sha256(new TextEncoder().encode(envelope.payload))); return JSON.stringify(envelope);
};

test("batch references reuse the existing canvas range and require actual integer positions", () => {
    assertBatchRef({ id: "valid-batch", index: 0, total: 2 });
    assertBatchRef({ id: "valid-batch", index: 14, total: 15 });
    for (const value of [null, [], {}, { id: "invalid/route", index: 0, total: 2 }, { id: "batch", index: 0, total: 1 }, { id: "batch", index: 0, total: 16 }, { id: "batch", index: -1, total: 2 }, { id: "batch", index: 2, total: 2 }, { id: "batch", index: 0.5, total: 2 }, { id: "batch", index: 0, total: "2" }, { id: "batch", index: 0, total: 2, apiKey: "private" }]) assert.throws(() => assertBatchRef(value));
});
test("batch membership detects duplicated indices rather than silently dropping a member", () => {
    assertBatchMembers([task(0), task(1), task(2)]);
    assert.throws(() => assertBatchMembers([task(0), { ...task(1), batch: task(0).batch }]));
});
test("original batch inputs, parameters, model and release cannot diverge between members", () => {
    for (const change of [(value: Intent) => { value.releaseDigest = "c".repeat(64); }, (value: Intent) => { value.batch!.total = 4; }, (value: Intent) => { const body = JSON.parse(value.body); body.model = "other"; value.body = JSON.stringify(body); }, (value: Intent) => { const body = JSON.parse(value.body); body.parameters.seed = 99; value.body = JSON.stringify(body); }, (value: Intent) => { const body = JSON.parse(value.body); body.input.prompt = "changed"; value.body = JSON.stringify(body); }]) {
        const changed = task(1); change(changed); assert.throws(() => assertBatchMembers([task(0), changed]));
    }
});
test("batch identifiers are scoped by original origin, key and canvas project", () => {
    for (const change of [(value: Intent) => { value.identity = { ...owner, origin: "http://other.test" }; }, (value: Intent) => { value.identity = { ...owner, fingerprint: "f".repeat(64) }; }, (value: Intent) => { value.projectId = "other-project"; }]) {
        const independent = task(0); change(independent); assertBatchMembers([task(0), independent]);
    }
});
test("partial batches retain their declared total and do not invent missing members", () => {
    const tasks = [task(0), task(2)]; assertBatchMembers(tasks);
    assert.equal(batchSummary(tasks).prepared, 2); assert.equal(tasks[0].batch!.total, 3);
});
test("batch summary separates pending, unknown, active and every authoritative terminal state", () => {
    const tasks = [task(0), { ...task(1), stage: "unknown" as const }, known(2, "running"), known(3, "succeeded"), known(4, "failed"), known(5, "cancelled"), known(6, "expired")].map((value) => ({ ...value, batch: { ...value.batch!, total: 7 } }));
    assert.deepEqual(batchSummary(tasks), { prepared: 1, unknown: 1, active: 1, succeeded: 1, failed: 1, cancelled: 1, expired: 1, unverified: 0 });
});
test("a prepared or successful imported record never appears as new work or trusted success", () => {
    const recovery = { version: 1 as const, importedAt: 1, archiveSha256: "c".repeat(64) };
    assert.deepEqual(batchSummary([{ ...task(0), recovered: recovery }, { ...known(1, "succeeded"), recovered: recovery }, { ...known(2, "failed"), recovered: recovery, recoveryVerified: true }]), { prepared: 0, unknown: 0, active: 0, succeeded: 0, failed: 1, cancelled: 0, expired: 0, unverified: 2 });
});
test("v2 batch journals preserve all original bytes, independent keys and grouping", () => {
    const records = [task(0), task(1), task(2)];
    const source = createTaskJournal(records, owner);
    assert.equal(JSON.parse(source).version, 2);
    const restored = readTaskJournal(source, owner).tasks;
    assert.deepEqual(restored.map((value) => value.batch), records.map((value) => value.batch));
    assert.deepEqual(restored.map(taskSubmissionSignature), records.map(taskSubmissionSignature));
    assert.deepEqual(restored.map((value) => value.body), records.map((value) => value.body));
});
test("non-batch backups remain v1 and readable while unknown envelope versions are rejected", () => {
    const single = task(0); delete single.batch;
    const source = createTaskJournal([single], owner);
    assert.equal(JSON.parse(source).version, 1); assert.equal(readTaskJournal(source, owner).tasks.length, 1);
    assert.throws(() => readTaskJournal(rewrite(source, (envelope) => { envelope.version = 99; }), owner));
});
test("v1 cannot smuggle batch fields and v2 refuses contradictory or credential-bearing members", () => {
    const source = createTaskJournal([task(0), task(1)], owner);
    assert.throws(() => readTaskJournal(rewrite(source, (envelope) => { envelope.version = 1; }), owner));
    for (const change of [(payload: any) => { payload.tasks[1].batch.index = 0; }, (payload: any) => { payload.tasks[1].batch.total = 4; }, (payload: any) => { payload.tasks[0].batch.authorization = "private"; }]) assert.throws(() => readTaskJournal(rewrite(source, (_, payload) => change(payload)), owner));
});
test("batch association participates in original-submission conflict detection", () => {
    assert.notEqual(taskSubmissionSignature(task(0)), taskSubmissionSignature({ ...task(0), batch: { id: "changed", index: 0, total: 3 } }));
});
