import { create } from "zustand";
import { nanoid } from "nanoid";
import type { CanvasNodeData, Position } from "@/types/canvas";

type Draft = { id: string; position: Position; sourceIds: string[]; prompt: string; preferredModel?: string; media?: string };
export const useStudioComposerStore = create<{
    draft?: Draft;
    open: (options: { position: Position; sources?: CanvasNodeData[]; prompt?: string; preferredModel?: string; media?: string }) => void;
    close: () => void;
}>(() => ({
    open: (options) => useStudioComposerStore.setState({ draft: { id: `studio-task-${nanoid()}`, position: options.position, sourceIds: (options.sources || []).map((node) => node.id), prompt: options.prompt || "", preferredModel: options.preferredModel, media: options.media } }),
    close: () => useStudioComposerStore.setState({ draft: undefined }),
}));
