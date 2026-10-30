import { sha256 } from "@noble/hashes/sha256";
import { bytesToHex } from "@noble/hashes/utils";
import { joinCatalog, object } from "./capabilities";
import { pauseBusinessRequests, waitBusinessTurn } from "../../aggregator-budget";
import { taskTypes, type AccessPolicy, type ApiResult, type Capability, type Catalog, type ContentMetadata, type JobRequest, type ListPage, type PublishedModel, type RemoteJob, type UploadedAsset } from "./types";

export function normalizeOrigin(value: string): string {
    const url = new URL(value.trim());
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || !["/", "/v1", "/v1/"].includes(url.pathname)) throw new Error("请填写服务源站，例如 http://服务器:端口；地址不能包含凭证、查询或其他路径");
    return url.origin;
}

export function identityFingerprint(origin: string, key: string): string {
    return bytesToHex(sha256(new TextEncoder().encode(`${normalizeOrigin(origin)}\0${key}`)));
}

export async function blobHash(blob: Blob): Promise<string> {
    const hash = sha256.create();
    const reader = blob.stream().getReader();
    try {
        for (;;) {
            const { done, value } = await reader.read();
            if (done) return bytesToHex(hash.digest());
            hash.update(value);
        }
    } finally { reader.releaseLock(); }
}

export function parseRetryAfter(value: string | null, now = Date.now()): number | undefined {
    if (!value) return undefined;
    const seconds = /^\d+(?:\.\d+)?$/.test(value.trim()) ? Number(value) : NaN;
    const at = Number.isFinite(seconds) ? now + seconds * 1000 : Date.parse(value);
    return Number.isFinite(at) ? Math.max(now, at) : undefined;
}

export class GatewayError extends Error {
    constructor(message: string, public status: number, public code: string, public retryAt?: number, public requestId?: string) { super(message); this.name = "GatewayError"; }
}

function identifier(value: string, kind: "job" | "content" | "asset"): string {
    const expression = kind === "asset" ? /^asset_[A-Za-z0-9_-]{6,80}$/ : new RegExp(`^${kind}_[a-f0-9]{32}$`);
    if (!expression.test(value)) throw new Error("服务返回的资源编号无效");
    return value;
}

function jobResponse(value: unknown): RemoteJob {
    if (!object(value) || typeof value.id !== "string" || value.object !== "generation.job" || typeof value.status !== "string" || !Number.isInteger(value.updated_revision) || !taskTypes.includes(value.task_type as never)) throw new Error("任务响应格式不受支持，请保留原任务编号后查询");
    identifier(value.id, "job");
    return value as RemoteJob;
}

export class AggregatorClient {
    readonly origin: string;
    readonly fingerprint: string;
    private policyRpm: number | null | undefined;
    constructor(origin: string, private readonly key: string, private readonly fetcher: typeof fetch = globalThis.fetch) {
        this.origin = normalizeOrigin(origin);
        if (!key.trim() || /[\r\n]/.test(key)) throw new Error("请输入有效的业务 API Key");
        this.fingerprint = identityFingerprint(this.origin, key);
    }

    private async send(path: string, options: RequestInit = {}): Promise<Response> {
        if (!path.startsWith("/v1/") || path.startsWith("//") || path.includes("#")) throw new Error("无效的业务接口路径");
        const headers = new Headers(options.headers);
        headers.set("Authorization", `Bearer ${this.key}`);
        if (this.policyRpm !== undefined) await waitBusinessTurn(this.fingerprint, this.policyRpm, options.signal || undefined);
        const response = await this.fetcher.call(globalThis, `${this.origin}${path}`, { ...options, headers, credentials: "omit", redirect: "error", cache: "no-store" });
        if (!response.ok) {
            let body: unknown;
            try { body = await response.json(); } catch { body = null; }
            const error = object(body) && object(body.error) ? body.error : {};
            const code = typeof error.code === "string" && /^[a-z0-9_]+$/.test(error.code) ? error.code : `http_${response.status}`;
            const raw = typeof error.message === "string" ? error.message : `服务请求失败（${response.status}）`;
            const retryAt = parseRetryAfter(response.headers.get("Retry-After"));
            if ((response.status === 429 || response.status === 503) && retryAt && this.policyRpm !== undefined) await pauseBusinessRequests(this.fingerprint, retryAt);
            throw new GatewayError(raw.split(this.key).join("[已隐藏凭证]"), response.status, code, retryAt, response.headers.get("X-Request-ID") || undefined);
        }
        return response;
    }

    private async json<T>(path: string, options?: RequestInit): Promise<ApiResult<T>> {
        const response = await this.send(path, options);
        return { data: await response.json() as T, retryAt: parseRetryAfter(response.headers.get("Retry-After")), requestId: response.headers.get("X-Request-ID") || undefined, studioContract: response.headers.get("X-Studio-Contract") || undefined, submissionLookup: response.headers.get("X-Studio-Submission-Lookup") || undefined };
    }

    async catalog(signal?: AbortSignal): Promise<Catalog> {
        const policy = await this.json<AccessPolicy>("/v1/access-policy", { signal });
        if (!Array.isArray(policy.data.scopes) || !object(policy.data.limits)) throw new Error("业务权限说明不完整");
        const rpm = policy.data.limits.rpm;
        if (rpm) this.policyRpm = rpm.mode === "unlimited" ? null : rpm.value ?? 0;
        const models = await this.json<{ data: PublishedModel[] }>("/v1/models", { signal });
        const capabilities = await this.json<{ data: Capability[] }>("/v1/capabilities", { signal });
        if (!Array.isArray(models.data.data) || !Array.isArray(capabilities.data.data)) throw new Error("模型目录格式不受支持");
        return { policy: policy.data, models: await joinCatalog(models.data.data, capabilities.data.data, policy.data.scopes), releaseGuard: policy.studioContract === "1", submissionLookup: policy.submissionLookup === "1" };
    }

    async createJob(body: string, idempotencyKey: string, releaseDigest: string, signal?: AbortSignal): Promise<ApiResult<RemoteJob>> {
        if (!idempotencyKey || !/^[a-f0-9]{64}$/.test(releaseDigest)) throw new Error("缺少持久提交标识或发布摘要");
        const result = await this.json<unknown>("/v1/jobs", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "X-Expected-Release-Digest": releaseDigest }, body, signal });
        return { ...result, data: jobResponse(result.data) };
    }

    async lookupSubmission(body: string, idempotencyKey: string, releaseDigest: string, signal?: AbortSignal): Promise<ApiResult<import("./types").SubmissionLookup>> {
        if (!idempotencyKey || !/^[a-f0-9]{64}$/.test(releaseDigest)) throw new Error("缺少原提交标识或发布摘要，无法只读核对");
        const result = await this.json<unknown>("/v1/jobs/lookup", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "X-Expected-Release-Digest": releaseDigest }, body, signal });
        const value = result.data;
        if (!object(value) || value.object !== "generation.submission_lookup") throw new Error("核对响应格式不受支持，原记录已保留");
        if (value.outcome === "found") return { ...result, data: { object: value.object, outcome: "found", job: jobResponse(value.job) } };
        if (value.outcome === "not_observed" && value.job === null) return { ...result, data: { object: value.object, outcome: "not_observed", job: null } };
        throw new Error("核对结果与任务信息不符，原记录已保留");
    }

    async job(id: string, signal?: AbortSignal): Promise<ApiResult<RemoteJob>> {
        const result = await this.json<unknown>(`/v1/jobs/${identifier(id, "job")}`, { signal });
        const job = jobResponse(result.data);
        if (job.id !== id) throw new Error("返回任务编号与查询不符");
        return { ...result, data: job };
    }

    async cancel(id: string, signal?: AbortSignal): Promise<ApiResult<RemoteJob>> {
        const result = await this.json<unknown>(`/v1/jobs/${identifier(id, "job")}/cancel`, { method: "POST", signal });
        const job = jobResponse(result.data);
        if (job.id !== id) throw new Error("取消响应的任务编号不符");
        return { ...result, data: job };
    }

    jobs(cursor?: string, signal?: AbortSignal) { return this.json<ListPage<RemoteJob>>(`/v1/jobs${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, { signal }); }
    contents(cursor?: string, signal?: AbortSignal) { return this.json<ListPage<ContentMetadata & { job_id: string }>>(`/v1/content${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, { signal }); }

    async upload(file: Blob, name: string, idempotencyKey: string, expectedHash: string, signal?: AbortSignal): Promise<UploadedAsset> {
        const form = new FormData();
        form.append("file", file, name);
        const { data } = await this.json<UploadedAsset>("/v1/assets", { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: form, signal });
        identifier(data.id, "asset");
        if (data.status !== "ready" || data.bytes !== file.size || data.sha256 !== expectedHash || data.media_type !== file.type) throw new Error("上传文件的完整性或媒体类型不匹配；请核对原上传记录");
        return data;
    }

    async metadata(id: string, signal?: AbortSignal): Promise<ContentMetadata> {
        const { data } = await this.json<ContentMetadata>(`/v1/content/${identifier(id, "content")}`, { signal });
        if (data.id !== id || !Number.isSafeInteger(data.bytes) || data.bytes <= 0 || !/^[a-f0-9]{64}$/.test(data.sha256) || !Number.isFinite(data.expires_at) || !object(data.metadata)) throw new Error("产物元数据不完整");
        return data;
    }

    async download(metadata: ContentMetadata, signal?: AbortSignal): Promise<Blob> {
        const response = await this.send(`/v1/content/${identifier(metadata.id, "content")}/download`, { signal });
        if (response.status !== 200 || !response.body || response.headers.get("Content-Type")?.split(";", 1)[0] !== metadata.media_type) throw new Error("下载响应的状态或媒体类型不匹配");
        const length = response.headers.get("Content-Length");
        if (length !== null && Number(length) !== metadata.bytes) throw new Error("下载长度与产物记录不符");
        const reader = response.body.getReader();
        const hash = sha256.create();
        const chunks: Uint8Array<ArrayBuffer>[] = [];
        let received = 0;
        try {
            for (;;) {
                const { done, value } = await reader.read();
                if (done) break;
                received += value.byteLength;
                if (received > metadata.bytes) throw new Error("下载文件超过产物声明长度");
                hash.update(value);
                chunks.push(new Uint8Array(value));
            }
            if (received !== metadata.bytes || bytesToHex(hash.digest()) !== metadata.sha256) throw new Error("下载文件不完整或校验失败，尚未保存本机");
        } catch (error) {
            await reader.cancel().catch(() => {});
            throw error;
        } finally { reader.releaseLock(); }
        const blob = new Blob(chunks, { type: metadata.media_type });
        if (metadata.media_type === "model/gltf-binary") {
            const header = new DataView(await blob.slice(0, 12).arrayBuffer());
            if (header.byteLength < 12 || header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2 || header.getUint32(8, true) !== metadata.bytes) throw new Error("GLB 文件头或声明长度无效");
        }
        return blob;
    }
}

export function freezeRequest(request: JobRequest): string { return JSON.stringify(request); }
