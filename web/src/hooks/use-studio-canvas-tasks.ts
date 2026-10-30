import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { localTasks, subscribeStudioTasks } from "@/services/aggregator-engine";
import { journalGet } from "@/services/aggregator-journal";
import { assertLocalResult } from "@/services/studio-result-ownership";
import { releaseStudioMedia, studioMediaUrl } from "@/services/studio-media";
import { useStudioStore } from "@/stores/use-studio-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData, type CanvasNodeStatus } from "@/types/canvas";
import { fitNodeSize } from "@/lib/canvas/canvas-node-size";

export function useStudioCanvasTasks(projectId: string, ready: boolean, nodes: CanvasNodeData[], setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>, setConnections: Dispatch<SetStateAction<CanvasConnection[]>>) {
    const client = useStudioStore((state) => state.client);
    const nodeIds = useRef(new Set<string>());
    nodeIds.current = new Set(nodes.map((node) => node.id));
    useEffect(() => {
        if (!ready || !client) return;
        let active = true;
        let running = false;
        let requested = false;
        const sync = async () => {
            if (running) { requested = true; return; }
            running = true;
            try {
                const intents = (await localTasks()).filter((intent) => intent.projectId === projectId && intent.canvas && !intent.canvas.detached);
                const updates = await Promise.all(intents.map(async (intent) => {
                    const artifact = intent.job?.artifacts?.[0];
                    const saved = artifact ? await journalGet("results", `studio:${client.fingerprint}:${artifact.id}`) : undefined;
                    if (saved) assertLocalResult(saved, { ...intent.identity, origin: client.origin, fingerprint: client.fingerprint }, artifact!.id, intent.job?.id);
                    const type = saved ? saved.metadata.media_type.startsWith("image/") ? CanvasNodeType.Image : saved.metadata.media_type.startsWith("video/") ? CanvasNodeType.Video : saved.metadata.media_type.startsWith("audio/") ? CanvasNodeType.Audio : "studio:model3d" : "studio:task";
                    const status: CanvasNodeStatus = saved ? "success" : intent.job && ["failed", "cancelled", "expired"].includes(intent.job.status) ? "error" : intent.job?.status === "succeeded" ? "success" : "loading";
                    const dimensions = saved?.metadata.metadata;
                    const width = typeof dimensions?.width === "number" ? dimensions.width : undefined;
                    const height = typeof dimensions?.height === "number" ? dimensions.height : undefined;
                    const metadata = { ...intent.canvas!.node.metadata, studioIntentId: intent.id, studioJobId: intent.job?.id, studioStatus: intent.job?.status || intent.stage, status, errorDetails: intent.error,
                        ...(saved ? { content: await studioMediaUrl(saved.id), storageKey: saved.id, studioContentId: saved.metadata.id, studioExpiresAt: saved.metadata.expires_at, bytes: saved.metadata.bytes, mimeType: saved.metadata.media_type, naturalWidth: width, naturalHeight: height, durationMs: typeof dimensions?.duration_milliseconds === "number" ? dimensions.duration_milliseconds : undefined } : {}) };
                    return { intent, type, metadata, size: width && height ? fitNodeSize(width, height) : undefined };
                }));
                if (!active || useStudioStore.getState().client !== client) return;
                setNodes((nodes) => {
                    const next = [...nodes]; let changed = false;
                    for (const update of updates) {
                        const index = next.findIndex((node) => node.id === update.intent.nodeId);
                        const existing = next[index];
                        // The journal can reconstruct a node lost before the canvas autosave committed.
                        const node = { ...(existing || update.intent.canvas!.node), type: update.type, metadata: { ...existing?.metadata, ...update.metadata }, ...(!existing || existing.type !== update.type ? update.size : {}) };
                        if (JSON.stringify(existing) === JSON.stringify(node)) continue;
                        if (index >= 0) next[index] = node; else next.push(node);
                        changed = true;
                    }
                    return changed ? next : nodes;
                });
                setConnections((connections) => {
                    const next = [...connections];
                    for (const { intent } of updates) for (const source of intent.canvas!.sourceIds) if (nodeIds.current.has(source) && !next.some((item) => item.fromNodeId === source && item.toNodeId === intent.nodeId)) next.push({ id: `studio:${intent.id}:${source}`, fromNodeId: source, toNodeId: intent.nodeId });
                    return next.length === connections.length ? connections : next;
                });
            } finally { running = false; if (active && requested) { requested = false; void sync().catch(() => {}); } }
        };
        void sync().catch(() => {}); const off = subscribeStudioTasks(() => void sync().catch(() => {}));
        return () => { active = false; off(); releaseStudioMedia(); };
    }, [projectId, ready, client, setNodes, setConnections]);
}
