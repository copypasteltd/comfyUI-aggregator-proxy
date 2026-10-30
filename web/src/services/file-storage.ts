import { MANAGED_STUDIO } from "@/constant/studio";
import { ensureStudioEditor } from "./studio-editor-session";
import { isManagedMediaKey } from "./studio-local-media-types";
import { addManagedMedia, getManagedMediaBlob, newManagedMediaKey, trackLocalMediaWrite } from "./studio-local-media";
import localforage from "localforage";
import { nanoid } from "nanoid";

import { withLocalProxy } from "@/stores/use-config-store";
import { getStudioResult, isStudioKey, studioMediaUrl } from "./studio-media";

export type UploadedFile = { url: string; storageKey: string; bytes: number; mimeType: string; width?: number; height?: number; durationMs?: number };

const store = localforage.createInstance({ name: "infinite-canvas", storeName: "media_files" });
const objectUrls = new Map<string, string>();

export function uploadMediaFile(input: string | Blob, prefix = "file"): Promise<UploadedFile> {
    return trackLocalMediaWrite(() => storeUploadedMedia(input, prefix));
}

async function storeUploadedMedia(input: string | Blob, prefix: string): Promise<UploadedFile> {
    if (MANAGED_STUDIO) await ensureStudioEditor();
    const blob = typeof input === "string" ? await (await fetch(withLocalProxy(input))).blob() : input;
    const storageKey = MANAGED_STUDIO ? newManagedMediaKey("media") : `${prefix}:${nanoid()}`;
    await (isManagedMediaKey(storageKey) ? addManagedMedia(storageKey, blob) : store.setItem(storageKey, blob));
    const url = URL.createObjectURL(blob);
    objectUrls.set(storageKey, url);
    const meta = blob.type.startsWith("video/") ? await readVideoMeta(url) : blob.type.startsWith("audio/") ? await readAudioMeta(url) : {};
    return { url, storageKey, bytes: blob.size, mimeType: blob.type || "application/octet-stream", ...meta };
}

export async function resolveMediaUrl(storageKey?: string, fallback = "") {
    if (!storageKey) return fallback;
    if (MANAGED_STUDIO) await ensureStudioEditor();
    if (isStudioKey(storageKey)) return studioMediaUrl(storageKey);
    const cached = objectUrls.get(storageKey);
    if (cached) return cached;
    const blob = await (isManagedMediaKey(storageKey) ? getManagedMediaBlob(storageKey) : store.getItem<Blob>(storageKey));
    if (!blob) return fallback;
    const url = URL.createObjectURL(blob);
    objectUrls.set(storageKey, url);
    return url;
}

export async function getMediaBlob(storageKey: string) {
    if (MANAGED_STUDIO) await ensureStudioEditor();
    if (isStudioKey(storageKey)) return (await getStudioResult(storageKey))?.blob || null;
    return isManagedMediaKey(storageKey) ? getManagedMediaBlob(storageKey) : store.getItem<Blob>(storageKey);
}

export function setMediaBlob(storageKey: string, blob: Blob) {
    return trackLocalMediaWrite(() => storeImportedMedia(storageKey, blob));
}

async function storeImportedMedia(storageKey: string, blob: Blob) {
    if (MANAGED_STUDIO && !isManagedMediaKey(storageKey)) throw new Error("导入必须使用新版独立原件键，未覆盖已有文件");
    await (isManagedMediaKey(storageKey) ? addManagedMedia(storageKey, blob) : store.setItem(storageKey, blob));
    const url = URL.createObjectURL(blob);
    objectUrls.set(storageKey, url);
    return url;
}

export async function deleteStoredMedia(keys: Iterable<string>) {
    if (MANAGED_STUDIO) throw new Error("请保存并进入本机安全清理页面，原件未删除");
    await Promise.all(
        Array.from(new Set(keys)).map(async (key) => {
            const url = objectUrls.get(key);
            if (url) URL.revokeObjectURL(url);
            objectUrls.delete(key);
            await store.removeItem(key);
        }),
    );
}

export async function cleanupUnusedMedia(usedData: unknown) {
    if (MANAGED_STUDIO) return;
    const usedKeys = collectMediaStorageKeys(usedData);
    const unused: string[] = [];
    await store.iterate((_value, key) => {
        if (!usedKeys.has(key)) unused.push(key);
    });
    await Promise.all(unused.map((key) => store.removeItem(key)));
}

export function collectMediaStorageKeys(value: unknown, keys = new Set<string>()) {
    if (!value || typeof value !== "object") return keys;
    if ("storageKey" in value && typeof value.storageKey === "string" && value.storageKey.includes(":")) keys.add(value.storageKey);
    Object.values(value).forEach((item) => (Array.isArray(item) ? item.forEach((child) => collectMediaStorageKeys(child, keys)) : collectMediaStorageKeys(item, keys)));
    return keys;
}

function readVideoMeta(url: string) {
    return new Promise<{ width: number; height: number; durationMs?: number }>((resolve) => {
        const video = document.createElement("video");
        const done = () => resolve({ width: video.videoWidth || 1280, height: video.videoHeight || 720, durationMs: Number.isFinite(video.duration) ? Math.round(video.duration * 1000) : undefined });
        video.onloadedmetadata = done;
        video.onerror = done;
        video.src = url;
    });
}

function readAudioMeta(url: string) {
    return new Promise<{ durationMs?: number }>((resolve) => {
        const audio = document.createElement("audio");
        const done = () => resolve({ durationMs: Number.isFinite(audio.duration) ? Math.round(audio.duration * 1000) : undefined });
        audio.onloadedmetadata = done;
        audio.onerror = done;
        audio.src = url;
    });
}
