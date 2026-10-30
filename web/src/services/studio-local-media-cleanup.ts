import { canvasStorageKeys } from "@/lib/canvas/canvas-transfer";
import { documentNames, documentReferences, readDocument } from "./studio-documents";
import { rawJournalTransaction } from "./studio-journal-db";
import { validEditorClaim } from "./studio-editor-session";
import { isManagedMediaKey, validManagedMedia, type ManagedLocalMedia } from "./studio-local-media-types";
import type { LocalDocument } from "./aggregator-journal";

const stores = ["editors", "local_media", "documents", "intents", "uploads", "results"];
type Snapshot = Record<typeof stores[number], any[]>;
export type CleanupFile = Omit<ManagedLocalMedia, "blob"> & { references: string[] };
const object = (value: unknown): value is Record<string, any> => !!value && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const identity = (value: unknown) => object(value) && [value.profileId, value.origin, value.fingerprint].every(text);

function protectedReferences(snapshot: Snapshot) {
    const references = new Map<string, Set<string>>();
    const include = (label: string, value: unknown) => {
        // Unknown reference shapes are not proof that an original is unused.
        const inspect = (entry: unknown) => {
            if (!entry || typeof entry !== "object") return;
            if ("storageKey" in entry && entry.storageKey !== undefined && typeof entry.storageKey !== "string") throw new Error("存在无法核对的文件引用，原件继续保留");
            if ("references" in entry && entry.references !== undefined && (!Array.isArray(entry.references) || entry.references.some((key) => typeof key !== "string"))) throw new Error("存在无法核对的参考引用，原件继续保留");
            Object.values(entry).forEach(inspect);
        };
        inspect(value);
        for (const key of canvasStorageKeys(value)) {
            const labels = references.get(key) || new Set<string>(); labels.add(label); references.set(key, labels);
        }
    };
    if (snapshot.documents.length !== documentNames.length) throw new Error("画布及素材文档不完整，原件继续保留");
    for (const name of documentNames) {
        const value = readDocument(snapshot.documents.find((entry) => entry?.id === name) as LocalDocument | undefined, name);
        include(name === documentNames[0] ? "画布及可恢复回收站" : "我的素材", JSON.parse(value.value).state);
        if (value.legacyValue !== null) {
            documentReferences(name, value.legacyValue);
            include("迁移前保留文档", JSON.parse(value.legacyValue).state);
        }
    }
    for (const value of snapshot.intents) {
        if (!object(value) || value.version !== 1 || !text(value.id) || !identity(value.identity) || !["prepared", "submitting", "unknown", "accepted", "terminal"].includes(value.stage) || !text(value.body) || !text(value.idempotencyKey)) throw new Error("存在不受支持或损坏的任务记录，原件继续保留");
        let body: unknown;
        try { body = JSON.parse(value.body); if (!object(body)) throw new Error(); } catch { throw new Error("原任务正文无法完整核对，原件继续保留"); }
        include("冻结的原任务", value); include("冻结的原请求", body);
    }
    for (const value of snapshot.uploads) {
        if (!object(value) || value.version !== 1 || !text(value.id) || !identity(value.identity) || !(value.blob instanceof Blob) || typeof value.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.sha256) || !text(value.idempotencyKey)) throw new Error("参考缓存结构无法完整核对，原件继续保留");
        include("参考缓存", value);
    }
    for (const value of snapshot.results) {
        if (!object(value) || value.version !== 1 || !text(value.id) || !identity(value.identity) || !(value.blob instanceof Blob) || !object(value.metadata)) throw new Error("生成原件记录无法完整核对，原件继续保留");
        include("生成原件记录", value);
    }
    return references;
}

export function describeCleanupSnapshot(snapshot: Snapshot) {
    const issues: string[] = [];
    let references = new Map<string, Set<string>>();
    try { references = protectedReferences(snapshot); } catch (error) { issues.push(error instanceof Error ? error.message : "本机引用无法完整核对，原件继续保留"); }
    if (snapshot.editors.some((entry) => !validEditorClaim(entry))) issues.push("存在未知页面占用记录，禁止清理");
    if (snapshot.local_media.some((entry) => !validManagedMedia(entry))) issues.push("存在未知或损坏的新版原件记录，禁止清理");
    const originals = new Set(snapshot.local_media.filter(validManagedMedia).map((entry) => entry.id));
    if ([...references.keys()].some((key) => isManagedMediaKey(key) && !originals.has(key))) issues.push("文档或任务引用的新版原件缺失，请先恢复重要作品，暂不清理");
    const files: CleanupFile[] = snapshot.local_media.filter(validManagedMedia).map(({ blob: _blob, ...entry }) => ({ ...entry, references: [...(references.get(entry.id) || [])].sort() }));
    return { capturedAt: new Date().toISOString(), editors: snapshot.editors as unknown[], files, issues, canDelete: snapshot.editors.length === 0 && issues.length === 0 };
}
export type CleanupSnapshot = ReturnType<typeof describeCleanupSnapshot>;

function transaction<T>(mode: IDBTransactionMode, run: (snapshot: Snapshot, tx: IDBTransaction, result: (value: T) => void, abort: (error: unknown) => void) => void) {
    return rawJournalTransaction<T>(stores, mode, (tx, result, abort) => {
        const snapshot: Snapshot = {};
        let remaining = stores.length;
        for (const name of stores) {
            const request = tx.objectStore(name).getAll();
            request.onsuccess = () => {
                snapshot[name] = request.result;
                if (--remaining === 0) { try { run(snapshot, tx, result, abort); } catch (error) { abort(error); } }
            };
        }
    });
}

export const readCleanupSnapshot = () => transaction<CleanupSnapshot>("readonly", (snapshot, _tx, result) => result(describeCleanupSnapshot(snapshot)));

export function deleteUnusedLocalMedia(expected: CleanupFile[]): Promise<{ files: number; bytes: number }> {
    if (!expected.length || new Set(expected.map((file) => file.id)).size !== expected.length) return Promise.reject(new Error("请选择需要清理的原件"));
    return transaction("readwrite", (snapshot, tx, result) => {
        const current = describeCleanupSnapshot(snapshot);
        if (!current.canDelete) throw new Error(current.issues[0] || "仍有画布页面占用文件，请保存并离开后重新检查");
        for (const file of expected) {
            const entry = current.files.find((value) => value.id === file.id);
            if (!entry || !isManagedMediaKey(file.id) || entry.bytes !== file.bytes || entry.mimeType !== file.mimeType || entry.createdAt !== file.createdAt) throw new Error("原件清单已经变化，本次未删除任何文件，请重新检查");
            if (entry.references.length) throw new Error("原件仍被画布、回收站或任务引用，本次未删除任何文件");
        }
        for (const file of expected) tx.objectStore("local_media").delete(file.id);
        result({ files: expected.length, bytes: expected.reduce((total, file) => total + file.bytes, 0) });
    });
}

export const readCleanupOriginal = (id: string) => rawJournalTransaction<Blob>(["local_media"], "readonly", (tx, result, abort) => {
    const request = tx.objectStore("local_media").get(id);
    request.onsuccess = () => {
        if (!validManagedMedia(request.result) || request.result.id !== id) { abort(new Error("原件无法读取，未生成备份")); return; }
        result(request.result.blob);
    };
});
