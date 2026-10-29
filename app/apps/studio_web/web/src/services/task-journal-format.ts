import { sha256 } from "@noble/hashes/sha256";
import { bytesToHex } from "@noble/hashes/utils";
import { taskTypes, terminalStatuses, type RemoteJob } from "./api/aggregator/types";
import type { IdentityRef, Intent } from "./aggregator-journal";

export type TaskJournalRecord = Pick<Intent, "version" | "id" | "identity" | "projectId" | "nodeId" | "body" | "idempotencyKey" | "releaseDigest" | "stage" | "createdAt" | "updatedAt" | "output" | "job">;
export type TaskJournalBackup = { exportedAt: number; identity: Pick<IdentityRef, "origin" | "fingerprint">; tasks: TaskJournalRecord[] };
const digest = (value: string) => bytesToHex(sha256(new TextEncoder().encode(value)));
const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const natural = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
function fail(): never { throw new Error("任务清单结构、版本或关联无效，未写入任何记录"); }
function fields(value: unknown, names: string[]): asserts value is Record<string, unknown> {
    if (!object(value) || Object.keys(value).some((key) => !names.includes(key))) fail();
}
function identity(value: unknown): asserts value is IdentityRef {
    fields(value, ["profileId", "origin", "fingerprint"]);
    if (!text(value.profileId) || !text(value.origin) || !hash(value.fingerprint)) fail();
    let url: URL;
    try { url = new URL(value.origin); } catch { fail(); }
    if (!["http:", "https:"].includes(url.protocol) || url.origin !== value.origin) fail();
}
export function sameJournalIdentity(a: Pick<IdentityRef, "origin" | "fingerprint">, b: Pick<IdentityRef, "origin" | "fingerprint">) {
    return a.origin === b.origin && a.fingerprint === b.fingerprint;
}
const statuses = ["admission_pending", "queued", "leased", "preparing", "submitting", "running", "collecting", "uploading", "verifying", "reconciling", "retry_wait", "cancel_requested", "succeeded", "failed", "cancelled", "expired"];

function validateRequest(value: unknown) {
    if (Array.isArray(value)) { value.forEach(validateRequest); return; }
    if (object(value)) for (const [key, item] of Object.entries(value)) {
        if (/^(__proto__|prototype|constructor|api[_-]?key|authorization|password|secret|token|access[_-]?token|refresh[_-]?token|csrf[_-]?token)$/i.test(key)) fail();
        validateRequest(item);
    }
    else if (typeof value === "number" && !Number.isFinite(value)) fail();
}

export function validateTaskRecord(value: unknown): asserts value is TaskJournalRecord {
    fields(value, ["version", "id", "identity", "projectId", "nodeId", "body", "idempotencyKey", "releaseDigest", "stage", "createdAt", "updatedAt", "output", "job"]);
    if (value.version !== 1 || !text(value.id) || !/^[A-Za-z0-9_-]+$/.test(value.id) || !text(value.projectId) || !text(value.nodeId) || !text(value.body) || value.idempotencyKey !== `studio-job-${value.id}` || !hash(value.releaseDigest) || !natural(value.createdAt) || !natural(value.updatedAt) || !["prepared", "submitting", "unknown", "accepted", "terminal"].includes(String(value.stage))) fail();
    identity(value.identity);
    let request;
    try { request = JSON.parse(value.body); } catch { fail(); }
    fields(request, ["model", "task_type", "input", "parameters", "client_request_id"]);
    if (!text(request.model) || !taskTypes.includes(request.task_type as never) || !object(request.input) || !object(request.parameters) || request.client_request_id !== value.id) fail();
    validateRequest(request);
    fields(value.output, ["mediaTypes", "maxBytes"]);
    if (!Array.isArray(value.output.mediaTypes) || !value.output.mediaTypes.length || !value.output.mediaTypes.every((item) => typeof item === "string" && /^[\w.+-]+\/[\w.+-]+$/.test(item)) || !natural(value.output.maxBytes) || value.output.maxBytes === 0) fail();
    if (value.job !== undefined) {
        const job = value.job;
        fields(job, ["id", "object", "model", "task_type", "status", "updated_revision", "created_at", "completed_at", "artifacts"]);
        if (typeof job.id !== "string" || !/^job_[a-f0-9]{32}$/.test(job.id) || job.object !== "generation.job" || job.model !== request.model || job.task_type !== request.task_type || !statuses.includes(String(job.status)) || !natural(job.updated_revision) || !natural(job.created_at) || job.completed_at !== undefined && !natural(job.completed_at) || value.stage !== (terminalStatuses.has(String(job.status)) ? "terminal" : "accepted")) fail();
        if (job.artifacts !== undefined) {
            if (!Array.isArray(job.artifacts)) fail();
            const ids = new Set();
            for (const artifact of job.artifacts) {
                fields(artifact, ["id"]);
                if (typeof artifact.id !== "string" || !/^content_[a-f0-9]{32}$/.test(artifact.id) || ids.has(artifact.id)) fail();
                ids.add(artifact.id);
            }
        }
    } else if (!["prepared", "submitting", "unknown"].includes(String(value.stage))) fail();
}

// Project explicit fields only: connection secrets, canvas/plugin data and transient errors never enter the file.
export function taskJournalRecord(intent: Intent): TaskJournalRecord {
    const { version, id, identity: owner, projectId, nodeId, body, idempotencyKey, releaseDigest, stage, createdAt, updatedAt, output } = intent;
    const job = intent.job && { id: intent.job.id, object: intent.job.object, model: intent.job.model, task_type: intent.job.task_type, status: intent.job.status, updated_revision: intent.job.updated_revision, created_at: intent.job.created_at, ...(intent.job.completed_at !== undefined ? { completed_at: intent.job.completed_at } : {}), ...(intent.job.artifacts ? { artifacts: intent.job.artifacts.map(({ id }) => ({ id })) } : {}) } as RemoteJob;
    const result = { version, id, identity: { profileId: owner.profileId, origin: owner.origin, fingerprint: owner.fingerprint }, projectId, nodeId, body, idempotencyKey, releaseDigest, stage, createdAt, updatedAt, output: { mediaTypes: output.mediaTypes, maxBytes: output.maxBytes }, ...(job ? { job } : {}) };
    validateTaskRecord(result);
    return result;
}

export function taskSubmissionSignature(task: TaskJournalRecord) {
    return JSON.stringify([task.version, task.id, task.identity.profileId, task.identity.origin, task.identity.fingerprint, task.projectId, task.nodeId, task.body, task.idempotencyKey, task.releaseDigest, task.createdAt, task.output.mediaTypes, task.output.maxBytes]);
}

export function readTaskJournal(source: string, owner: Pick<IdentityRef, "origin" | "fingerprint">): TaskJournalBackup & { sha256: string } {
    let envelope, payload;
    try { envelope = JSON.parse(source); } catch { fail(); }
    fields(envelope, ["format", "version", "payload", "sha256"]);
    if (envelope.format !== "comfy-studio-task-journal" || envelope.version !== 1 || !text(envelope.payload) || !hash(envelope.sha256)) fail();
    if (digest(envelope.payload) !== envelope.sha256) throw new Error("任务清单SHA-256不符，未写入任何记录");
    try { payload = JSON.parse(envelope.payload); } catch { fail(); }
    fields(payload, ["exportedAt", "identity", "tasks"]);
    fields(payload.identity, ["origin", "fingerprint"]);
    identity({ ...payload.identity, profileId: "backup" });
    if (!sameJournalIdentity(payload.identity as Pick<IdentityRef, "origin" | "fingerprint">, owner)) throw new Error("请连接清单对应的原服务及原API Key，未导入其他身份的记录");
    if (!natural(payload.exportedAt) || !Array.isArray(payload.tasks)) fail();
    const ids = new Set();
    const nodes = new Set();
    for (const task of payload.tasks) {
        validateTaskRecord(task);
        const node = `${task.identity.fingerprint}:${task.projectId}:${task.nodeId}`;
        if (!sameJournalIdentity(task.identity, owner) || ids.has(task.id) || task.stage !== "terminal" && nodes.has(node)) fail();
        ids.add(task.id);
        if (task.stage !== "terminal") nodes.add(node);
    }
    return { ...(payload as TaskJournalBackup), sha256: envelope.sha256 };
}

export function createTaskJournal(tasks: Intent[], owner: Pick<IdentityRef, "origin" | "fingerprint">): string {
    const payload = JSON.stringify({ exportedAt: Date.now(), identity: { origin: owner.origin, fingerprint: owner.fingerprint }, tasks: tasks.map(taskJournalRecord) } satisfies TaskJournalBackup);
    const source = JSON.stringify({ format: "comfy-studio-task-journal", version: 1, payload, sha256: digest(payload) }, null, 2);
    readTaskJournal(source, owner);
    return source;
}
