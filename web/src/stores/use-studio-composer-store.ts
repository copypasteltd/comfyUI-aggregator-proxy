import { create } from "zustand";
import { nanoid } from "nanoid";
import type { CanvasNodeData, Position } from "@/types/canvas";
import type { TaskType } from "@/services/api/aggregator/types";

type Draft = { id: string; position: Position; sourceIds: string[]; prompt: string; preferredModel?: string; media?: string; taskType?: TaskType };
export const useStudioComposerStore = create<{
    draft?: Draft;
    open: (options: { position: Position; sources?: CanvasNodeData[]; prompt?: string; preferredModel?: string; media?: string; taskType?: TaskType }) => void;
    close: () => void;
}>(() => ({
    open: (options) => useStudioComposerStore.setState({ draft: { id: `studio-task-${nanoid()}`, position: options.position, sourceIds: (options.sources || []).map((node) => node.id), prompt: options.prompt || "", preferredModel: options.preferredModel, media: options.media, taskType: options.taskType } }),
    close: () => useStudioComposerStore.setState({ draft: undefined }),
}));
