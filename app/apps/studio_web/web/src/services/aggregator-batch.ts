import { MAX_GENERATION_COUNT } from "@/constant/generation";
import type { Intent } from "./aggregator-journal";

export type BatchRef = { id: string; index: number; total: number };

export const batchKey = (task: Pick<Intent, "batch" | "identity" | "projectId">) => JSON.stringify([task.identity.origin, task.identity.fingerprint, task.projectId, task.batch?.id]);

export function assertBatchRef(value: unknown): asserts value is BatchRef {
    const batch = value as BatchRef;
    if (!batch || typeof batch !== "object" || Array.isArray(batch) || Object.keys(batch).some((key) => !["id", "index", "total"].includes(key)) || typeof batch.id !== "string" || !/^[A-Za-z0-9_-]+$/.test(batch.id) || !Number.isSafeInteger(batch.total) || batch.total < 2 || batch.total > MAX_GENERATION_COUNT || !Number.isSafeInteger(batch.index) || batch.index < 0 || batch.index >= batch.total) throw new Error("批次关联无效，原记录已保留");
}

export function assertBatchMembers(tasks: Pick<Intent, "batch" | "identity" | "projectId" | "body" | "releaseDigest">[]) {
    const groups = new Map<string, { signature: string; indices: Set<number> }>();
    for (const task of tasks) {
        if (!task.batch) continue;
        assertBatchRef(task.batch);
        const { client_request_id: _requestId, ...request } = JSON.parse(task.body);
        const signature = JSON.stringify([task.batch.total, task.releaseDigest, request]);
        const key = batchKey(task);
        const group = groups.get(key) || { signature, indices: new Set<number>() };
        if (group.signature !== signature || group.indices.has(task.batch.index)) throw new Error("批次成员冲突，未修改原记录");
        group.indices.add(task.batch.index); groups.set(key, group);
    }
}

export function batchSummary(tasks: Intent[]) {
    assertBatchMembers(tasks);
    const counts = { prepared: 0, unknown: 0, active: 0, succeeded: 0, failed: 0, cancelled: 0, expired: 0, unverified: 0 };
    for (const task of tasks) {
        if (task.recovered && !task.recoveryVerified) counts.unverified++;
        else if (task.job && ["succeeded", "failed", "cancelled", "expired"].includes(task.job.status)) counts[task.job.status as "succeeded" | "failed" | "cancelled" | "expired"]++;
        else if (task.job) counts.active++;
        else if (task.stage === "prepared" && !task.recovered) counts.prepared++;
        else counts.unknown++;
    }
    return counts;
}
