import type { ContentMetadata, RemoteJob, UploadedAsset } from "./api/aggregator/types";
import type { CanvasNodeData } from "@/types/canvas";
import { assertBatchMembers, batchKey, type BatchRef } from "./aggregator-batch";
import { editorTransaction, type EditorClaim } from "./studio-editor-session";
import type { ManagedLocalMedia } from "./studio-local-media-types";

export type IdentityRef = { profileId: string; origin: string; fingerprint: string };
export type TaskReadAccess = { version: 1; identity: IdentityRef; jobId: string; submissionSha256: string; verifiedAt: number };
export type Intent = {
    version: 1; id: string; identity: IdentityRef; projectId: string; nodeId: string;
    body: string; idempotencyKey: string; releaseDigest: string;
    stage: "prepared" | "submitting" | "unknown" | "accepted" | "terminal";
    createdAt: number; updatedAt: number; job?: RemoteJob; error?: string;
    retryAt?: number; cancelRequested?: boolean;
    pollIntervalMs?: number; activeNodeKey?: string;
    pollPaused?: boolean;
    recovered?: { version: 1; archiveSha256: string; importedAt: number };
    recoveryVerified?: boolean;
    readAccess?: TaskReadAccess[];
    batch?: BatchRef;
    output: { mediaTypes: string[]; maxBytes: number };
    canvas?: { node: CanvasNodeData; sourceIds: string[]; detached?: boolean };
};
export type InputUpload = {
    version: 1; id: string; identity: IdentityRef; name: string; blob: Blob;
    sha256: string; idempotencyKey: string; createdAt: number; asset?: UploadedAsset; error?: string;
};
export type LocalResult = { version: 1; id: string; identity: IdentityRef; intentId: string; jobId?: string; metadata: ContentMetadata; blob: Blob; savedAt: number };
type Records = { intents: Intent; uploads: InputUpload; results: LocalResult };
export type RequestBudget = { id: string; nextAt: number; pausedUntil: number };
export type LocalDocument = { version: 1; id: string; revision: number; value: string; storageKeys: string[]; legacyValue: string | null };
type JournalRecords = Records & { budgets: RequestBudget; documents: LocalDocument; local_media: ManagedLocalMedia; editors: EditorClaim };
export const submissionSnapshot = (intent: Intent) => JSON.stringify({ version: intent.version, id: intent.id, identity: intent.identity, projectId: intent.projectId, nodeId: intent.nodeId, body: intent.body, idempotencyKey: intent.idempotencyKey, releaseDigest: intent.releaseDigest, createdAt: intent.createdAt, output: intent.output, canvas: intent.canvas && { node: intent.canvas.node, sourceIds: intent.canvas.sourceIds }, recovered: intent.recovered, batch: intent.batch });

// Registration and the business operation share the editors-store lock. A
// maintenance transaction cannot delete files beneath an editing page.
export function journalTransaction<T>(names: (keyof JournalRecords)[], mode: IDBTransactionMode, run: (tx: IDBTransaction, setResult: (value: T) => void, abort: (error: unknown) => void) => void): Promise<T> {
    return editorTransaction(names, mode, run);
}

function transaction<T>(name: keyof JournalRecords, mode: IDBTransactionMode, run: (store: IDBObjectStore, setResult: (value: T) => void) => void): Promise<T> {
    return journalTransaction([name], mode, (tx, result) => run(tx.objectStore(name), result));
}

export function journalList<N extends keyof JournalRecords>(name: N): Promise<JournalRecords[N][]> {
    return transaction(name, "readonly", (store, result) => {
        const request = store.getAll();
        request.onsuccess = () => result(request.result as JournalRecords[N][]);
    });
}

export function localResultIndex(): Promise<Omit<LocalResult, "blob">[]> {
    return transaction("results", "readonly", (store, result) => {
        const values: Omit<LocalResult, "blob">[] = [];
        const cursor = store.openCursor();
        cursor.onsuccess = () => {
            if (!cursor.result) { result(values); return; }
            const { blob: _blob, ...metadata } = cursor.result.value as LocalResult;
            values.push(metadata); cursor.result.continue();
        };
    });
}

export function journalGet<N extends keyof JournalRecords>(name: N, id: string): Promise<JournalRecords[N] | undefined> {
    return transaction(name, "readonly", (store, result) => {
        const request = store.get(id);
        request.onsuccess = () => result(request.result as JournalRecords[N] | undefined);
    });
}

export function journalPut<N extends keyof JournalRecords>(name: N, value: JournalRecords[N]): Promise<JournalRecords[N]> {
    return transaction(name, "readwrite", (store, result) => { store.put(value); result(value); });
}

export function journalDelete(name: keyof JournalRecords, id: string): Promise<void> {
    return transaction(name, "readwrite", (store, result) => { store.delete(id); result(); });
}

export function updateUpload(original: InputUpload, change: (current: InputUpload) => InputUpload): Promise<InputUpload> {
    return journalTransaction(["uploads"], "readwrite", (tx, result, abort) => {
        const store = tx.objectStore("uploads");
        const request = store.get(original.id);
        request.onsuccess = () => {
            try {
                const current = request.result as InputUpload | undefined;
                if (!current) throw new Error("本机参考缓存已清理，请重新选图");
                if (current.version !== 1 || current.sha256 !== original.sha256 || current.idempotencyKey !== original.idempotencyKey || current.identity?.origin !== original.identity.origin || current.identity?.fingerprint !== original.identity.fingerprint) throw new Error("原上传记录已变化，未覆盖缓存");
                const next = change(current);
                store.put(next); result(next);
            } catch (error) { abort(error); }
        };
    });
}

export function prepareIntent(intent: Intent): Promise<Intent> {
    return prepareIntents([intent]).then((saved) => saved[0]);
}

export function prepareIntents(intents: Intent[]): Promise<Intent[]> {
    if (!intents.length || new Set(intents.map((intent) => intent.id)).size !== intents.length) throw new Error("提交记录为空或重复，未创建任务");
    assertBatchMembers(intents);
    return journalTransaction(["intents"], "readwrite", (tx, result, abort) => {
        const store = tx.objectStore("intents");
        const request = store.getAll();
        request.onsuccess = () => {
            try {
                const records = request.result as Intent[];
                const byId = new Map(records.map((value) => [value.id, value]));
                const active = new Set(records.flatMap((value) => value.activeNodeKey ? [value.activeNodeKey] : []));
                const saved: Intent[] = [];
                for (const intent of intents) {
                    const existing = byId.get(intent.id);
                    if (existing) {
                        if (existing.version !== 1 || submissionSnapshot(existing) !== submissionSnapshot(intent)) throw new Error("同ID提交记录冲突，未创建任务");
                        saved.push(existing); continue;
                    }
                    if (intent.activeNodeKey && active.has(intent.activeNodeKey)) throw new Error("该节点已有活动任务，未创建新任务");
                    if (intent.activeNodeKey) active.add(intent.activeNodeKey);
                    store.add(intent); saved.push(intent);
                }
                const groups = new Set(intents.filter((intent) => intent.batch).map(batchKey));
                assertBatchMembers([...records.filter((intent) => intent.batch && groups.has(batchKey(intent))), ...intents.filter((intent) => !byId.has(intent.id))]);
                result(saved);
            } catch (error) { abort(error); }
        };
    });
}

export function updateIntent(id: string, update: (intent: Intent) => Intent): Promise<Intent> {
    return journalTransaction(["intents"], "readwrite", (tx, result, abort) => {
        const store = tx.objectStore("intents");
        const request = store.get(id);
        request.onsuccess = () => {
            try {
                const existing = request.result as Intent | undefined;
                if (!existing || existing.version !== 1) throw new Error("原任务记录不存在或版本不受支持，未修改日志");
                const original = submissionSnapshot(existing);
                const value = update(existing);
                // Mutating an intent must never change its original submission identity or bytes.
                if (submissionSnapshot(value) !== original) throw new Error("原提交不可改写，未修改任务日志");
                if (value.stage === "terminal") delete value.activeNodeKey;
                store.put(value);
                result(value);
            } catch (error) { abort(error); }
        };
    });
}

export function updateRequestBudget(id: string, update: (budget: RequestBudget) => RequestBudget): Promise<RequestBudget> {
    return transaction("budgets", "readwrite", (store, result) => {
        const request = store.get(id);
        request.onsuccess = () => {
            const budget = update(request.result || { id, nextAt: 0, pausedUntil: 0 });
            store.put(budget); result(budget);
        };
    });
}
