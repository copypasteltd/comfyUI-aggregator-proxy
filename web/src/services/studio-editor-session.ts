import { nanoid } from "nanoid";
import { rawJournalTransaction } from "./studio-journal-db";

export type EditorClaim = { version: 1; id: string; markerDatabase: string; startedAt: number };
const markerPrefix = "comfy-studio-editor:";
const claim: EditorClaim = { version: 1, id: `editor-v1:${nanoid()}`, markerDatabase: "", startedAt: Date.now() };
claim.markerDatabase = markerPrefix + claim.id;
let marker: IDBDatabase | undefined, registration: Promise<void> | undefined, closed = false;
const closedError = () => new Error("此编辑页面已离开或失去本机存储占用，请重新载入后继续");

export function validEditorClaim(value: unknown): value is EditorClaim {
    const entry = value as EditorClaim | undefined;
    return !!entry && entry.version === 1 && /^editor-v1:[A-Za-z0-9_-]+$/.test(entry.id) && entry.markerDatabase === markerPrefix + entry.id && Number.isSafeInteger(entry.startedAt) && entry.startedAt >= 0;
}

function closeEditor() { closed = true; marker?.close(); marker = undefined; }
if (typeof window !== "undefined") window.addEventListener("pagehide", (event) => { if (!event.persisted) closeEditor(); });
if (typeof window !== "undefined") window.addEventListener("pageshow", (event) => { if (event.persisted && closed) window.location.reload(); });

export async function leaveStudioEditor() {
    await ensureStudioEditor();
    // Drain previously opened business transactions before releasing the marker.
    await rawJournalTransaction<void>(["editors"], "readwrite", (_tx, result) => result());
    closeEditor();
}

async function registerEditor() {
    marker = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open(claim.markerDatabase, 1);
        request.onupgradeneeded = (event) => {
            if (closed || event.oldVersion !== 0) { request.transaction!.abort(); return; }
            request.result.createObjectStore("owner", { keyPath: "id" }).add(claim);
        };
        request.onerror = () => reject(request.error || new Error("无法建立本页的文件保护"));
        request.onblocked = () => reject(new Error("本页的文件保护被其他操作阻塞，请重新载入"));
        request.onsuccess = () => {
            if (closed) { request.result.close(); reject(closedError()); return; }
            // A live page deliberately holds v1. A v2 maintenance probe must wait
            // for the browser to release this connection; age is never evidence.
            request.result.onversionchange = () => undefined;
            request.result.onclose = closeEditor;
            resolve(request.result);
        };
    });
    await rawJournalTransaction<void>(["editors"], "readwrite", (tx, result, abort) => {
        if (closed) { abort(closedError()); return; }
        tx.objectStore("editors").add(claim); result();
    });
}

export async function ensureStudioEditor() {
    if (closed) throw closedError();
    registration ||= registerEditor().catch((error) => { closeEditor(); throw error; });
    await registration;
    if (closed) throw closedError();
    return claim;
}

export async function editorTransaction<T>(names: string[], mode: IDBTransactionMode, run: (tx: IDBTransaction, setResult: (value: T) => void, abort: (error: unknown) => void) => void): Promise<T> {
    const own = await ensureStudioEditor();
    return rawJournalTransaction<T>([...names, "editors"], mode, (tx, result, abort) => {
        const request = tx.objectStore("editors").get(own.id);
        request.onsuccess = () => {
            if (closed || JSON.stringify(request.result) !== JSON.stringify(own)) { abort(closedError()); return; }
            try { run(tx, result, abort); } catch (error) { abort(error); }
        };
    });
}

export type EditorProbe = { id: string; state: "checking" | "occupied" | "released" | "uncertain"; message: string };
const probes = new Map<string, EditorProbe>();
const listeners = new Set<() => void>();
const publish = (value: EditorProbe) => { probes.set(value.id, value); listeners.forEach((listener) => listener()); };
export const editorProbes = () => [...probes.values()];
export const subscribeEditorProbes = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

// A probe can remain queued behind another browser request. It never blocks the
// maintenance UI, and has no expiry, heartbeat, forced release or timed retry.
export function probeStudioEditor(value: unknown) {
    if (!validEditorClaim(value)) return;
    if (probes.has(value.id)) return;
    publish({ id: value.id, state: "checking", message: "正在等待浏览器核对页面占用" });
    const request = indexedDB.open(value.markerDatabase, 2);
    let failure = "";
    request.onupgradeneeded = (event) => {
        if (event.oldVersion !== 1 || request.result.objectStoreNames.length !== 1 || !request.result.objectStoreNames.contains("owner") || request.transaction!.objectStore("owner").keyPath !== "id") {
            failure = "页面占用标记缺失或结构未知，文件继续保留"; request.transaction!.abort();
        }
    };
    request.onblocked = () => publish({ id: value.id, state: "occupied", message: "页面仍在占用；请在该页面保存并离开" });
    request.onerror = () => publish({ id: value.id, state: "uncertain", message: failure || "无法确认页面已经离开，文件继续保留" });
    request.onsuccess = () => {
        const database = request.result;
        database.onversionchange = () => database.close();
        const verify = new Promise<void>((resolve, reject) => {
            try {
                if (database.version !== 2 || database.objectStoreNames.length !== 1 || !database.objectStoreNames.contains("owner")) throw new Error();
                const tx = database.transaction("owner", "readonly"), owners = tx.objectStore("owner").getAll();
                owners.onsuccess = () => { if (owners.result.length !== 1 || JSON.stringify(owners.result[0]) !== JSON.stringify(value)) tx.abort(); };
                tx.oncomplete = () => resolve(); tx.onabort = () => reject(new Error());
            } catch (error) { reject(error); }
        });
        void verify.then(() => rawJournalTransaction<void>(["editors"], "readwrite", (tx, result, abort) => {
            const row = tx.objectStore("editors").get(value.id);
            row.onsuccess = () => {
                if (row.result !== undefined && JSON.stringify(row.result) !== JSON.stringify(value)) { abort(new Error("页面占用记录发生变化，继续保留文件")); return; }
                tx.objectStore("editors").delete(value.id); result();
            };
        })).then(() => {
            publish({ id: value.id, state: "released", message: "浏览器已确认该页面连接释放" });
        }).catch(() => publish({ id: value.id, state: "uncertain", message: "页面占用标记无法完整核对，文件继续保留" })).finally(() => database.close());
    };
}
