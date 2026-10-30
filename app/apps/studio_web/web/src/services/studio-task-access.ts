import { sha256 } from "@noble/hashes/sha256";
import { bytesToHex } from "@noble/hashes/utils";
import { submissionSnapshot, type IdentityRef, type Intent, type TaskReadAccess } from "./aggregator-journal";
import { taskJournalRecord } from "./task-journal-format";

export const sameTaskIdentity = (a: Pick<IdentityRef, "origin" | "fingerprint">, b: Pick<IdentityRef, "origin" | "fingerprint">) => a.origin === b.origin && a.fingerprint === b.fingerprint;
export const submissionHash = (intent: Intent) => bytesToHex(sha256(new TextEncoder().encode(submissionSnapshot(intent))));

export function validTaskAccess(value: TaskReadAccess, intent: Intent): boolean {
    return value?.version === 1 && typeof value.identity?.profileId === "string" && Boolean(value.identity.profileId) && value.identity.origin === intent.identity.origin && /^[a-f0-9]{64}$/.test(value.identity.fingerprint) && /^job_[a-f0-9]{32}$/.test(value.jobId) && value.jobId === intent.job?.id && value.submissionSha256 === submissionHash(intent) && Number.isSafeInteger(value.verifiedAt) && value.verifiedAt > 0;
}

export function canReadTask(intent: Intent, identity: Pick<IdentityRef, "origin" | "fingerprint">): boolean {
    if (intent.version !== 1 || !intent.identity) return false;
    return sameTaskIdentity(intent.identity, identity) || Array.isArray(intent.readAccess) && intent.readAccess.some((item) => validTaskAccess(item, intent) && sameTaskIdentity(item.identity, identity));
}

export function taskAccessReason(intent: Intent, identity: IdentityRef): string | undefined {
    if (intent.version !== 1 || !intent.identity || intent.identity.origin !== identity.origin) return "仅能在原服务接管已知任务";
    if (sameTaskIdentity(intent.identity, identity)) return "这是当前Key的原任务，无需接管";
    try { taskJournalRecord(intent); } catch { return "原任务记录需要核对，未接管或修改原提交"; }
    if (!intent.job || !/^job_[a-f0-9]{32}$/.test(intent.job.id)) return "没有已确认的任务ID，不能换Key补发或核对提交";
    if (intent.readAccess !== undefined && (!Array.isArray(intent.readAccess) || !intent.readAccess.every((item) => validTaskAccess(item, intent)))) return "访问关联版本或归属需要核对，原记录已保留";
    return undefined;
}
