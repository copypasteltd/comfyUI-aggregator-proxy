import type { CanvasExportFile } from "@/types/canvas-export";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType } from "@/types/canvas";

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const secrets = /^(api[_-]?key|authorization|password|secret|client[_-]?secret|token|access[_-]?token|refresh[_-]?token|csrf[_-]?token|webdav[_-]?password)$/i;

// A canvas snapshot contains local work, never reusable service credentials.
export function withoutCredentials<T>(value: T): T {
    if (Array.isArray(value)) return value.map(withoutCredentials) as T;
    if (record(value)) return Object.fromEntries(Object.entries(value).filter(([key]) => !secrets.test(key)).map(([key, item]) => [key, withoutCredentials(item)])) as T;
    if (typeof value === "string" && /^https?:\/\//i.test(value)) {
        try {
            const url = new URL(value);
            url.username = ""; url.password = "";
            for (const key of [...url.searchParams.keys()]) if (secrets.test(key) || /^(key|token)$/i.test(key)) url.searchParams.delete(key);
            return url.toString() as T;
        } catch { return value; }
    }
    return value;
}

export function validateCanvasArchive(value: unknown): asserts value is CanvasExportFile {
    if (!record(value) || value.app !== "infinite-canvas" || value.version !== 3 || !Array.isArray(value.projects)) throw new Error("不支持的画布备份结构或版本，未写入任何项目");
    if (value.studio !== undefined && (!record(value.studio) || value.studio.format_version !== 1 || value.studio.semantics !== "local-media-snapshot")) throw new Error("不支持的托管画布备份版本");
    for (const item of value.projects) {
        if (!record(item) || !record(item.project) || !Array.isArray(item.files) || !Array.isArray(item.project.nodes) || !Array.isArray(item.project.connections)) throw new Error("画布备份缺少项目、节点或文件清单");
        const ids = new Set<string>();
        for (const node of item.project.nodes) {
            if (!record(node) || typeof node.id !== "string" || ids.has(node.id) || typeof node.type !== "string" || !record(node.position) || ![node.position.x, node.position.y, node.width, node.height].every((number) => typeof number === "number" && Number.isFinite(number)) || Number(node.width) <= 0 || Number(node.height) <= 0) throw new Error("备份节点结构不完整或编号冲突");
            ids.add(node.id);
        }
        for (const edge of item.project.connections) if (!record(edge) || typeof edge.id !== "string" || !ids.has(String(edge.fromNodeId)) || !ids.has(String(edge.toNodeId))) throw new Error("备份含无效的画布连线");
        const files = new Set<string>();
        for (const file of item.files) {
            if (!record(file) || typeof file.storageKey !== "string" || !file.storageKey.includes(":") || files.has(file.storageKey) || typeof file.path !== "string" || file.path.split(/[\\/]/).some((part) => !part || part === ".." || part === ".") || typeof file.mimeType !== "string" || !Number.isSafeInteger(file.bytes) || Number(file.bytes) < 0 || (file.sha256 !== undefined && (typeof file.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(file.sha256))) || (value.studio && !file.sha256)) throw new Error("备份文件清单无效或缺少完整性摘要");
            files.add(file.storageKey);
        }
        const verifyReferences = (entry: unknown): void => {
            if (Array.isArray(entry)) { entry.forEach(verifyReferences); return; }
            if (!record(entry)) return;
            if (typeof entry.storageKey === "string" && entry.storageKey.includes(":") && !files.has(entry.storageKey)) throw new Error("备份引用了文件清单以外的原件，未写入任何项目");
            Object.values(entry).forEach(verifyReferences);
        };
        verifyReferences(item.project);
    }
}

export function importedCanvasSnapshot(project: CanvasProject, keys: Map<string, string>): CanvasProject {
    const clean = withoutCredentials(project);
    const rewrite = (value: unknown): unknown => {
        if (Array.isArray(value)) return value.map(rewrite);
        if (!record(value)) return typeof value === "string" && value.startsWith("blob:") ? "" : value;
        return Object.fromEntries(Object.entries(value).filter(([key]) => !key.startsWith("studio")).map(([key, item]) => [key, key === "storageKey" && typeof item === "string" ? keys.get(item) || item : rewrite(item)]));
    };
    const snapshot = rewrite(clean) as CanvasProject;
    snapshot.chatSessions = []; snapshot.activeChatId = null;
    snapshot.nodes = snapshot.nodes.map((node, index) => clean.nodes[index].type === "studio:task" ? {
        ...node, type: CanvasNodeType.Text, metadata: { status: "idle", content: `原任务 ${clean.nodes[index].metadata?.studioJobId || "提交结果待核对"}：这是离线画布快照，不会重新提交。请使用原服务和 Key 在任务页查询。` },
    } : node);
    return snapshot;
}
