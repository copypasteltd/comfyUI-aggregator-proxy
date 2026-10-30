import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { Alert, App, Button, Drawer, Input, InputNumber, Select, Switch } from "antd";
import { Sparkles, Upload } from "lucide-react";
import { useStudioStore } from "@/stores/use-studio-store";
import { useStudioComposerStore } from "@/stores/use-studio-composer-store";
import { createIntentBatch, stageUpload, submitIntent, submitPreparedBatch, uploadReference, subscribeStudioTasks, validateReferenceOriginal } from "@/services/aggregator-engine";
import { MAX_GENERATION_COUNT } from "@/constant/generation";
import { journalList, type InputUpload } from "@/services/aggregator-journal";
import { describePreset, presetPolicyReason, schemaProperties, taskLabels, validateJob } from "@/services/api/aggregator/capabilities";
import { buildComposerRequest, firstAllowedPreset, suggestComposerModel, slotIndex, type ReferenceChoice } from "@/services/api/aggregator/composer";
import type { CatalogModel, JsonObject } from "@/services/api/aggregator/types";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import { getImageBlob } from "@/services/image-storage";
import StudioReferencePreview from "./studio-reference-preview";

const fieldLabels: Record<string, string> = { prompt: "创作描述", negative_prompt: "避免出现的内容", seed: "种子", strength: "变换强度", fps: "帧率" };
function field(name: string, schema: JsonObject, value: unknown, change: (value: unknown) => void, disabled: boolean) {
    const label = fieldLabels[name] || name;
    if (Array.isArray(schema.enum)) return <Select aria-label={label} disabled={disabled} allowClear value={value} onChange={change} options={schema.enum.map((item) => ({ value: item, label: String(item) }))} />;
    if (schema.type === "number" || schema.type === "integer") return <InputNumber aria-label={label} disabled={disabled} value={typeof value === "number" ? value : null} min={schema.minimum as number | undefined} max={schema.maximum as number | undefined} step={schema.type === "integer" ? 1 : typeof schema.multipleOf === "number" ? schema.multipleOf : undefined} precision={schema.type === "integer" ? 0 : undefined} onChange={(number) => change(number ?? undefined)} placeholder="留空使用服务默认值" style={{ width: "100%" }} />;
    if (schema.type === "boolean") return <Switch aria-label={label} disabled={disabled} checked={value === true} onChange={change} />;
    return <Input.TextArea aria-label={label} disabled={disabled} value={String(value || "")} onChange={(event) => change(event.target.value)} maxLength={schema.maxLength as number | undefined} autoSize={{ minRows: name === "prompt" ? 4 : 2 }} />;
}

export default function StudioComposer({ projectId, nodes }: { projectId: string; nodes: CanvasNodeData[] }) {
    const { message } = App.useApp();
    const draft = useStudioComposerStore((state) => state.draft);
    const close = useStudioComposerStore((state) => state.close);
    const client = useStudioStore((state) => state.client);
    const catalog = useStudioStore((state) => state.catalog);
    const [modelId, setModelId] = useState("");
    const [presetIndex, setPresetIndex] = useState(0);
    const [count, setCount] = useState(1);
    const [input, setInput] = useState<JsonObject>({});
    const [parameters, setParameters] = useState<JsonObject>({});
    const [choices, setChoices] = useState<Record<string, string | undefined>>({});
    const [weights, setWeights] = useState<Record<string, number | undefined>>({});
    const [uploads, setUploads] = useState<InputUpload[]>([]);
    const [canvasSources, setCanvasSources] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const initializedDraft = useRef<string | undefined>(undefined);
    useEffect(() => () => close(), [projectId, close]);
    const models = catalog?.models || [];
    const model = models.find((item) => item.id === modelId && item.usage && !item.unavailable_reason);
    const imageNodes = nodes.filter((node) => node.type === CanvasNodeType.Image && node.metadata?.storageKey);
    const selectedImages = draft?.sourceIds.map((id) => imageNodes.find((node) => node.id === id)).filter((node): node is CanvasNodeData => Boolean(node)) || [];
    const initialChoices = (selected?: CatalogModel) => Object.fromEntries((selected?.usage.references.slots.filter((slot) => slot.path !== "input.mask") || []).slice(0, selectedImages.length).map((slot, index) => [slot.path, `canvas:${selectedImages[index].id}`]));
    useEffect(() => {
        if (!draft) { initializedDraft.current = undefined; return; }
        if (initializedDraft.current === draft.id) return;
        if (!catalog) { setModelId(""); return; }
        const suggested = catalog ? suggestComposerModel(models, catalog.policy, draft.preferredModel, draft.media, selectedImages.length) : undefined;
        setModelId(suggested?.id || ""); setPresetIndex(suggested && catalog ? firstAllowedPreset(suggested, catalog.policy) : -1); setCount(1); setInput({ prompt: draft.prompt }); setParameters({}); setChoices(initialChoices(suggested)); setCanvasSources({}); setWeights({}); setError("");
        initializedDraft.current = draft.id;
    // Initialize once per explicit creation, after a restoring catalog becomes available.
    // A later catalog refresh must not replace the user's model, prompt or references.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [draft?.id, catalog]);
    useEffect(() => {
        let active = true;
        const load = async () => {
            try { const records = await journalList("uploads"); if (active) setUploads(records.filter((item) => item.version === 1 && item.identity.fingerprint === client?.fingerprint && item.identity.origin === client?.origin)); }
            catch (reason) { if (active) setError(reason instanceof Error ? reason.message : "无法读取原参考图记录"); }
        };
        void load(); const off = subscribeStudioTasks(() => void load());
        return () => { active = false; off(); };
    }, [client]);
    const act = async (work: () => Promise<void>) => {
        if (busy) return;
        setBusy(true); setError("");
        try { await work(); }
        catch (reason) { setError(reason instanceof Error ? reason.message : "操作未完成"); }
        finally { setBusy(false); }
    };
    const chooseFile = (path: string, file: File) => act(async () => {
        if (!model) throw new Error("请先选择模型");
        const record = await stageUpload(file, model);
        setUploads((items) => [...items.filter((item) => item.id !== record.id), record]); setChoices((items) => ({ ...items, [path]: `upload:${record.id}` }));
    });
    const submit = () => act(async () => {
        if (!model || !draft || !client) throw new Error("请先连接服务并选择模型");
        if (!Number.isSafeInteger(count) || count < 1 || count > MAX_GENERATION_COUNT) throw new Error("请选择画布支持的任务数量");
        const restriction = catalog && presetPolicyReason(model, model.usage.presets[presetIndex], catalog.policy);
        if (restriction) throw new Error(restriction);
        const fingerprint = client.fingerprint;
        const assertConnection = () => { if (useStudioStore.getState().client?.fingerprint !== fingerprint) throw new Error("连接已切换，请使用原连接继续"); };
        const allowed = schemaProperties(model.usage, "input");
        const values = Object.fromEntries(Object.entries(input).filter(([key]) => key in allowed));
        // Validate the full form before any remote upload. Temporary IDs are only
        // local bindings for the schema; createJob always receives real asset IDs.
        const previewReferences = Object.fromEntries(model.usage.references.slots.filter((slot) => choices[slot.path]).map((slot, index) => [slot.path, { assetId: `asset_preflight_${index}`, weight: weights[slot.path] }]));
        const issues = await validateJob(model, buildComposerRequest(model, presetIndex, { input: values, parameters, references: previewReferences }));
        if (issues.length) throw new Error(issues.join("；"));
        const staged: Record<string, InputUpload> = {};
        const sourceIds = new Set(draft.sourceIds.filter((id) => nodes.some((node) => node.id === id && node.type === CanvasNodeType.Text)));
        // Stage and verify every selected original before uploading the first one.
        for (const slot of model.usage.references.slots) {
            assertConnection();
            const selected = choices[slot.path];
            if (!selected) continue;
            let record: InputUpload | undefined;
            if (selected.startsWith("canvas:")) {
                const source = imageNodes.find((node) => node.id === selected.slice(7));
                const blob = source?.metadata?.storageKey ? await getImageBlob(source.metadata.storageKey) : null;
                if (!blob) throw new Error("所选画布图片的本机原件不可读取，请重新选择");
                record = await stageUpload(new File([blob], source!.title || "reference", { type: blob.type }), model);
                sourceIds.add(source!.id);
                setCanvasSources((items) => ({ ...items, [record!.id]: source!.id }));
                setUploads((items) => [...items.filter((item) => item.id !== record!.id), record!]);
                setChoices((items) => ({ ...items, [slot.path]: `upload:${record!.id}` }));
            } else if (selected.startsWith("upload:")) {
                record = uploads.find((item) => item.id === selected.slice(7));
                if (record && canvasSources[record.id]) sourceIds.add(canvasSources[record.id]);
                if (record) await validateReferenceOriginal(record.blob, model);
            }
            if (!record) throw new Error("找不到所选参考图记录，请重新选择");
            if (record.identity.fingerprint !== fingerprint || record.identity.origin !== client.origin) throw new Error("所选参考图属于其他连接，请重新选择");
            if (record.asset && record.asset.expires_at * 1000 <= Date.now()) throw new Error("所选参考图已过期，请明确选择新的图片");
            staged[slot.path] = record;
        }
        const references: Record<string, ReferenceChoice | undefined> = {};
        for (const slot of model.usage.references.slots) {
            assertConnection();
            const record = staged[slot.path];
            if (!record) continue;
            const uploaded = await uploadReference(record.id);
            if (!uploaded.asset) throw new Error("参考图上传尚未确认");
            references[slot.path] = { assetId: uploaded.asset.id, weight: weights[slot.path] };
        }
        if (useStudioStore.getState().client?.fingerprint !== fingerprint) throw new Error("连接已切换，请使用原连接继续");
        const request = buildComposerRequest(model, presetIndex, { input: values, parameters, references });
        const entries = Array.from({ length: count }, (_, index) => {
            const node: CanvasNodeData = { id: index ? `${draft.id}-${index}` : draft.id, type: "studio:task", title: model.usage.display_name + (count > 1 ? ` · ${index + 1}/${count}` : ""), position: { x: draft.position.x + index % 3 * 380, y: draft.position.y + Math.floor(index / 3) * 300 }, width: 340, height: 260, metadata: { model: model.id, prompt: String(request.input.prompt || ""), status: "loading" } };
            return { nodeId: node.id, canvas: { node, sourceIds: [...sourceIds] } };
        });
        const intents = await createIntentBatch(projectId, model, request, entries, draft.id);
        close();
        try { if (count > 1) await submitPreparedBatch(draft.id, projectId, intents[0].identity); else await submitIntent(intents[0].id); message.success(count > 1 ? "批次已提交，可在任务页逐项查看" : "任务已受理，可以离开画布，稍后继续查看"); }
        catch (reason) { message.warning(reason instanceof Error ? reason.message : "提交结果待核对；原提交记录已保留"); }
    });
    const inputProperties = model ? schemaProperties(model.usage, "input") : {};
    const parameterProperties = model ? schemaProperties(model.usage, "parameters") : {};
    const fixedFields = Object.keys(model?.usage.presets[presetIndex] || {});
    const preset = model?.usage.presets[presetIndex];
    const presetRestriction = model && preset && catalog ? presetPolicyReason(model, preset, catalog.policy) : undefined;
    const selectedCanvasIds = new Set(Object.values(choices).filter((value) => value?.startsWith("canvas:")).map((value) => value!.slice(7)));
    const unusedImages = selectedImages.filter((node) => !selectedCanvasIds.has(node.id) && !Object.values(choices).some((value) => value?.startsWith("upload:") && canvasSources[value.slice(7)] === node.id));
    return <Drawer open={Boolean(draft)} onClose={() => !busy && close()} title={<span><Sparkles size={17} /> 新建创作</span>} size="large" className="studio-composer" destroyOnHidden={false} footer={<div className="studio-actions"><Button onClick={close} disabled={busy}>关闭</Button><Button type="primary" icon={<Sparkles size={16} />} loading={busy} disabled={!model || !preset || Boolean(presetRestriction) || !catalog?.releaseGuard} onClick={() => void submit()}>提交 {count} 项生成任务</Button></div>}>
        {!client ? <Alert showIcon type="info" title="连接你的生成服务" description={<NavLink to="/config" onClick={close}>填写地址及业务 API Key 后开始创作</NavLink>} /> : <div className="studio-form">
            {!catalog?.releaseGuard && <Alert showIcon type="warning" title="当前服务仅可查看模型" description="该 API 未声明提交所需的发布校验能力，请连接已支持此能力的原服务。" />}
            {error && <Alert type="error" showIcon title="操作尚未完成" description={error} />}
            <label>模型<Select aria-label="生成模型" showSearch optionFilterProp="label" value={modelId || undefined} onChange={(id) => { const selected = models.find((item) => item.id === id); setModelId(id); setPresetIndex(selected && catalog ? firstAllowedPreset(selected, catalog.policy) : -1); setParameters({}); setChoices(initialChoices(selected)); setWeights({}); setError(""); }} disabled={busy} options={models.map((item) => { const blocked = !item.unavailable_reason && catalog && firstAllowedPreset(item, catalog.policy) < 0; return { value: item.id, disabled: Boolean(item.unavailable_reason || blocked), label: `${taskLabels[item.task_type]} · ${item.usage?.display_name || item.id}${blocked ? " · 当前 Key 无可用规格" : ""}` }; })} /></label>
            {!model && <Alert type="info" showIcon title="请选择可用模型" description="当前创作类型没有已授权的可用规格时，可以选择其他模型，或返回模型页刷新目录。" />}
            {model && <>
                <p>{model.usage.description}</p>
                <label>完整规格<Select aria-label="完整规格" value={preset ? presetIndex : undefined} onChange={(index) => { setPresetIndex(index); setParameters({}); }} disabled={busy} options={model.usage.presets.map((preset, index) => { const restriction = catalog && presetPolicyReason(model, preset, catalog.policy); return { value: index, disabled: Boolean(restriction), label: `${describePreset(preset)}${restriction ? ` · ${restriction}` : ""}` }; })} /></label>
                {presetRestriction && <Alert type="warning" showIcon title={presetRestriction} description="请重新选择当前 Key 获准使用的规格。" />}
                <label>任务数量<InputNumber aria-label="任务数量" value={count} min={1} max={MAX_GENERATION_COUNT} precision={0} disabled={busy} onChange={(value) => setCount(value ?? 1)} style={{ width: "100%" }} /></label>
                {count > 1 && <p className="studio-note">将创建 {count} 项独立任务，分别排队并占用额度；固定种子会重复使用。中途提交未确认时暂停，已受理项继续，未发送项可在任务页继续。</p>}
                {Object.entries(inputProperties).filter(([name]) => !["source_image", "mask", "references"].includes(name)).map(([name, schema]) => <label key={name}>{fieldLabels[name] || name}{field(name, schema, input[name], (value) => setInput((values) => ({ ...values, [name]: value })), busy)}</label>)}
                {selectedImages.length > 0 && <p className="studio-note">所选图片按顺序填入参考槽位，请检查每张图片的用途。{unusedImages.length > 0 && `其中 ${unusedImages.length} 张未加入本次输入。`}</p>}
                {model.usage.references.slots.map((slot) => {
                    const index = slotIndex(slot.path);
                    const choice = choices[slot.path];
                    const record = choice?.startsWith("upload:") ? uploads.find((item) => item.id === choice.slice(7)) : undefined;
                    const canvasImage = choice?.startsWith("canvas:") ? imageNodes.find((node) => node.id === choice.slice(7)) : undefined;
                    const label = index !== undefined ? `参考图 ${index + 1}` : slot.path === "input.mask" ? "蒙版" : "源图片";
                    return <fieldset key={slot.path} disabled={busy} className="studio-reference-slot"><legend>{label}{slot.required ? " · 必填" : " · 可选"}</legend><p>{slot.description}</p>
                        <Select aria-label={`${label}选择`} showSearch optionFilterProp="label" allowClear placeholder="选择画布图片或已保存的参考图" value={choice} disabled={busy} onChange={(id) => setChoices((items) => ({ ...items, [slot.path]: id }))} options={[{ label: "画布图片", options: imageNodes.map((node) => ({ value: `canvas:${node.id}`, label: node.title })) }, { label: "已保存参考图", options: uploads.map((item) => ({ value: `upload:${item.id}`, label: `${item.name} · ${item.asset ? item.asset.expires_at * 1000 > Date.now() ? "已上传" : "已过期" : item.error ? "待核对上传" : "已保存在本机"}`, disabled: Boolean(item.asset && item.asset.expires_at * 1000 <= Date.now()) })) }]} style={{ width: "100%" }} />
                        {(canvasImage || record) && <div data-studio-reference-preview={slot.path}><StudioReferencePreview storageKey={canvasImage?.metadata?.storageKey} blob={record?.blob} name={canvasImage?.title || record!.name} identity={client.fingerprint} /></div>}
                        <div className="studio-actions"><label className="studio-upload-button"><Upload size={15} /> 选择本机图片<input type="file" aria-label={`上传${label}`} accept={model.usage.references.media_types.join(",")} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void chooseFile(slot.path, file); }} /></label></div>
                        {record?.error && <p role="status">{record.error}。再次提交会核对原上传记录。</p>}
                        {slot.weight_supported && <label>权重<InputNumber value={weights[slot.path]} onChange={(value) => setWeights((items) => ({ ...items, [slot.path]: value ?? undefined }))} /></label>}
                    </fieldset>;
                })}
                {Object.entries(parameterProperties).filter(([name]) => name !== "n" && !fixedFields.includes(name)).map(([name, schema]) => <label key={name}>{fieldLabels[name] || name}{field(name, schema, parameters[name], (value) => setParameters((values) => ({ ...values, [name]: value })), busy)}</label>)}
                <div className="studio-note">{model.usage.notes.map((note) => <p key={note}>{note}</p>)}<p>生成完成后的云端素材保留 {model.usage.output_retention_seconds / 3600} 小时；存入本机的副本独立保留。每项使用相同输入与完整规格，结果独立保留。</p></div>
            </>}
        </div>}
    </Drawer>;
}
