import { nanoid } from "nanoid";
import { GatewayError, blobHash, freezeRequest } from "./api/aggregator/client";
import { presetPolicyReason, validateJob } from "./api/aggregator/capabilities";
import { terminalStatuses, type ApiResult, type CatalogModel, type JobRequest, type RemoteJob } from "./api/aggregator/types";
import { journalGet, journalList, journalPut, prepareIntent, updateIntent, type IdentityRef, type InputUpload, type Intent, type LocalResult } from "./aggregator-journal";
import { assertLocalResult } from "./studio-result-ownership";
import { useStudioStore } from "@/stores/use-studio-store";

const inFlight = new Map<string, Promise<Intent>>();
const controllers = new Map<string, AbortController>();
const listeners = new Set<() => void>();
let channel: BroadcastChannel | undefined;
const changed = () => { listeners.forEach((listener) => listener()); channel?.postMessage("changed"); };

export function subscribeStudioTasks(listener: () => void) {
    listeners.add(listener);
    if (!channel && globalThis.BroadcastChannel) {
        channel = new BroadcastChannel("comfy-studio-journal");
        channel.onmessage = () => listeners.forEach((item) => item());
    }
    return () => { listeners.delete(listener); if (!listeners.size) { channel?.close(); channel = undefined; } };
}

function connection(identity?: IdentityRef) {
    const state = useStudioStore.getState();
    if (!state.client || !state.activeProfileId) throw new Error("请先连接生成服务");
    const reference = { profileId: state.activeProfileId, origin: state.client.origin, fingerprint: state.client.fingerprint };
    if (identity && (identity.fingerprint !== reference.fingerprint || identity.origin !== reference.origin)) throw new Error("请使用原服务及原 API Key 恢复这项任务");
    return { client: state.client, identity: reference, catalog: state.catalog };
}

export async function stageUpload(file: File, model: CatalogModel): Promise<InputUpload> {
    const { identity } = connection();
    const references = model.usage.references;
    if (!references.media_types.includes(file.type) || file.size <= 0 || file.size > references.max_upload_bytes) throw new Error("参考图类型或文件大小不符合模型要求");
    const bitmap = await createImageBitmap(file);
    try { if (bitmap.width * bitmap.height > references.max_image_pixels) throw new Error("参考图像素超过模型公布的上限"); }
    finally { bitmap.close(); }
    const record: InputUpload = { version: 1, id: nanoid(), identity, name: file.name, blob: file, sha256: await blobHash(file), idempotencyKey: `studio-upload-${nanoid()}`, createdAt: Date.now() };
    await journalPut("uploads", record);
    return record;
}

export async function uploadReference(id: string, signal?: AbortSignal): Promise<InputUpload> {
    const record = await journalGet("uploads", id);
    if (!record || record.version !== 1) throw new Error("找不到原上传记录，未发起新上传");
    const { client } = connection(record.identity);
    if (record.asset) {
        if (record.asset.expires_at * 1000 <= Date.now()) throw new Error("源图片已过期，请明确选择新的图片再创建任务");
        return record;
    }
    try {
        const asset = await client.upload(record.blob, record.name, record.idempotencyKey, record.sha256, signal);
        const result = { ...record, asset, error: undefined };
        await journalPut("uploads", result);
        changed();
        return result;
    } catch (error) {
        await journalPut("uploads", { ...record, error: error instanceof Error ? error.message : "上传结果未知" });
        changed(); throw error;
    }
}

export async function createIntent(projectId: string, nodeId: string, model: CatalogModel, request: JobRequest, canvas?: Intent["canvas"]): Promise<Intent> {
    const { identity, catalog } = connection();
    if (!catalog?.releaseGuard) throw new Error("服务尚未支持画布发布版本校验，已阻止创建任务");
    const errors = await validateJob(model, request);
    if (errors.length) throw new Error(errors.join("；"));
    const restricted = presetPolicyReason(model, request.parameters as Record<string, string | number | boolean>, catalog.policy);
    if (restricted) throw new Error(restricted);
    const id = nanoid();
    const intent: Intent = { version: 1, id, identity, projectId, nodeId, body: freezeRequest({ ...request, client_request_id: id }), idempotencyKey: `studio-job-${id}`, releaseDigest: model.release.digest, stage: "prepared", activeNodeKey: `${identity.fingerprint}:${projectId}:${nodeId}`, output: { mediaTypes: model.usage.output.media_types, maxBytes: model.usage.output.max_bytes }, createdAt: Date.now(), updatedAt: Date.now() };
    const saved = await prepareIntent({ ...intent, canvas });
    changed(); return saved;
}

async function applyJob(id: string, result: ApiResult<RemoteJob>): Promise<Intent> {
    const updated = await updateIntent(id, (intent) => {
        const request = JSON.parse(intent.body) as JobRequest;
        if (result.data.model !== request.model || result.data.task_type !== request.task_type || intent.job && intent.job.id !== result.data.id) throw new Error("任务归属与原提交不符");
        if (intent.job && intent.job.updated_revision > result.data.updated_revision) return intent;
        if (intent.job && (intent.job.updated_revision === result.data.updated_revision && intent.job.status !== result.data.status || terminalStatuses.has(intent.job.status) && intent.job.status !== result.data.status)) throw new Error("任务状态与已确认终态或版本冲突，原记录已保留");
        const pollIntervalMs = result.retryAt ? Math.max(0, result.retryAt - Date.now()) : intent.pollIntervalMs;
        return { ...intent, job: result.data, stage: terminalStatuses.has(result.data.status) ? "terminal" : "accepted", updatedAt: Date.now(), error: undefined, pollPaused: false, pollIntervalMs, retryAt: result.retryAt || (pollIntervalMs ? Date.now() + pollIntervalMs : undefined) };
    });
    changed(); return updated;
}

function singleFlight(id: string, run: (signal: AbortSignal) => Promise<Intent>, external?: AbortSignal): Promise<Intent> {
    const running = inFlight.get(id);
    if (running) return running;
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (external?.aborted) abort(); else external?.addEventListener("abort", abort, { once: true });
    controllers.set(id, controller);
    const promise = run(controller.signal).finally(() => { inFlight.delete(id); controllers.delete(id); external?.removeEventListener("abort", abort); });
    inFlight.set(id, promise); return promise;
}

export function stopLocalWait(id: string) { controllers.get(id)?.abort(); }

export function submitIntent(id: string, signal?: AbortSignal): Promise<Intent> {
    return singleFlight(id, async (activeSignal) => {
        const existing = await journalGet("intents", id);
        if (!existing) throw new Error("任务日志未持久化，已阻止提交");
        const { client } = connection(existing.identity);
        if (existing.job) return existing;
        // The immutable body and key were committed before this state and before POST.
        const intent = await updateIntent(id, (value) => ({ ...value, stage: "submitting", updatedAt: Date.now(), error: undefined }));
        changed();
        try { return await applyJob(id, await client.createJob(intent.body, intent.idempotencyKey, intent.releaseDigest, activeSignal)); }
        catch (error) {
            await updateIntent(id, (value) => ({ ...value, stage: value.job ? value.stage : "unknown", error: error instanceof Error ? error.message : "提交结果未知，请核对原提交", retryAt: error instanceof GatewayError ? error.retryAt : undefined, updatedAt: Date.now() }));
            changed(); throw error;
        }
    }, signal);
}

export function refreshIntent(id: string, signal?: AbortSignal): Promise<Intent> {
    return singleFlight(id, async (activeSignal) => {
        const intent = await journalGet("intents", id);
        if (!intent?.job) throw new Error("尚未确认任务编号，请先核对原提交");
        const { client } = connection(intent.identity);
        try { return await applyJob(id, await client.job(intent.job.id, activeSignal)); }
        catch (error) {
            if (activeSignal.aborted) throw error;
            const retryAt = error instanceof GatewayError && [429, 503].includes(error.status) ? error.retryAt : undefined;
            await updateIntent(id, (value) => ({ ...value, error: error instanceof Error ? error.message : "查询暂不可用", retryAt, pollPaused: !retryAt }));
            changed(); throw error;
        }
    }, signal);
}

export const jobStatusLabels: Record<string, string> = {
    admission_pending: "等待准入", queued: "排队中", leased: "已分配设备", preparing: "准备中", submitting: "提交至设备", running: "生成中", collecting: "收集产物", uploading: "上传产物", verifying: "校验产物", reconciling: "核对设备状态", retry_wait: "等待内部恢复", cancel_requested: "等待取消确认", succeeded: "已完成", failed: "失败", cancelled: "已取消", expired: "已过期",
};

export async function cancelIntent(id: string, signal?: AbortSignal): Promise<Intent> {
    const intent = await journalGet("intents", id);
    if (!intent?.job) throw new Error("提交归属尚未确认，不能将浏览器中止当成远端取消；请先核对原提交");
    const { client } = connection(intent.identity);
    await updateIntent(id, (value) => ({ ...value, cancelRequested: true, updatedAt: Date.now() }));
    changed();
    return applyJob(id, await client.cancel(intent.job.id, signal));
}

export async function saveResult(intentId: string, contentId: string, signal?: AbortSignal): Promise<LocalResult> {
    const intent = await journalGet("intents", intentId);
    if (!intent?.job?.artifacts?.some((artifact) => artifact.id === contentId)) throw new Error("产物不属于这项任务，请先刷新任务详情");
    const { client, identity } = connection(intent.identity);
    const id = `studio:${identity.fingerprint}:${contentId}`;
    const existing = await journalGet("results", id);
    if (existing) {
        assertLocalResult(existing, identity, contentId, intent.job!.id);
        if (!intent.output.mediaTypes.includes(existing.metadata.media_type) || existing.metadata.bytes > intent.output.maxBytes) throw new Error("本机产物超过原模型公布范围");
        return existing;
    }
    const metadata = await client.metadata(contentId, signal);
    if (!intent.output.mediaTypes.includes(metadata.media_type) || metadata.bytes > intent.output.maxBytes) throw new Error("产物的媒体类型或大小超过原模型公布范围，未载入浏览器");
    const blob = await client.download(metadata, signal);
    const result: LocalResult = { version: 1, id, identity, intentId, jobId: intent.job!.id, metadata, blob, savedAt: Date.now() };
    await journalPut("results", result);
    changed(); return result;
}

export async function localTasks(): Promise<Intent[]> {
    const { identity } = connection();
    const values = (await journalList("intents")).filter((intent) => intent.version === 1 && intent.identity.fingerprint === identity.fingerprint && intent.identity.origin === identity.origin);
    for (const intent of values) {
        try { const request = JSON.parse(intent.body); if (!request || typeof request.model !== "string" || typeof request.task_type !== "string" || !request.input) throw new Error(); }
        catch { throw new Error("部分任务日志损坏，原记录已保留；请从服务端历史查询已知任务"); }
    }
    return values.sort((a, b) => b.createdAt - a.createdAt);
}

// Server history is authoritative and may predate this browser's submission journal.
// Reading an existing job never constructs a generation intent or sends a generation POST.
export async function saveRemoteResult(jobId: string, contentId: string, signal?: AbortSignal): Promise<LocalResult> {
    const { client, identity } = connection();
    const { data: job } = await client.job(jobId, signal);
    if (job.status !== "succeeded" || !job.artifacts?.some((artifact) => artifact.id === contentId)) throw new Error("此产物不属于已完成的原任务");
    const id = `studio:${identity.fingerprint}:${contentId}`;
    const existing = await journalGet("results", id);
    if (existing) { assertLocalResult(existing, identity, contentId, jobId); return existing; }
    const metadata = await client.metadata(contentId, signal);
    const blob = await client.download(metadata, signal);
    const result: LocalResult = { version: 1, id, identity, intentId: `remote:${job.id}`, jobId: job.id, metadata, blob, savedAt: Date.now() };
    await journalPut("results", result);
    changed(); return result;
}
