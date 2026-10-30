import { nanoid } from "nanoid";
import { journalGet, journalTransaction } from "./aggregator-journal";
import { isManagedMediaKey, validManagedMedia, type ManagedLocalMedia } from "./studio-local-media-types";

export const newManagedMediaKey = (kind: "image" | "media") => `${kind === "image" ? "image" : "file"}:managed-v1:${nanoid()}`;

export function addManagedMedia(id: string, blob: Blob): Promise<void> {
    if (!isManagedMediaKey(id)) throw new Error("新版原件必须使用独立文件键，未覆盖旧原件");
    const entry: ManagedLocalMedia = { version: 1, id, kind: id.startsWith("image:") ? "image" : "media", blob, createdAt: Date.now(), bytes: blob.size, mimeType: blob.type };
    return journalTransaction(["local_media"], "readwrite", (tx, result) => { tx.objectStore("local_media").add(entry); result(); });
}

export async function getManagedMediaBlob(id: string): Promise<Blob | null> {
    const entry = await journalGet("local_media", id);
    if (!entry) return null;
    if (!validManagedMedia(entry) || entry.id !== id) throw new Error("本机原件记录损坏，原记录已保留");
    return entry.blob;
}

let pending = 0;
export const localMediaWritePending = () => pending > 0;
export async function trackLocalMediaWrite<T>(run: () => Promise<T>): Promise<T> {
    pending++;
    try { return await run(); } finally { pending--; }
}
