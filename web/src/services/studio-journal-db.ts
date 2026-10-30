export const journalDatabaseName = "comfy-aggregator-studio:journal";
export const journalDatabaseVersion = 5;
const originalStores = ["intents", "uploads", "results", "budgets"];
const documentStores = [...originalStores, "documents"];
const currentStores = [...documentStores, "local_media", "editors"];

function validLayout(database: IDBDatabase, tx: IDBTransaction, names: string[]) {
    if (database.objectStoreNames.length !== names.length || names.some((name) => !database.objectStoreNames.contains(name) || tx.objectStore(name).keyPath !== "id")) return false;
    const intents = tx.objectStore("intents");
    return intents.indexNames.contains("active_node") && intents.index("active_node").keyPath === "activeNodeKey" && intents.index("active_node").unique;
}

function openDatabase(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
        if (!globalThis.indexedDB) { reject(new Error("当前浏览器无法保存任务日志，已阻止提交；请启用浏览器存储")); return; }
        const request = indexedDB.open(journalDatabaseName, journalDatabaseVersion);
        let abandoned = false;
        request.onupgradeneeded = (event) => {
            if (abandoned) { request.transaction!.abort(); return; }
            if (event.oldVersion === 0) {
                const intents = request.result.createObjectStore("intents", { keyPath: "id" });
                intents.createIndex("active_node", "activeNodeKey", { unique: true });
                for (const name of currentStores.filter((name) => name !== "intents")) request.result.createObjectStore(name, { keyPath: "id" });
            } else {
                const names = event.oldVersion === 1 ? originalStores : documentStores;
                if (![1, 2, 3, 4].includes(event.oldVersion) || !validLayout(request.result, request.transaction!, names)) { request.transaction!.abort(); return; }
                if (event.oldVersion < 2) request.result.createObjectStore("documents", { keyPath: "id" });
                for (const name of ["local_media", "editors"]) request.result.createObjectStore(name, { keyPath: "id" });
            }
        };
        request.onblocked = () => { abandoned = true; reject(new Error("任务日志升级被其他页面阻塞，请保存并关闭旧版本画布页面")); };
        request.onerror = () => reject(request.error || new Error("无法打开任务日志"));
        request.onsuccess = () => {
            const database = request.result;
            if (abandoned) { database.close(); return; }
            try {
                if (!validLayout(database, database.transaction(currentStores, "readonly"), currentStores)) throw new Error("本机日志结构不受支持，原记录已保留");
                database.onversionchange = () => database.close();
                resolve(database);
            } catch (error) { database.close(); reject(error); }
        };
    });
}

// Only maintenance and editor registration bypass the live-editor guard.
// All business access goes through journalTransaction in aggregator-journal.
export async function rawJournalTransaction<T>(names: string[], mode: IDBTransactionMode, run: (tx: IDBTransaction, setResult: (value: T) => void, abort: (error: unknown) => void) => void): Promise<T> {
    const database = await openDatabase();
    try {
        return await new Promise<T>((resolve, reject) => {
            const tx = database.transaction([...new Set(names)], mode, { durability: "strict" });
            let result: T, failure: unknown;
            tx.oncomplete = () => resolve(result);
            tx.onabort = () => reject(failure || tx.error || new Error("本机事务未提交，已阻止后续操作"));
            tx.onerror = () => { failure ||= tx.error; };
            const abort = (error: unknown) => {
                if (failure) return;
                failure = error;
                try { tx.abort(); } catch { reject(failure); }
            };
            try { run(tx, (value) => { result = value; }, abort); } catch (error) { abort(error); }
        });
    } finally { database.close(); }
}
