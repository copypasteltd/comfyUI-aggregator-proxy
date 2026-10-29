import test from "node:test";
import assert from "node:assert/strict";
import { assertLocalResult } from "../src/services/studio-result-ownership";
import type { LocalResult } from "../src/services/aggregator-journal";

const identity = { profileId: "synthetic-profile", origin: "http://fixture.invalid", fingerprint: "f".repeat(64) };
const content = `content_${"1".repeat(32)}`;
const job = `job_${"2".repeat(32)}`;
const result: LocalResult = { version: 1, id: `studio:${identity.fingerprint}:${content}`, identity, intentId: `remote:${job}`, jobId: job, metadata: { id: content, bytes: 3, sha256: "a".repeat(64), media_type: "image/png", expires_at: 1, metadata: {} }, blob: new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" }), savedAt: 1 };

test("a history-loaded original is reusable by its canvas even after cloud expiry", () => {
    assert.doesNotThrow(() => assertLocalResult(result, identity, content, job));
    assert.doesNotThrow(() => assertLocalResult({ ...result, jobId: undefined, intentId: "original-browser-intent" }, identity, content, job));
});
test("cached content rejects another identity, job, version and damaged local bytes", () => {
    assert.throws(() => assertLocalResult(result, { ...identity, fingerprint: "other-key" }, content, job));
    assert.throws(() => assertLocalResult(result, identity, content, `job_${"3".repeat(32)}`));
    for (const value of [{ ...result, version: 2 }, { ...result, metadata: { ...result.metadata, bytes: 4 } }, { ...result, blob: new Blob(["bad"], { type: "audio/wav" }) }]) assert.throws(() => assertLocalResult(value as LocalResult, identity, content, job));
});
