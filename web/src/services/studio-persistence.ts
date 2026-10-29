import localforage from "localforage";
import { MANAGED_STUDIO } from "@/constant/studio";
import { DocumentReloadRequired, documentNames, initializeDocuments, saveDocument, type DocumentName } from "./studio-documents";
import type { LocalDocument } from "./aggregator-journal";

const legacy = localforage.createInstance({ name: "infinite-canvas", storeName: "app_state" });
const documents = new Map<DocumentName, LocalDocument>();
const loaded = new Set<DocumentName>();
const pending = new Map<DocumentName, string>();
const listeners = new Set<() => void>();
type SaveState = { saving: boolean; error: string; canRetry: boolean };
let state: SaveState = { saving: false, error: "", canRetry: false };
let initialized: Promise<void> | undefined;
let writing: Promise<void> | undefined;
const publish = (next: SaveState) => { state = next; listeners.forEach((listener) => listener()); };
export const getStudioSaveState = () => state;
export const subscribeStudioSaveState = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const isStudioDocument = (name: string): name is DocumentName => MANAGED_STUDIO && documentNames.includes(name as DocumentName);

function failed(reason: unknown, canRetry: boolean) {
    const error = reason instanceof DOMException && reason.name === "QuotaExceededError" ? "本机存储空间不足，画布尚未保存。请先导出重要作品，再释放空间并重试。" : reason instanceof Error ? reason.message : "本机保存失败，当前编辑尚未保存，请先导出画布";
    publish({ saving: false, error, canRetry: canRetry && !(reason instanceof DocumentReloadRequired) });
}

async function initialize() {
    if (!initialized) initialized = (async () => {
        const values = {} as Record<DocumentName, string | null>;
        for (const name of documentNames) {
            const primary = await legacy.getItem<unknown>(name);
            const fallback = window.localStorage.getItem(name);
            if (primary !== null && typeof primary !== "string" || primary !== null && fallback !== null && primary !== fallback) throw new DocumentReloadRequired("发现不同的旧画布副本，已保留全部原记录；请先导出旧页面内容，再处理副本冲突");
            values[name] = (primary as string | null) ?? fallback;
        }
        for (const value of await initializeDocuments(values)) {
            if (value.legacyValue !== values[value.id as DocumentName]) throw new DocumentReloadRequired("旧版页面修改了迁移前的副本，已保留两份记录；请先导出旧页面内容，再处理副本冲突");
            documents.set(value.id as DocumentName, value);
        }
    })().catch((reason) => { failed(reason, false); throw reason; });
    await initialized;
}

// Zustand does not await persistence writes. Keep their failure visible and retained;
// actions that require durability explicitly await flushStudioDocuments instead.
function drain() {
    if (writing || state.error || !pending.size) return;
    publish({ saving: true, error: "", canRetry: false });
    writing = (async () => {
        while (pending.size) {
            const [name, value] = pending.entries().next().value!;
            const existing = documents.get(name)!;
            const saved = existing.value === value ? existing : await saveDocument(name, value, existing.revision);
            documents.set(name, saved);
            if (pending.get(name) === value) pending.delete(name);
        }
        publish({ saving: false, error: "", canRetry: false });
    })().catch((reason) => { failed(reason, true); }).finally(() => { writing = undefined; drain(); });
}

export async function readStudioDocument(name: DocumentName) {
    await initialize();
    loaded.add(name);
    return documents.get(name)!.value;
}

export function studioDocumentReadFailed(name: DocumentName, reason: unknown) {
    if (!MANAGED_STUDIO) return;
    loaded.delete(name);
    failed(reason, false);
}

export function writeStudioDocument(name: DocumentName, value: string) {
    if (!loaded.has(name)) {
        if (!state.error) failed(new DocumentReloadRequired("本机文档尚未成功读取，已阻止覆盖；请重新载入页面"), false);
        return;
    }
    pending.set(name, value);
    drain();
}

export async function flushStudioDocuments() {
    if (!MANAGED_STUDIO) return;
    await initialize();
    drain();
    while (writing) await writing;
    if (state.error) throw new Error(state.error);
}

export async function retryStudioSave() {
    if (!state.canRetry) return;
    publish({ saving: false, error: "", canRetry: false });
    await flushStudioDocuments();
}

export function clearStudioDocument(name: DocumentName) {
    writeStudioDocument(name, JSON.stringify({ state: name === documentNames[0] ? { projects: [], deletedProjects: [] } : { assets: [] }, version: 0 }));
}
