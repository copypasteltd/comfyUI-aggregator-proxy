import type { IdentityRef, LocalResult } from "./aggregator-journal";

// A verified content item can be loaded from history before its canvas opens.
// Its first loading UI is not its owner; identity + server artifact membership are.
export function assertLocalResult(result: LocalResult, identity: IdentityRef, contentId: string, jobId?: string): void {
    if (result.version !== 1 || result.identity?.fingerprint !== identity.fingerprint || result.identity?.origin !== identity.origin || result.id !== `studio:${identity.fingerprint}:${contentId}` || result.metadata?.id !== contentId || !(result.blob instanceof Blob) || result.blob.size !== result.metadata.bytes || result.blob.type !== result.metadata.media_type || (jobId && result.jobId && result.jobId !== jobId)) throw new Error("本机原件版本、归属或文件不符，原记录已保留");
}
