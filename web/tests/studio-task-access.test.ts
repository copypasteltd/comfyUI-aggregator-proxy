import test from "node:test";
import assert from "node:assert/strict";
import type { Intent, TaskReadAccess } from "../src/services/aggregator-journal";
import { canReadTask, submissionHash, taskAccessReason } from "../src/services/studio-task-access";
import { createTaskJournal, readTaskJournal, readTaskJournalFromOrigin, taskJournalRecord } from "../src/services/task-journal-format";

const owner = { profileId: "original", origin: "http://access.test", fingerprint: "a".repeat(64) };
const target = { profileId: "rotated", origin: owner.origin, fingerprint: "b".repeat(64) };
const task = (): Intent => ({ version: 1, id: "rotation-intent", identity: { ...owner }, projectId: "original-project", nodeId: "original-node", body: JSON.stringify({ model: "original-model", task_type: "text_to_image", input: { prompt: "原提示词" }, parameters: { n: 1 }, client_request_id: "rotation-intent" }), idempotencyKey: "studio-job-rotation-intent", releaseDigest: "c".repeat(64), stage: "accepted", createdAt: 1, updatedAt: 2, output: { mediaTypes: ["image/png"], maxBytes: 1024 }, job: { id: `job_${"1".repeat(32)}`, object: "generation.job", model: "original-model", task_type: "text_to_image", status: "queued", updated_revision: 1, created_at: 1 } });
const grant = (value: Intent): TaskReadAccess => ({ version: 1, identity: { ...target }, jobId: value.job!.id, submissionSha256: submissionHash(value), verifiedAt: 1 });

test("known task access is limited to original identity until explicitly associated", () => {
    const value = task();
    assert.equal(canReadTask(value, owner), true);
    assert.equal(canReadTask(value, target), false);
    value.readAccess = [grant(value)];
    assert.equal(canReadTask(value, target), true);
    assert.equal(canReadTask(value, { ...target, origin: "https://access.test" }), false);
    assert.equal(canReadTask(value, { ...target, fingerprint: "d".repeat(64) }), false);
});
test("a read association is bound to original body, identity, job and project", () => {
    const value = task(); value.readAccess = [grant(value)];
    for (const mutate of [(x: Intent) => { x.body += " "; }, (x: Intent) => { x.identity.profileId = "other"; }, (x: Intent) => { x.projectId = "other"; }, (x: Intent) => { x.job!.id = `job_${"2".repeat(32)}`; }, (x: Intent) => { x.idempotencyKey += "other"; }, (x: Intent) => { x.releaseDigest = "f".repeat(64); }]) {
        const changed = structuredClone(value); mutate(changed); assert.equal(canReadTask(changed, target), false);
    }
});
test("unknown submissions and prepared records cannot use a new Key", () => {
    for (const stage of ["prepared", "submitting", "unknown"] as const) {
        const value = task(); delete value.job; value.stage = stage;
        assert.match(taskAccessReason(value, target)!, /任务ID/);
        assert.equal(canReadTask(value, target), false);
    }
});
test("same-origin metadata and a matching fingerprint are not server permission", () => {
    const value = task();
    assert.match(taskAccessReason(value, owner)!, /无需接管/);
    assert.match(taskAccessReason(value, { ...target, origin: "http://other.test" })!, /原服务/);
    value.readAccess = [{ ...grant(value), version: 99 } as never];
    assert.equal(canReadTask(value, target), false);
    assert.match(taskAccessReason(value, target)!, /版本|归属/);
});
test("damaged requests and access manifests cannot be adopted", () => {
    const value = task(); value.body = "broken";
    assert.match(taskAccessReason(value, target)!, /原任务记录/);
    const other = task(); other.readAccess = {} as never;
    assert.equal(canReadTask(other, target), false);
    assert.match(taskAccessReason(other, target)!, /版本|归属/);
});
test("mutable remote progress does not change the frozen submission binding", () => {
    const value = task(); value.readAccess = [grant(value)];
    const before = submissionHash(value);
    value.updatedAt++; value.pollPaused = true; value.error = "retry later"; value.job!.updated_revision++; value.job!.status = "running";
    assert.equal(submissionHash(value), before);
    assert.equal(canReadTask(value, target), true);
});
test("task journal exports the original identity and excludes access proofs", () => {
    const value = task(); value.readAccess = [grant(value)];
    assert.equal("readAccess" in taskJournalRecord(value), false);
    const source = createTaskJournal([value], owner), parsed = readTaskJournal(source, owner);
    assert.equal(parsed.identity.fingerprint, owner.fingerprint);
    assert.equal("readAccess" in parsed.tasks[0], false);
    assert.throws(() => readTaskJournal(source, target), /原API Key/);
});
test("explicit origin-only restore validates the full old journal without granting access", () => {
    const value = task(), source = createTaskJournal([value], owner);
    const restored = readTaskJournalFromOrigin(source, target.origin);
    assert.equal(restored.tasks[0].body, value.body);
    assert.deepEqual(restored.tasks[0].identity, value.identity);
    assert.equal(canReadTask(restored.tasks[0] as Intent, target), false);
    assert.throws(() => readTaskJournalFromOrigin(source, "http://other.test"), /原服务/);
});
test("malformed association metadata never bypasses structural validation", () => {
    const value = task();
    for (const change of [{ verifiedAt: 0 }, { verifiedAt: NaN }, { submissionSha256: "wrong" }, { identity: { ...target, profileId: "" } }, { jobId: "job_bad" }]) {
        value.readAccess = [{ ...grant(value), ...change }]; assert.equal(canReadTask(value, target), false);
    }
});
test("multiple explicit Key associations remain independent", () => {
    const value = task(), third = { ...target, fingerprint: "d".repeat(64), profileId: "third" };
    value.readAccess = [grant(value), { ...grant(value), identity: third }];
    assert.equal(canReadTask(value, target), true); assert.equal(canReadTask(value, third), true);
    value.readAccess = value.readAccess.filter((item) => item.identity.fingerprint !== target.fingerprint);
    assert.equal(canReadTask(value, target), false); assert.equal(canReadTask(value, third), true); assert.equal(canReadTask(value, owner), true);
});
