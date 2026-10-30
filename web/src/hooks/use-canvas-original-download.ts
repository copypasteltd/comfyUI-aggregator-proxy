import { useCallback } from "react";
import { App } from "antd";
import { saveAs } from "file-saver";
import { MANAGED_STUDIO } from "@/constant/studio";
import { getImageBlob } from "@/services/image-storage";
import { getMediaBlob } from "@/services/file-storage";
import { getStudioResult, mediaExtension } from "@/services/studio-media";
import type { CanvasNodeMetadata } from "@/types/canvas";

type Original = Pick<CanvasNodeMetadata, "storageKey" | "content" | "mimeType">;

export function useCanvasOriginalDownload() {
    const { message } = App.useApp();
    return useCallback(async (metadata: Original | undefined, filename: string) => {
        try {
            let blob: Blob | null;
            if (metadata?.storageKey?.startsWith("studio:")) {
                const result = await getStudioResult(metadata.storageKey);
                if (!result) throw new Error("请使用原连接读取本机素材，或从完整备份恢复");
                blob = result.blob; filename = result.metadata.id;
            } else if (metadata?.storageKey) {
                blob = await (metadata.storageKey.startsWith("image:") ? getImageBlob(metadata.storageKey) : getMediaBlob(metadata.storageKey));
            } else {
                if (!metadata?.content || MANAGED_STUDIO && !/^(blob:|data:)/i.test(metadata.content)) throw new Error("请先将素材保存到本机，再下载原件");
                const response = await fetch(metadata.content, { credentials: "omit" });
                if (!response.ok) throw new Error("原件读取失败，请重新载入素材");
                blob = await response.blob();
            }
            if (!(blob instanceof Blob)) throw new Error("本机原件不可读，请重新载入原件或从完整备份恢复");
            saveAs(blob, `${filename}.${mediaExtension(blob.type || metadata?.mimeType || "")}`);
        } catch (error) {
            message.error(error instanceof Error ? error.message : "原件下载未完成");
        }
    }, [message]);
}
