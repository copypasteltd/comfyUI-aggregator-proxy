import { canvasStorageKeys } from "@/lib/canvas/canvas-transfer";
import { documentNames, documentReferences } from "./studio-documents";

type MediaStore = "image_files" | "media_files" | "image_previews" | "local_media";
export type LocalMediaRecord = { key: string; store: MediaStore; bytes: number | null; mediaType: string };
type ReferenceSource = { label: string; keys: string[] };
export type LocalMediaInventory = ReturnType<typeof buildLocalMediaInventory>;

export function buildLocalMediaInventory(records: LocalMediaRecord[], sources: ReferenceSource[], warnings: string[] = []) {
    const references = new Map<string, Set<string>>();
    for (const source of sources) for (const key of source.keys) {
        const labels = references.get(key) || new Set<string>(); labels.add(source.label); references.set(key, labels);
    }
    const originals = records.filter((item) => item.store !== "image_previews");
    const keys = new Set(originals.map((item) => item.key));
    return {
        capturedAt: new Date().toISOString(),
        files: originals.map((item) => ({ ...item, references: [...(references.get(item.key) || [])].sort() })),
        missing: [...references].filter(([key]) => /^(?:image|file|audio|video):/.test(key) && !keys.has(key)).map(([key, labels]) => ({ key, references: [...labels].sort() })),
        previewRecords: records.filter((item) => item.store === "image_previews").length,
        originalBytes: originals.reduce((total, item) => total + (item.bytes ?? 0), 0),
        warnings: [...new Set(warnings)],
    };
}

export async function readLocalMediaInventory(current: unknown): Promise<LocalMediaInventory> {
    const records: LocalMediaRecord[] = [], sources: ReferenceSource[] = [], warnings: string[] = [];
    const source = (label: string, value: unknown) => { sources.push({ label, keys: [...canvasStorageKeys(value)] }); };
    source("当前页面的画布与素材", current);
    const parse = (label: string, value: unknown) => {
        try { const parsed = typeof value === "string" ? JSON.parse(value) : value; if (!parsed || typeof parsed !== "object") throw new Error(); source(label, parsed); }
        catch { warnings.push(`${label}无法完整读取，未确认引用的文件也必须保留。`); }
    };
    await Promise.all([
        readStores("infinite-canvas", ["image_files", "media_files", "image_previews", "image_generation_logs", "video_generation_logs", "app_state"], (store, key, value) => {
            if (["image_files", "media_files", "image_previews"].includes(store)) {
                const blob = store === "image_previews" ? value?.blob : value;
                if (typeof key !== "string") { warnings.push("发现非文本文件键，未纳入普通原件清单；该记录保持原样。"); return; }
                const emptyPreview = store === "image_previews" && value?.version === 1 && value.blob === undefined;
                if (!(blob instanceof Blob) && !emptyPreview) warnings.push(`${store}含无法确认的记录，扫描不修改该记录。`);
                records.push({ key, store: store as MediaStore, bytes: blob instanceof Blob ? blob.size : emptyPreview ? 0 : null, mediaType: blob instanceof Blob ? blob.type : "" });
            } else if (store === "app_state") {
                if (documentNames.includes(key as typeof documentNames[number])) parse("迁移前保留文档", value);
            } else parse(store === "image_generation_logs" ? "图片历史记录" : "视频历史记录", value);
        }),
        readStores("comfy-aggregator-studio:journal", ["documents", "intents", "local_media"], (store, key, value) => {
            if (store === "local_media") {
                if (typeof key !== "string" || value?.version !== 1 || !(value.blob instanceof Blob)) { warnings.push("新版原件含无法核对的记录，文件保持原样。"); return; }
                records.push({ key, store: "local_media", bytes: value.blob.size, mediaType: value.blob.type }); return;
            }
            if (store === "intents") { if (value?.version !== 1) warnings.push("存在未知版本任务，引用检查不完整，文件保持原样。"); else source("本机任务记录", value); return; }
            const name = value?.id;
            if (!documentNames.includes(name)) { warnings.push("发现未知本机文档，引用检查不完整，文件保持原样。"); return; }
            try {
                if (value.version !== 1) throw new Error();
                documentReferences(name, value.value);
                parse(name === documentNames[0] ? "已保存画布及回收站" : "已保存素材", value.value);
                if (value.legacyValue !== null && value.legacyValue !== undefined) parse("迁移前保留文档", value.legacyValue);
            } catch { warnings.push("已保存文档无法完整核对，未确认引用的文件也必须保留。"); }
        }),
    ]);
    return buildLocalMediaInventory(records, sources, warnings);
}

function readStores(name: string, names: string[], visit: (store: string, key: IDBValidKey, value: any) => void): Promise<void> {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(name);
        let absent = false, abandoned = false;
        request.onupgradeneeded = () => { absent = true; request.transaction!.abort(); };
        request.onerror = () => absent ? resolve() : reject(request.error);
        request.onblocked = () => { abandoned = true; reject(new Error("本机引用扫描被其他页面阻塞，请稍后重新检查")); };
        request.onsuccess = () => {
            if (abandoned) { request.result.close(); return; }
            const database = request.result, available = names.filter((store) => database.objectStoreNames.contains(store));
            if (!available.length) { database.close(); resolve(); return; }
            const tx = database.transaction(available, "readonly");
            let failure: unknown;
            tx.oncomplete = () => { database.close(); resolve(); };
            tx.onabort = () => { database.close(); reject(failure || tx.error || new Error("本机引用扫描未完成，未执行任何清理")); };
            for (const store of available) {
                const cursor = tx.objectStore(store).openCursor();
                cursor.onsuccess = () => {
                    if (!cursor.result) return;
                    try { visit(store, cursor.result.key, cursor.result.value); cursor.result.continue(); }
                    catch (reason) { failure = reason; tx.abort(); }
                };
            }
        };
    });
}
