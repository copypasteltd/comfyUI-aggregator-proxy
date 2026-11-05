import type { AssetKind, CatalogModel, ModelUsage, RemoteJob } from "./types";

export const assetKindLabels: Record<AssetKind, string> = { image: "图片", video: "视频", audio: "音频", model: "GLB 模型" };
export const structuredInputFields = ["source_image", "mask", "references", "media", "texts", "context"];
export type ComposerSlot = { path: string; name?: string; label: string; description: string; required: boolean; kind: AssetKind; mimeTypes: string[]; maxBytes: number; maxImagePixels?: number; weightSupported: boolean };

export function composerSlots(usage: ModelUsage): ComposerSlot[] {
    return [
        ...usage.references.slots.map((slot) => {
            const index = /^input\.references\[(\d+)\]$/.exec(slot.path);
            return { path: slot.path, label: index ? `参考图 ${Number(index[1]) + 1}` : slot.path === "input.mask" ? "蒙版" : "源图片", description: slot.description, required: slot.required, kind: "image" as const, mimeTypes: usage.references.media_types, maxBytes: usage.references.max_upload_bytes, maxImagePixels: usage.references.max_image_pixels, weightSupported: slot.weight_supported };
        }),
        ...(usage.input_slots || []).map((slot) => ({ path: `input.media.${slot.name}`, name: slot.name, label: slot.label, description: slot.description, required: slot.required, kind: slot.kind, mimeTypes: slot.mime_types, maxBytes: slot.max_bytes, maxImagePixels: slot.kind === "image" ? usage.references.max_image_pixels : undefined, weightSupported: false })),
    ];
}

export function inputKind(mime: string): AssetKind | undefined {
    return mime.startsWith("image/") ? "image" : mime.startsWith("video/") ? "video" : mime.startsWith("audio/") ? "audio" : mime === "model/gltf-binary" ? "model" : undefined;
}

// File pickers may omit GLB/FLAC MIME types. This supplies the declared type for
// multipart only; the existing API independently verifies the complete bytes.
export function normalizeInputFile(file: File): File {
    const aliases: Record<string, string> = { "audio/x-wav": "audio/wav", "audio/x-flac": "audio/flac", "image/jpg": "image/jpeg" };
    const extensions: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", mp4: "video/mp4", webm: "video/webm", wav: "audio/wav", flac: "audio/flac", mp3: "audio/mpeg", ogg: "audio/ogg", glb: "model/gltf-binary" };
    const mime = aliases[file.type] || (!file.type || file.type === "application/octet-stream" ? extensions[file.name.split(".").pop()?.toLowerCase() || ""] : file.type);
    return mime && mime !== file.type ? new File([file], file.name, { type: mime, lastModified: file.lastModified }) : file;
}

export function slotAccepts(slot: ComposerSlot, file: Blob, kind = inputKind(file.type)): boolean {
    return slot.kind === kind && slot.mimeTypes.includes(file.type) && file.size > 0 && file.size <= slot.maxBytes;
}

export async function validateSlotOriginal(file: Blob, slot: ComposerSlot): Promise<void> {
    if (!slotAccepts(slot, file)) throw new Error(`${slot.label}的类型或文件大小不符合模型要求`);
    if (slot.kind === "image") {
        const bitmap = await createImageBitmap(file);
        try { if (!bitmap.width || !bitmap.height || slot.maxImagePixels && bitmap.width * bitmap.height > slot.maxImagePixels) throw new Error(`${slot.label}像素超过模型公布的上限`); }
        finally { bitmap.close(); }
    }
    if (slot.kind === "model") {
        const header = new DataView(await file.slice(0, 12).arrayBuffer());
        if (header.byteLength !== 12 || header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2 || header.getUint32(8, true) !== file.size) throw new Error("请选择完整的 GLB 2.0 文件");
    }
}

export function contextReason(model: CatalogModel, job: RemoteJob, now = Date.now()): string | undefined {
    if (!model.usage.context?.required) return "当前模型不需要父任务上下文";
    if (job.status !== "succeeded") return "父任务尚未成功完成";
    const context = job.context;
    if (!context?.available || !context.expires_at || context.expires_at * 1000 <= now) return "父任务没有可用的续拍上下文，或上下文已到期";
    if (context.family !== model.usage.context.family || context.compatibility !== model.usage.context.compatibility) return "父任务与当前续拍模型不兼容";
    return undefined;
}

export function modelInputSummary(usage: ModelUsage): string {
    const slots = composerSlots(usage).filter((slot) => slot.required);
    const counts = Object.entries(assetKindLabels).map(([kind, label]) => { const n = slots.filter((slot) => slot.kind === kind).length; return n ? `${n} ${label}` : ""; }).filter(Boolean);
    if (usage.text_slots?.some((slot) => slot.required)) counts.push("专用文本");
    if (usage.context?.required) counts.push("父任务");
    return counts.join(" · ") || "文字输入";
}
