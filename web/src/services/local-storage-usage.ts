export type IndexedDbStoreUsage = { name: string; records: number; bytes: number };
export type IndexedDbDatabaseUsage = { name: string; version: number; bytes: number; stores: IndexedDbStoreUsage[] };
export type LocalStorageUsage = { usage: number; quota: number | null; siteEstimateAvailable: boolean; contentBytes: number; databases: IndexedDbDatabaseUsage[] };

export async function readLocalStorageUsage(names = ["infinite-canvas"]): Promise<LocalStorageUsage> {
    const [estimate, databases] = await Promise.all([readStorageEstimate(), Promise.all(names.map(readDatabaseUsage))]);
    const contentBytes = databases.reduce((total, database) => total + database.bytes, 0);
    const siteEstimateAvailable = typeof estimate?.usage === "number" && Number.isFinite(estimate.usage) && estimate.usage >= 0;
    return { usage: siteEstimateAvailable ? estimate!.usage! : contentBytes, quota: estimate?.quota ?? null, siteEstimateAvailable, contentBytes, databases };
}

async function readStorageEstimate() {
    if (typeof navigator === "undefined" || !navigator.storage?.estimate) return null;
    try {
        return await navigator.storage.estimate();
    } catch {
        return null;
    }
}

function readDatabaseUsage(name: string) {
    return new Promise<IndexedDbDatabaseUsage>((resolve, reject) => {
        const request = indexedDB.open(name);
        let absent = false;
        // A read-only meter must not create a missing database or change its version.
        request.onupgradeneeded = () => { absent = true; request.transaction!.abort(); };
        request.onerror = () => absent ? resolve({ name, version: 0, bytes: 0, stores: [] }) : reject(request.error);
        request.onsuccess = () => {
            const database = request.result;
            const names = Array.from(database.objectStoreNames);
            if (!names.length) {
                database.close();
                resolve({ name, version: database.version, bytes: 0, stores: [] });
                return;
            }
            const transaction = database.transaction(names, "readonly");
            let stores: IndexedDbStoreUsage[] = [];
            transaction.oncomplete = () => {
                database.close();
                resolve({ name, version: database.version, bytes: stores.reduce((total, store) => total + store.bytes, 0), stores: stores.sort((a, b) => b.bytes - a.bytes) });
            };
            transaction.onabort = () => { database.close(); reject(transaction.error || new Error("本机占用统计未完成，请刷新重试")); };
            Promise.all(names.map((storeName) => readStoreUsage(transaction.objectStore(storeName))))
                .then((values) => { stores = values; })
                .catch((error) => { reject(error); database.close(); });
        };
    });
}

function readStoreUsage(store: IDBObjectStore) {
    return new Promise<IndexedDbStoreUsage>((resolve, reject) => {
        let records = 0;
        let bytes = 0;
        const request = store.openCursor();
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            const cursor = request.result;
            if (!cursor) {
                resolve({ name: store.name, records, bytes });
                return;
            }
            records += 1;
            bytes += storageValueBytes(cursor.value);
            cursor.continue();
        };
    });
}

export function storageValueBytes(value: unknown) {
    let binary = 0;
    const seen = new WeakSet<object>();
    const json = JSON.stringify(value, (_key, item) => {
        if (typeof item === "bigint") return item.toString();
        if (item && typeof item === "object") {
            if (seen.has(item)) return null;
            seen.add(item);
            if (item instanceof Blob || item instanceof ArrayBuffer || ArrayBuffer.isView(item)) {
                binary += item instanceof Blob ? item.size : item.byteLength;
                return null;
            }
            if (item instanceof Map || item instanceof Set) return Array.from(item);
        }
        return item;
    });
    return binary + new TextEncoder().encode(json ?? "").byteLength;
}
