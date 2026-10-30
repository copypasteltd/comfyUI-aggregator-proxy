import { object, presetParameters, presetPolicyReason, schemaProperties } from "./capabilities";
import type { AccessPolicy, CatalogModel, JobRequest, JsonObject } from "./types";

export type ReferenceChoice = { assetId: string; weight?: number };
export type ComposerValues = { input: JsonObject; parameters: JsonObject; references: Record<string, ReferenceChoice | undefined> };

export function firstAllowedPreset(model: CatalogModel, policy: AccessPolicy): number {
    return model.usage.presets.findIndex((preset) => !presetPolicyReason(model, preset, policy));
}

export function suggestComposerModel(models: CatalogModel[], policy: AccessPolicy, preferred?: string, media?: string, imageCount = 0): CatalogModel | undefined {
    const available = models.filter((model) => !model.unavailable_reason && firstAllowedPreset(model, policy) >= 0);
    if (preferred) return available.find((model) => model.id === preferred);
    const matching = available.filter((model) => !media || model.usage.output.media_types.some((mime) => mime.startsWith(`${media}/`)));
    const slots = (model: CatalogModel) => model.usage.references.slots.filter((slot) => slot.path !== "input.mask").length;
    return (imageCount ? matching.find((model) => slots(model) === imageCount) || matching.find((model) => slots(model) > 0) : matching.find((model) => slots(model) === 0)) || matching[0];
}

export function slotIndex(path: string): number | undefined {
    const match = /^input\.references\[(\d+)\]$/.exec(path);
    return match ? Number(match[1]) : undefined;
}

export function buildComposerRequest(model: CatalogModel, presetIndex: number, values: ComposerValues): JobRequest {
    const preset = model.usage.presets[presetIndex];
    if (!preset) throw new Error("请选择当前模型公布的规格");
    const input: JsonObject = {};
    const inputProperties = schemaProperties(model.usage, "input");
    for (const [name, value] of Object.entries(values.input)) {
        if (!(name in inputProperties) || ["source_image", "mask", "references"].includes(name)) throw new Error("输入字段不符合模型说明");
        if (value !== undefined && value !== "") input[name] = value;
    }
    const references: JsonObject[] = [];
    for (const slot of model.usage.references.slots) {
        const choice = values.references[slot.path];
        if (!choice) { if (slot.required) throw new Error(`请添加${slot.path === "input.source_image" ? "源图片" : slot.path === "input.mask" ? "蒙版" : `参考图 ${Number(slotIndex(slot.path)) + 1}`}`); continue; }
        if (!/^asset_[A-Za-z0-9_-]{6,80}$/.test(choice.assetId)) throw new Error("参考图尚未完成上传");
        if (slot.path === "input.source_image" || slot.path === "input.mask") input[slot.path.slice(6)] = { asset_id: choice.assetId };
        else {
            const index = slotIndex(slot.path);
            if (index === undefined) throw new Error("当前输入槽位尚未适配，已阻止提交");
            const schema = inputProperties.references?.prefixItems as JsonObject[] | undefined;
            const properties = schema?.[index]?.properties as Record<string, JsonObject> | undefined;
            const example = model.usage.example_request.input.references as JsonObject[] | undefined;
            const role = properties?.role?.const ?? example?.[index]?.role;
            if (typeof role !== "string") throw new Error("模型没有明确参考图角色，已阻止提交");
            const reference: JsonObject = { asset_id: choice.assetId, role };
            if (choice.weight !== undefined) {
                if (!slot.weight_supported || !object(properties?.weight)) throw new Error("此参考图不支持权重");
                reference.weight = choice.weight;
            }
            references[index] = reference;
        }
    }
    if (references.length) {
        if (Array.from(references).some((reference) => !reference)) throw new Error("参考图片必须按槽位顺序完整选择");
        input.references = references;
    }
    const parameters = presetParameters(model.usage, preset);
    const allowed = schemaProperties(model.usage, "parameters");
    const fixed = Object.keys(preset).filter((name) => name in allowed);
    for (const [name, value] of Object.entries(values.parameters)) {
        if (!(name in allowed) || fixed.includes(name) || name === "n") throw new Error("不能覆盖完整规格中的固定参数");
        if (value !== undefined && value !== "") parameters[name] = value;
    }
    if ("n" in allowed) parameters.n = 1;
    return { model: model.id, task_type: model.task_type, input, parameters };
}
