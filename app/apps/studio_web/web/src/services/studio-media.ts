import { journalGet } from "./aggregator-journal";
import { useStudioStore } from "@/stores/use-studio-store";
import { assertLocalResult } from "./studio-result-ownership";

const urls = new Map<string, string>();
export const isStudioKey = (key?: string): key is string => Boolean(key?.startsWith("studio:"));

export async function getStudioResult(key: string) {
    const result = await journalGet("results", key);
    const client = useStudioStore.getState().client;
    if (!result || result.version !== 1 || !client || client.fingerprint !== result.identity.fingerprint || client.origin !== result.identity.origin) return undefined;
    assertLocalResult(result, result.identity, result.metadata.id);
    return result;
}

export async function studioMediaUrl(key: string): Promise<string> {
    const result = await getStudioResult(key);
    if (!result) return "";
    const cached = urls.get(key);
    if (cached) return cached;
    const url = URL.createObjectURL(result.blob);
    urls.set(key, url);
    return url;
}

export function releaseStudioMedia(key?: string) {
    for (const [id, url] of urls) if (!key || id === key) { URL.revokeObjectURL(url); urls.delete(id); }
}

useStudioStore.subscribe((state, previous) => {
    if (state.client?.fingerprint !== previous.client?.fingerprint) releaseStudioMedia();
});
globalThis.addEventListener?.("pagehide", () => releaseStudioMedia());

export const mediaExtension = (mime: string) => ({ "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4", "video/webm": "webm", "audio/wav": "wav", "audio/flac": "flac", "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/opus": "opus", "model/gltf-binary": "glb" }[mime] || "bin");
