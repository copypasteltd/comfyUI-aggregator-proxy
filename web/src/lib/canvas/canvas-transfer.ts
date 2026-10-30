import type { CanvasExportFile } from "@/types/canvas-export";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType } from "@/types/canvas";

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const secrets = /^(?:api[_-]?keys?|x[_-]?api[_-]?key|authorization|proxy[_-]?authorization|password|secret|client[_-]?secret|token|bearer[_-]?token|access[_-]?token|refresh[_-]?token|csrf[_-]?token|session[_-]?(?:id|token)|cookies?|set[_-]?cookie|webdav[_-]?password)$/i;
const prototypeKey = (key: string) => ["__proto__", "constructor", "prototype"].includes(key);

// A canvas snapshot contains local work, never reusable service credentials.
export function withoutCredentials<T>(value: T): T {
    if (Array.isArray(value)) return value.map(withoutCredentials) as T;
    if (record(value)) return Object.fromEntries(Object.entries(value).filter(([key]) => !secrets.test(key) && !prototypeKey(key)).map(([key, item]) => [key, withoutCredentials(item)])) as T;
    if (typeof value === "string" && /^https?:\/\//i.test(value)) {
        try {
            const url = new URL(value);
            url.username = ""; url.password = "";
            for (const key of [...url.searchParams.keys()]) if (secrets.test(key) || /^(key|token)$/i.test(key)) url.searchParams.delete(key);
            const fragment = url.hash.slice(1), separator = fragment.indexOf("?");
            const prefix = separator >= 0 ? fragment.slice(0, separator + 1) : "";
            const parameters = new URLSearchParams(separator >= 0 ? fragment.slice(separator + 1) : fragment);
            let changed = false;
            for (const key of [...parameters.keys()]) if (secrets.test(key) || /^key$/i.test(key)) { parameters.delete(key); changed = true; }
            if (changed) url.hash = parameters.size ? prefix + parameters.toString() : prefix.replace(/\?$/, "");
            return url.toString() as T;
        } catch { return value; }
    }
    return value;
}

export function validateCanvasArchive(value: unknown): asserts value is CanvasExportFile {
    if (!record(value) || value.app !== "infinite-canvas" || value.version !== 3 || !Array.isArray(value.projects)) throw new Error("不支持的画布备份结构或版本，未写入任何项目");
    const entries: unknown[] = [value];
    while (entries.length) {
        const entry = entries.pop();
        if (!entry || typeof entry !== "object") continue;
        if (Object.keys(entry).some(prototypeKey)) throw new Error("备份含不支持的原型字段，未写入任何项目");
        for (const child of Object.values(entry)) entries.push(child);
    }
    if (value.studio !== undefined && (!record(value.studio) || value.studio.format_version !== 1 || value.studio.semantics !== "local-media-snapshot")) throw new Error("不支持的托管画布备份版本");
    const projectIds = new Set<string>();
    const filePaths = new Set<string>();
    for (const item of value.projects) {
        if (!record(item) || !record(item.project) || !Array.isArray(item.files) || !Array.isArray(item.project.nodes) || !Array.isArray(item.project.connections)) throw new Error("画布备份缺少项目、节点或文件清单");
        const project = item.project;
        if (typeof project.id !== "string" || !project.id || projectIds.has(project.id) || typeof project.title !== "string" || [project.createdAt, project.updatedAt].some((date) => date !== undefined && typeof date !== "string") || project.chatSessions !== undefined && !Array.isArray(project.chatSessions) || project.showImageInfo !== undefined && typeof project.showImageInfo !== "boolean" || project.backgroundMode !== undefined && !["dots", "lines", "blank"].includes(String(project.backgroundMode))) throw new Error("备份项目结构不完整或编号冲突");
        projectIds.add(project.id);
        if (project.viewport !== undefined && (!record(project.viewport) || ![project.viewport.x, project.viewport.y, project.viewport.k].every((number) => typeof number === "number" && Number.isFinite(number)) || Number(project.viewport.k) <= 0)) throw new Error("备份视口无效，未写入任何项目");
        const ids = new Set<string>();
        for (const node of item.project.nodes) {
            if (!record(node) || typeof node.id !== "string" || !node.id || ids.has(node.id) || typeof node.title !== "string" || typeof node.type !== "string" || !node.type || !record(node.position) || ![node.position.x, node.position.y, node.width, node.height].every((number) => typeof number === "number" && Number.isFinite(number)) || Number(node.width) <= 0 || Number(node.height) <= 0) throw new Error("备份节点结构不完整或编号冲突");
            if (node.metadata !== undefined) validateNodeMetadata(node.metadata);
            ids.add(node.id);
        }
        const edgeIds = new Set<string>();
        for (const edge of item.project.connections) {
            if (!record(edge) || typeof edge.id !== "string" || !edge.id || edgeIds.has(edge.id) || typeof edge.fromNodeId !== "string" || typeof edge.toNodeId !== "string" || !ids.has(edge.fromNodeId) || !ids.has(edge.toNodeId)) throw new Error("备份含无效或重复的画布连线");
            edgeIds.add(edge.id);
        }
        const files = new Set<string>();
        for (const file of item.files) {
            if (!record(file) || typeof file.storageKey !== "string" || !file.storageKey.includes(":") || files.has(file.storageKey) || typeof file.path !== "string" || file.path.includes(":") || file.path.includes("\0") || file.path === "projects.json" || filePaths.has(file.path) || file.path.split(/[\\/]/).some((part) => !part || part === ".." || part === ".") || typeof file.mimeType !== "string" || !/^[\w.+-]+\/[\w.+-]+$/.test(file.mimeType) || !Number.isSafeInteger(file.bytes) || Number(file.bytes) < 0 || (file.sha256 !== undefined && (typeof file.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(file.sha256))) || (value.studio && !file.sha256)) throw new Error("备份文件清单无效或缺少完整性摘要");
            files.add(file.storageKey);
            filePaths.add(file.path);
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

function validateNodeMetadata(value: unknown) {
    if (!record(value)) throw new Error("备份节点内容无效，未写入任何项目");
    for (const name of ["content", "composerContent", "prompt", "status", "errorDetails", "model", "storageKey", "mimeType", "primaryImageId", "primaryTextId", "studioIntentId", "studioJobId"])
        if (value[name] !== undefined && typeof value[name] !== "string") throw new Error("备份节点文字字段无效，未写入任何项目");
    for (const name of ["naturalWidth", "naturalHeight", "bytes", "durationMs", "fontSize"])
        if (value[name] !== undefined && (typeof value[name] !== "number" || !Number.isFinite(value[name]) || Number(value[name]) < 0)) throw new Error("备份节点媒体信息无效，未写入任何项目");
    for (const name of ["images", "texts"]) {
        if (value[name] === undefined) continue;
        if (!Array.isArray(value[name])) throw new Error("备份节点列表无效，未写入任何项目");
        const ids = new Set<string>();
        for (const child of value[name]) {
            validateNodeMetadata(child);
            if (!record(child) || typeof child.id !== "string" || !child.id || ids.has(child.id)) throw new Error("备份节点列表编号无效，未写入任何项目");
            ids.add(child.id);
        }
    }
    if (value.references !== undefined && (!Array.isArray(value.references) || !value.references.every((item) => typeof item === "string"))) throw new Error("备份节点引用无效，未写入任何项目");
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
