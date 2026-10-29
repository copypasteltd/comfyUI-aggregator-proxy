import type { ContentMetadata, RemoteJob, UploadedAsset } from "./api/aggregator/types";
import type { CanvasNodeData } from "@/types/canvas";

export type IdentityRef = { profileId: string; origin: string; fingerprint: string };
export type Intent = {
    version: 1; id: string; identity: IdentityRef; projectId: string; nodeId: string;
    body: string; idempotencyKey: string; releaseDigest: string;
    stage: "prepared" | "submitting" | "unknown" | "accepted" | "terminal";
    createdAt: number; updatedAt: number; job?: RemoteJob; error?: string;
    retryAt?: number; cancelRequested?: boolean;
    pollIntervalMs?: number; activeNodeKey?: string;
    pollPaused?: boolean;
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
type JournalRecords = Records & { budgets: RequestBudget };
const databaseName = "comfy-aggregator-studio:journal";
const submissionSnapshot = (intent: Intent) => JSON.stringify({ version: intent.version, id: intent.id, identity: intent.identity, projectId: intent.projectId, nodeId: intent.nodeId, body: intent.body, idempotencyKey: intent.idempotencyKey, releaseDigest: intent.releaseDigest, createdAt: intent.createdAt, output: intent.output, canvas: intent.canvas && { node: intent.canvas.node, sourceIds: intent.canvas.sourceIds } });

// Native transactions deliberately have no localStorage fallback. Completion is the commit signal.
function openDatabase(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
        if (!globalThis.indexedDB) { reject(new Error("当前浏览器无法保存任务日志，已阻止提交；请启用浏览器存储")); return; }
        const request = indexedDB.open(databaseName, 1);
        let abandoned = false;
        request.onupgradeneeded = () => {
            const intents = request.result.createObjectStore("intents", { keyPath: "id" });
            intents.createIndex("active_node", "activeNodeKey", { unique: true });
            for (const name of ["uploads", "results", "budgets"]) request.result.createObjectStore(name, { keyPath: "id" });
        };
        request.onblocked = () => { abandoned = true; reject(new Error("任务日志升级被其他页面阻塞，请关闭旧版本画布页面")); };
        request.onerror = () => reject(request.error || new Error("无法打开任务日志"));
        request.onsuccess = () => {
            if (abandoned) { request.result.close(); return; }
            request.result.onversionchange = () => request.result.close();
            resolve(request.result);
        };
    });
}

async function transaction<T>(name: keyof JournalRecords, mode: IDBTransactionMode, run: (store: IDBObjectStore, setResult: (value: T) => void) => void): Promise<T> {
    const database = await openDatabase();
    try {
        return await new Promise<T>((resolve, reject) => {
            const tx = database.transaction(name, mode, { durability: "strict" });
            let result: T;
            let failure: unknown;
            tx.oncomplete = () => resolve(result);
            tx.onabort = () => reject(failure || tx.error || new Error("任务日志事务未提交，已阻止后续操作"));
            tx.onerror = () => { failure ||= tx.error; };
            try { run(tx.objectStore(name), (value) => { result = value; }); }
            catch (error) { failure = error; tx.abort(); }
        });
    } finally { database.close(); }
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

export function prepareIntent(intent: Intent): Promise<Intent> {
    return transaction("intents", "readwrite", (store, result) => {
        const request = store.get(intent.id);
        request.onsuccess = () => {
            const existing = request.result as Intent | undefined;
            if (existing) {
                if (existing.version !== 1 || submissionSnapshot(existing) !== submissionSnapshot(intent)) {
                    store.transaction.abort();
                    return;
                }
                result(existing);
            } else if (intent.activeNodeKey) {
                const active = store.index("active_node").getKey(intent.activeNodeKey);
                active.onsuccess = () => {
                    if (active.result !== undefined) { store.transaction.abort(); return; }
                    store.add(intent); result(intent);
                };
            } else { store.add(intent); result(intent); }
        };
    });
}

export function updateIntent(id: string, update: (intent: Intent) => Intent): Promise<Intent> {
    return transaction("intents", "readwrite", (store, result) => {
        const request = store.get(id);
        request.onsuccess = () => {
            const existing = request.result as Intent | undefined;
            if (!existing || existing.version !== 1) { store.transaction.abort(); return; }
            const original = submissionSnapshot(existing);
            let value: Intent;
            try { value = update(existing); } catch { store.transaction.abort(); return; }
            // Mutating an intent must never change its original submission identity or bytes.
            if (submissionSnapshot(value) !== original) { store.transaction.abort(); return; }
            if (value.stage === "terminal") delete value.activeNodeKey;
            store.put(value);
            result(value);
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
