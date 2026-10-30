import { journalTransaction, type IdentityRef, type InputUpload, type Intent } from "./aggregator-journal";

export type CachedUpload = { id: string; name: string; bytes: number; createdAt: number; blockedReason: string | null; snapshot: string };
const owns = (value: { identity?: IdentityRef }, identity: IdentityRef) => value.identity?.origin === identity.origin && value.identity?.fingerprint === identity.fingerprint;
const snapshot = (value: InputUpload) => JSON.stringify([value.version, value.id, value.identity, value.sha256, value.idempotencyKey, value.name, value.createdAt, value.blob.size, value.blob.type, value.asset, value.error]);

export function uploadCacheBlocker(upload: InputUpload, intents: Intent[]): string | null {
    if (upload.version !== 1 || !(upload.blob instanceof Blob) || !/^[a-f0-9]{64}$/.test(upload.sha256)) return "缓存记录无法核对，已保留";
    if (!upload.asset) return "上传未确认或结果未知，请保留原参考图";
    if (upload.asset.status !== "ready" || !upload.asset.id || upload.asset.sha256 !== upload.sha256 || upload.asset.bytes !== upload.blob.size) return "上传回执无法核对，已保留";
    for (const intent of intents) {
        if (intent.version !== 1 || !intent.identity?.origin || !intent.identity?.fingerprint) return "存在无法识别的任务记录，已保留缓存";
        if (!owns(intent, upload.identity)) continue;
        let request;
        try { request = JSON.parse(intent.body); } catch { return "原任务正文无法核对，已保留缓存"; }
        if (!request || typeof request !== "object" || !request.input || typeof request.input !== "object") return "原任务正文无法核对，已保留缓存";
        const references = (value: unknown): boolean => Boolean(value && typeof value === "object" && ("asset_id" in value && value.asset_id === upload.asset!.id || Object.values(value).some(references)));
        if (references(request)) return "原任务仍引用此参考图，已保留缓存";
    }
    return null;
}

export function readUploadCache(identity: IdentityRef): Promise<CachedUpload[]> {
    return journalTransaction(["uploads", "intents"], "readonly", (tx, result, abort) => {
        const uploads = tx.objectStore("uploads").getAll();
        const intents = tx.objectStore("intents").getAll();
        intents.onsuccess = () => {
            try {
                result((uploads.result as InputUpload[]).filter((value) => owns(value, identity)).map((value) => ({ id: value.id, name: value.name, bytes: value.blob?.size || 0, createdAt: value.createdAt, blockedReason: uploadCacheBlocker(value, intents.result), snapshot: value.blob instanceof Blob ? snapshot(value) : "" })));
            } catch (error) { abort(error); }
        };
    });
}

export function deleteCachedUpload(selected: CachedUpload, identity: IdentityRef, isCurrent: () => boolean): Promise<void> {
    return journalTransaction(["uploads", "intents"], "readwrite", (tx, result, abort) => {
        const upload = tx.objectStore("uploads").get(selected.id);
        const intents = tx.objectStore("intents").getAll();
        intents.onsuccess = () => {
            try {
                if (!isCurrent()) throw new Error("连接已切换，缓存未清理");
                const value = upload.result as InputUpload | undefined;
                if (!value) { result(); return; }
                if (!owns(value, identity)) throw new Error("请使用原连接管理参考缓存");
                if (!(value.blob instanceof Blob) || snapshot(value) !== selected.snapshot) throw new Error("缓存已变化，请刷新后重新确认");
                const reason = uploadCacheBlocker(value, intents.result);
                if (reason) throw new Error(reason);
                tx.objectStore("uploads").delete(selected.id);
                result();
            } catch (error) { abort(error); }
        };
    });
}
