export type ManagedLocalMedia = { version: 1; id: string; kind: "image" | "media"; blob: Blob; createdAt: number; bytes: number; mimeType: string };
export const isManagedMediaKey = (key: string) => /^(?:image|file):managed-v1:[A-Za-z0-9_-]+$/.test(key);
export function validManagedMedia(value: unknown): value is ManagedLocalMedia {
    const entry = value as ManagedLocalMedia | undefined;
    return !!entry && entry.version === 1 && typeof entry.id === "string" && isManagedMediaKey(entry.id) && entry.kind === (entry.id.startsWith("image:") ? "image" : "media") && entry.blob instanceof Blob && entry.bytes === entry.blob.size && entry.mimeType === entry.blob.type && Number.isSafeInteger(entry.createdAt) && entry.createdAt >= 0;
}
