import { journalTransaction, type IdentityRef, type Intent, type LocalDocument, type LocalResult } from "./aggregator-journal";
import { assertLocalResult } from "./studio-result-ownership";
import { canReadTask, validTaskAccess } from "./studio-task-access";
import { canvasStorageKeys } from "@/lib/canvas/canvas-transfer";

export const documentNames = ["infinite-canvas:canvas_store", "infinite-canvas:asset_store"] as const;
export type DocumentName = typeof documentNames[number];
export class DocumentReloadRequired extends Error {}

export function documentReferences(name: DocumentName, value: string): string[] {
    let parsed;
    try { parsed = JSON.parse(value); } catch { throw new Error("本机文档无法解析，原记录已保留"); }
    if (!parsed || typeof parsed !== "object" || parsed.version !== undefined && parsed.version !== 0 || !Array.isArray(parsed.state?.[name === documentNames[0] ? "projects" : "assets"])) throw new Error("本机文档结构或版本不受支持，原记录已保留");
    return [...canvasStorageKeys(parsed.state)].filter((key) => key.startsWith("studio:")).sort();
}

function readDocument(value: LocalDocument | undefined, name: DocumentName): LocalDocument {
    if (!value || value.id !== name || value.version !== 1 || !Number.isSafeInteger(value.revision) || value.revision < 1 || !Array.isArray(value.storageKeys)) throw new DocumentReloadRequired("本机文档或引用记录损坏，原记录已保留");
    const storageKeys = documentReferences(name, value.value);
    if (JSON.stringify(storageKeys) === JSON.stringify(value.storageKeys)) return value;
    // Recognize only the exact previous index; derive protection from the intact document.
    const legacy = new Set<string>();
    const visit = (entry: unknown) => {
        if (!entry || typeof entry !== "object") return;
        if ("storageKey" in entry && typeof entry.storageKey === "string" && entry.storageKey.startsWith("studio:")) legacy.add(entry.storageKey);
        Object.values(entry).forEach(visit);
    };
    visit(JSON.parse(value.value).state);
    if (JSON.stringify([...legacy].sort()) !== JSON.stringify(value.storageKeys)) throw new DocumentReloadRequired("本机文档或引用记录损坏，原记录已保留");
    return { ...value, storageKeys };
}

export function initializeDocuments(legacy: Record<DocumentName, string | null>): Promise<LocalDocument[]> {
    return journalTransaction(["documents", "results"], "readwrite", (tx, result, abort) => {
        const documents = tx.objectStore("documents");
        const values: LocalDocument[] = [];
        for (const name of documentNames) {
            const request = documents.get(name);
            request.onsuccess = () => {
                try {
                    const stored = request.result as LocalDocument | undefined;
                    let value: LocalDocument;
                    if (stored) {
                        value = readDocument(stored, name);
                        if (value !== stored) { value = { ...value, revision: stored.revision + 1 }; documents.put(value); }
                    } else {
                        const json = legacy[name] ?? JSON.stringify({ state: name === documentNames[0] ? { projects: [], deletedProjects: [] } : { assets: [] }, version: 0 });
                        value = { version: 1, id: name, revision: 1, value: json, storageKeys: documentReferences(name, json), legacyValue: legacy[name] };
                        documents.add(value);
                    }
                    values.push(value);
                    if (values.length === documentNames.length) result(values);
                } catch (error) { abort(error); }
            };
        }
    });
}

export function saveDocument(name: DocumentName, value: string, expectedRevision: number): Promise<LocalDocument> {
    const storageKeys = documentReferences(name, value);
    return journalTransaction(["documents", "results"], "readwrite", (tx, result, abort) => {
        const documents = tx.objectStore("documents");
        const request = documents.get(name);
        request.onsuccess = () => {
            try {
                const existing = readDocument(request.result as LocalDocument | undefined, name);
                if (existing.revision !== expectedRevision) throw new DocumentReloadRequired("另一页面已更新本机文档；请先导出当前画布，再重新载入，未覆盖其他页面的版本");
                const additions = storageKeys.filter((key) => !existing.storageKeys.includes(key));
                let remaining = additions.length;
                const commit = () => {
                    const next: LocalDocument = { ...existing, revision: existing.revision + 1, value, storageKeys };
                    documents.put(next); result(next);
                };
                if (!remaining) { commit(); return; }
                for (const key of additions) {
                    const item = tx.objectStore("results").getKey(key);
                    item.onsuccess = () => {
                        if (item.result === undefined) { abort(new Error("引用的本机原件已不存在，本次画布未保存；请重新载入原件或移除该引用")); return; }
                        if (--remaining === 0) commit();
                    };
                }
            } catch (error) { abort(error); }
        };
    });
}

export function deleteUnreferencedResult(id: string, identity: IdentityRef): Promise<void> {
    return journalTransaction(["documents", "results", "intents"], "readwrite", (tx, result, abort) => {
        const documents = tx.objectStore("documents").getAll();
        documents.onsuccess = () => {
            try {
                const values = documentNames.map((name) => {
                    return readDocument((documents.result as LocalDocument[]).find((item) => item.id === name), name);
                });
                if (values.some((value) => value.storageKeys.includes(id))) throw new Error("该原件仍被已保存的画布或素材引用，请先移除引用");
                const projects = new Set((JSON.parse(values[0].value).state.projects as { id: string }[]).map((project) => project.id));
                const file = tx.objectStore("results").get(id);
                file.onsuccess = () => {
                    try {
                        const entry = file.result as LocalResult | undefined;
                        if (!entry) { result(); return; }
                        assertLocalResult(entry, identity, entry.metadata.id);
                        const cursor = tx.objectStore("intents").openCursor();
                        cursor.onsuccess = () => {
                            if (!cursor.result) { tx.objectStore("results").delete(id); result(); return; }
                            const intent = cursor.result.value as Intent;
                            if (intent.version !== 1) { abort(new DocumentReloadRequired("存在不受支持的任务记录版本，已保留本机原件")); return; }
                            if (intent.readAccess !== undefined && (!Array.isArray(intent.readAccess) || !intent.readAccess.every((value) => validTaskAccess(value, intent)))) { abort(new Error("存在无法核对的任务访问关联，已保留本机原件")); return; }
                            if (intent.version === 1 && intent.canvas && !intent.canvas.detached && projects.has(intent.projectId) && canReadTask(intent, identity) && intent.job?.artifacts?.some((item) => item.id === entry.metadata.id)) {
                                abort(new Error("该原件仍由画布任务使用，请先在画布中移除该任务节点")); return;
                            }
                            cursor.result.continue();
                        };
                    } catch (error) { abort(error); }
                };
            } catch (error) { abort(error); }
        };
    });
}
