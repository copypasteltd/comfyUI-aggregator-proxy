import { taskTypes, type AccessPolicy, type Capability, type CatalogModel, type JobRequest, type JsonObject, type ModelUsage, type Preset, type PublishedModel } from "./types";
import { validateModelSchema } from "./schema-rpc";
export { assertLocalSchema } from "./schema-local";
export const taskLabels: Record<string, string> = {
    text_to_image: "文生图", image_to_image: "图像编辑", references_to_image: "多参考生图",
    text_to_video: "文生视频", references_to_video: "参考生视频", text_to_audio: "音频 / 音乐", image_to_3d: "图片生 3D",
};

export function object(value: unknown): value is JsonObject {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function schemaProperties(usage: ModelUsage, part: "input" | "parameters"): Record<string, JsonObject> {
    const properties = usage.request_schema.properties as Record<string, JsonObject> | undefined;
    return (properties?.[part]?.properties || {}) as Record<string, JsonObject>;
}

export function presetParameters(usage: ModelUsage, preset: Preset): JsonObject {
    const allowed = schemaProperties(usage, "parameters");
    return Object.fromEntries(Object.entries({ ...usage.defaults, ...preset }).filter(([name]) => name in allowed));
}

export function describePreset(preset: Preset): string {
    const quality: Record<string, string> = { draft: "草稿", standard: "标准", high: "高质量" };
    return Object.entries(preset).map(([key, value]) => key === "quality" ? quality[String(value)] || String(value) : key === "seconds" ? `${value} 秒` : key === "sample_rate" ? `${value} Hz` : key === "channels" ? `${value} 声道` : String(value)).join(" · ");
}

// Advisory UI restrictions come only from this Key's published access policy.
// The server still performs admission and quota checks at submission time.
export function presetPolicyReason(model: CatalogModel, preset: Preset, policy: AccessPolicy): string | undefined {
    const spec = policy.spec_limits;
    if (!object(spec)) return undefined;
    const effective = { ...model.usage.defaults, ...preset };
    if (Array.isArray(spec.allowed_qualities) && !spec.allowed_qualities.includes(effective.quality)) return "当前 Key 未授权此质量";
    const video = model.task_type.endsWith("video");
    const audio = model.task_type === "text_to_audio";
    const mesh = model.task_type === "image_to_3d";
    const size = /^(\d+)x(\d+)$/.exec(String(effective.size || ""));
    const pixels = spec[video ? "video_max_pixels" : "image_max_pixels"];
    if (!audio && !mesh && size && typeof pixels === "number" && Number(size[1]) * Number(size[2]) > pixels) return "当前 Key 未授权此图像尺寸";
    const duration = spec[audio ? "audio_max_milliseconds" : "video_max_milliseconds"];
    if ((audio || video) && typeof duration === "number" && Number(effective.seconds) * 1000 > duration) return "当前 Key 未授权此时长";
    if (typeof spec.max_references === "number" && model.usage.references.exact_reference_count > spec.max_references) return "当前 Key 未授权此参考图数量";
    return undefined;
}

export async function validateJob(model: CatalogModel, request: JobRequest): Promise<string[]> {
    if (model.unavailable_reason) return [model.unavailable_reason];
    if (request.model !== model.id || request.task_type !== model.task_type) return ["模型或任务类型与当前发布不符"];
    const checked = await validateModelSchema(model.usage.request_schema, request);
    if (!checked.valid) return checked.errors;
    // The complete declared preset must match; output-only audio fields are never sent.
    const allowed = schemaProperties(model.usage, "parameters");
    const effective = { ...model.usage.defaults, ...request.parameters };
    const matches = model.usage.presets.filter((preset) => Object.entries(preset).filter(([key]) => key in allowed).every(([key, value]) => effective[key] === value));
    return matches.length === 1 ? [] : ["请选择模型公布的完整规格组合"];
}

export async function joinCatalog(models: PublishedModel[], capabilities: Capability[], scopes: string[]): Promise<CatalogModel[]> {
    const byId = new Map(models.map((model) => [model.id, model]));
    return Promise.all(capabilities.map(async (capability) => {
        const usage = byId.get(capability.id)?.usage;
        let unavailable_reason = "";
        try {
            if (!usage || usage.schema_version !== "1.0" || !taskTypes.includes(usage.task_type) || usage.task_type !== capability.task_type) throw new Error("缺少受支持的模型用法说明");
            if (!/^[a-f0-9]{64}$/.test(capability.release?.digest || "")) throw new Error("缺少明确的发布版本");
            if (!usage.presets?.length || !usage.parameter_details?.length || !usage.references?.slots || !usage.output?.media_types?.length) throw new Error("模型规格或输入槽位不完整");
            if (!usage.required_scopes.every((scope) => scopes.includes(scope))) throw new Error("当前 API Key 缺少此模型所需权限");
            const properties = usage.request_schema.properties as Record<string, JsonObject> | undefined;
            if (properties?.model?.const !== capability.id || properties?.task_type?.const !== capability.task_type) throw new Error("模型 Schema 与发布身份不符");
            const checked = await validateModelSchema(usage.request_schema, usage.example_request);
            if (!checked.valid) throw new Error("模型用法示例不符合其参数说明");
        } catch (error) {
            unavailable_reason = error instanceof Error ? error.message : "模型说明暂不可用";
        }
        return { ...capability, usage: usage as ModelUsage, ...(unavailable_reason ? { unavailable_reason } : {}) };
    }));
}
