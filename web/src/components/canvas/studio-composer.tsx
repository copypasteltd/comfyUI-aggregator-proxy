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
import { buildComposerRequest, firstAllowedPreset, suggestComposerModel, type ReferenceChoice } from "@/services/api/aggregator/composer";
import { assetKindLabels, composerSlots, contextReason, inputKind, slotAccepts, structuredInputFields, type ComposerSlot } from "@/services/api/aggregator/inputs";
import type { CatalogModel, JsonObject, RemoteJob } from "@/services/api/aggregator/types";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import { getImageBlob } from "@/services/image-storage";
import { getMediaBlob } from "@/services/file-storage";
import StudioReferencePreview from "./studio-reference-preview";
import { exampleModel } from "@/lib/studio-examples";

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
    const [texts, setTexts] = useState<Record<string, string>>({});
    const [parentId, setParentId] = useState("");
    const [parent, setParent] = useState<RemoteJob>();
    const [parentOptions, setParentOptions] = useState<RemoteJob[]>([]);
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
    const sourceNodes = nodes.filter((node) => node.metadata?.storageKey && (node.type === CanvasNodeType.Image || node.type === CanvasNodeType.Video || node.type === CanvasNodeType.Audio || node.metadata.mimeType === "model/gltf-binary"));
    const selectedSources = draft?.sourceIds.map((id) => sourceNodes.find((node) => node.id === id)).filter((node): node is CanvasNodeData => Boolean(node)) || [];
    const nodeMatches = (slot: ComposerSlot, node: CanvasNodeData) => {
        const kind = inputKind(node.metadata?.mimeType || "") || (node.type === CanvasNodeType.Image ? "image" : node.type === CanvasNodeType.Video ? "video" : node.type === CanvasNodeType.Audio ? "audio" : undefined);
        return kind === slot.kind && (!node.metadata?.mimeType || slot.mimeTypes.includes(node.metadata.mimeType));
    };
    const initialChoices = (selected?: CatalogModel) => {
        const used = new Set<string>(), entries: [string, string][] = [];
        for (const slot of selected ? composerSlots(selected.usage).filter((slot) => slot.path !== "input.mask") : []) {
            const source = selectedSources.find((node) => !used.has(node.id) && nodeMatches(slot, node));
            if (source) { used.add(source.id); entries.push([slot.path, `canvas:${source.id}`]); }
        }
        return Object.fromEntries(entries);
    };
    useEffect(() => {
        if (!draft) { initializedDraft.current = undefined; return; }
        if (initializedDraft.current === draft.id) return;
        if (!catalog) { setModelId(""); return; }
        const imageCount = selectedSources.filter((node) => node.type === CanvasNodeType.Image).length;
        const suggested = draft.taskType ? exampleModel({ taskType: draft.taskType, references: imageCount }, catalog) : suggestComposerModel(models, catalog.policy, draft.preferredModel, draft.media, imageCount);
        setModelId(suggested?.id || ""); setPresetIndex(suggested && catalog ? firstAllowedPreset(suggested, catalog.policy) : -1); setCount(1); setInput({ prompt: draft.prompt }); setParameters({}); setTexts({}); setParentId(""); setParent(undefined); setChoices(initialChoices(suggested)); setCanvasSources({}); setWeights({}); setError("");
        initializedDraft.current = draft.id;
    // Initialize once per explicit creation, after a restoring catalog becomes available.
    // A later catalog refresh must not replace the user's model, prompt or references.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [draft?.id, catalog]);
    useEffect(() => {
        let active = true;
        const load = async () => {
            try {
                const [records, tasks] = await Promise.all([journalList("uploads"), journalList("intents")]);
                if (active) {
                    setUploads(records.filter((item) => item.version === 1 && item.identity.fingerprint === client?.fingerprint && item.identity.origin === client?.origin));
                    setParentOptions(tasks.filter((item) => item.identity.fingerprint === client?.fingerprint && item.identity.origin === client?.origin && item.job?.status === "succeeded").map((item) => item.job!));
                }
            } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : "无法读取原素材记录"); }
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
        const slot = composerSlots(model.usage).find((item) => item.path === path);
        if (!slot) throw new Error("当前模型的输入槽已变化，请重新选择");
        const record = await stageUpload(file, model, slot);
        setUploads((items) => [...items.filter((item) => item.id !== record.id), record]); setChoices((items) => ({ ...items, [path]: `upload:${record.id}` }));
    });
    const verifyParent = async () => {
        if (!model || !client || !/^job_[a-f0-9]{32}$/.test(parentId.trim())) throw new Error("请填写有效的父任务编号");
        const result = await client.job(parentId.trim());
        if (useStudioStore.getState().client?.fingerprint !== client.fingerprint) throw new Error("连接已切换，请重新核对父任务");
        const reason = contextReason(model, result.data);
        if (reason) throw new Error(reason);
        setParent(result.data);
        return result.data.id;
    };
    const submit = () => act(async () => {
        if (!model || !draft || !client) throw new Error("请先连接服务并选择模型");
        if (!Number.isSafeInteger(count) || count < 1 || count > MAX_GENERATION_COUNT) throw new Error("请选择画布支持的任务数量");
        const restriction = catalog && presetPolicyReason(model, model.usage.presets[presetIndex], catalog.policy);
        if (restriction) throw new Error(restriction);
        const fingerprint = client.fingerprint;
        const assertConnection = () => { if (useStudioStore.getState().client?.fingerprint !== fingerprint) throw new Error("连接已切换，请使用原连接继续"); };
        const allowed = schemaProperties(model.usage, "input");
        const values = Object.fromEntries(Object.entries(input).filter(([key]) => key in allowed && !structuredInputFields.includes(key)));
        const slots = composerSlots(model.usage);
        const parentJobId = model.usage.context?.required ? parentId.trim() : undefined;
        // Validate the full form before any remote upload. Temporary IDs are only
        // local bindings for the schema; createJob always receives real asset IDs.
        const previewReferences = Object.fromEntries(slots.filter((slot) => choices[slot.path]).map((slot, index) => [slot.path, { assetId: `asset_preflight_${index}`, weight: weights[slot.path] }]));
        const issues = await validateJob(model, buildComposerRequest(model, presetIndex, { input: values, parameters, references: previewReferences, texts, parentJobId }));
        if (issues.length) throw new Error(issues.join("；"));
        if (model.usage.context?.required) await verifyParent();
        const staged: Record<string, InputUpload> = {};
        const sourceIds = new Set(draft.sourceIds.filter((id) => nodes.some((node) => node.id === id && node.type === CanvasNodeType.Text)));
        // Stage and verify every selected original before uploading the first one.
        for (const slot of slots) {
            assertConnection();
            const selected = choices[slot.path];
            if (!selected) continue;
            let record: InputUpload | undefined;
            if (selected.startsWith("canvas:")) {
                const source = sourceNodes.find((node) => node.id === selected.slice(7) && nodeMatches(slot, node));
                const blob = source?.metadata?.storageKey ? await (source.type === CanvasNodeType.Image ? getImageBlob(source.metadata.storageKey) : getMediaBlob(source.metadata.storageKey)) : null;
                if (!blob) throw new Error("所选画布素材的本机原件不可读取，请重新选择");
                record = await stageUpload(new File([blob], source!.title || "reference", { type: blob.type }), model, slot);
                sourceIds.add(source!.id);
                setCanvasSources((items) => ({ ...items, [record!.id]: source!.id }));
                setUploads((items) => [...items.filter((item) => item.id !== record!.id), record!]);
                setChoices((items) => ({ ...items, [slot.path]: `upload:${record!.id}` }));
            } else if (selected.startsWith("upload:")) {
                record = uploads.find((item) => item.id === selected.slice(7));
                if (record && canvasSources[record.id]) sourceIds.add(canvasSources[record.id]);
                if (record && !slotAccepts(slot, record.blob, record.kind || "image")) throw new Error(`${slot.label}的素材类型或大小不符合当前槽位`);
                if (record) await validateReferenceOriginal(record.blob, model, slot);
            }
            if (!record) throw new Error("找不到所选素材记录，请重新选择");
            if (record.identity.fingerprint !== fingerprint || record.identity.origin !== client.origin) throw new Error("所选素材属于其他连接，请重新选择");
            if (record.asset && record.asset.expires_at * 1000 <= Date.now()) throw new Error("所选素材已过期，请明确选择新的素材");
            staged[slot.path] = record;
        }
        const references: Record<string, ReferenceChoice | undefined> = {};
        for (const slot of slots) {
            assertConnection();
            const record = staged[slot.path];
            if (!record) continue;
            const uploaded = await uploadReference(record.id);
            if (!uploaded.asset) throw new Error("参考素材上传尚未确认");
            references[slot.path] = { assetId: uploaded.asset.id, weight: weights[slot.path] };
        }
        if (useStudioStore.getState().client?.fingerprint !== fingerprint) throw new Error("连接已切换，请使用原连接继续");
        const request = buildComposerRequest(model, presetIndex, { input: values, parameters, references, texts, parentJobId });
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
    const unusedSources = selectedSources.filter((node) => !selectedCanvasIds.has(node.id) && !Object.values(choices).some((value) => value?.startsWith("upload:") && canvasSources[value.slice(7)] === node.id));
    return <Drawer open={Boolean(draft)} onClose={() => !busy && close()} title={<span><Sparkles size={17} /> 新建创作</span>} size="large" className="studio-composer" destroyOnHidden={false} footer={<div className="studio-actions"><Button onClick={close} disabled={busy}>关闭</Button><Button type="primary" icon={<Sparkles size={16} />} loading={busy} disabled={!model || !preset || Boolean(presetRestriction) || !catalog?.releaseGuard} onClick={() => void submit()}>提交 {count} 项生成任务</Button></div>}>
        {!client ? <Alert showIcon type="info" title="连接你的生成服务" description={<NavLink to="/config" onClick={close}>填写地址及业务 API Key 后开始创作</NavLink>} /> : <div className="studio-form">
            {!catalog?.releaseGuard && <Alert showIcon type="warning" title="当前服务仅可查看模型" description="该 API 未声明提交所需的发布校验能力，请连接已支持此能力的原服务。" />}
            {error && <Alert type="error" showIcon title="操作尚未完成" description={error} />}
            <label>模型<Select aria-label="生成模型" showSearch optionFilterProp="label" value={modelId || undefined} onChange={(id) => { const selected = models.find((item) => item.id === id); setModelId(id); setPresetIndex(selected && catalog ? firstAllowedPreset(selected, catalog.policy) : -1); setParameters({}); setTexts({}); setParentId(""); setParent(undefined); setChoices(initialChoices(selected)); setWeights({}); setError(""); }} disabled={busy} options={models.map((item) => { const blocked = !item.unavailable_reason && catalog && firstAllowedPreset(item, catalog.policy) < 0; return { value: item.id, disabled: Boolean(item.unavailable_reason || blocked), label: `${taskLabels[item.task_type]} · ${item.usage?.display_name || item.id}${blocked ? " · 当前 Key 无可用规格" : ""}` }; })} /></label>
            {!model && <Alert type="info" showIcon title="请选择可用模型" description="当前创作类型没有已授权的可用规格时，可以选择其他模型，或返回模型页刷新目录。" />}
            {model && <>
                <p>{model.usage.description}</p>
                <label>完整规格<Select aria-label="完整规格" value={preset ? presetIndex : undefined} onChange={(index) => { setPresetIndex(index); setParameters({}); }} disabled={busy} options={model.usage.presets.map((preset, index) => { const restriction = catalog && presetPolicyReason(model, preset, catalog.policy); return { value: index, disabled: Boolean(restriction), label: `${describePreset(preset)}${restriction ? ` · ${restriction}` : ""}` }; })} /></label>
                {presetRestriction && <Alert type="warning" showIcon title={presetRestriction} description="请重新选择当前 Key 获准使用的规格。" />}
                <label>任务数量<InputNumber aria-label="任务数量" value={count} min={1} max={MAX_GENERATION_COUNT} precision={0} disabled={busy} onChange={(value) => setCount(value ?? 1)} style={{ width: "100%" }} /></label>
                {count > 1 && <p className="studio-note">将创建 {count} 项独立任务，分别排队并占用额度；固定种子会重复使用。中途提交未确认时暂停，已受理项继续，未发送项可在任务页继续。</p>}
                {Object.entries(inputProperties).filter(([name]) => !structuredInputFields.includes(name)).map(([name, schema]) => <label key={name}>{fieldLabels[name] || name}{field(name, schema, input[name], (value) => setInput((values) => ({ ...values, [name]: value })), busy)}</label>)}
                {(model.usage.text_slots || []).map((slot) => <label key={slot.name}>{slot.label}{slot.required ? " · 必填" : " · 可选"}<Input.TextArea aria-label={slot.label} data-studio-text={slot.name} disabled={busy} value={texts[slot.name] || ""} onChange={(event) => setTexts((values) => ({ ...values, [slot.name]: event.target.value }))} autoSize={{ minRows: 3 }} count={{ show: true, max: slot.max_length, strategy: (value) => Array.from(value).length }} /><span className="studio-note">{slot.description}</span></label>)}
                {model.usage.context?.required && <fieldset className="studio-reference-slot" disabled={busy}><legend>续拍父任务 · 必填</legend><p>选择原连接中已成功的任务，或填写任务编号。提交前会重新检查上下文是否兼容及到期。</p><Select aria-label="选择续拍父任务" allowClear value={parentOptions.some((job) => job.id === parentId) ? parentId : undefined} placeholder="选择本机记录中的成功任务" onChange={(value) => { setParentId(value || ""); setParent(undefined); }} options={parentOptions.filter((job) => !contextReason(model, job)).map((job) => ({ value: job.id, label: `${job.model} · ${job.id}` }))} /><Input aria-label="父任务编号" value={parentId} onChange={(event) => { setParentId(event.target.value); setParent(undefined); }} placeholder="job_…" /><Button disabled={busy || !parentId} onClick={() => void act(async () => { await verifyParent(); })}>核对父任务</Button>{parent?.id === parentId && <p role="status">父任务兼容，上下文到期时间：{new Date(parent.context!.expires_at! * 1000).toLocaleString()}</p>}</fieldset>}
                {selectedSources.length > 0 && <p className="studio-note">所选素材按类型和顺序填入槽位，请检查每项用途。{unusedSources.length > 0 && `其中 ${unusedSources.length} 项未加入本次输入。`}</p>}
                {composerSlots(model.usage).map((slot) => {
                    const choice = choices[slot.path];
                    const record = choice?.startsWith("upload:") ? uploads.find((item) => item.id === choice.slice(7)) : undefined;
                    const canvasSource = choice?.startsWith("canvas:") ? sourceNodes.find((node) => node.id === choice.slice(7)) : undefined;
                    const label = slot.label;
                    return <fieldset key={slot.path} data-studio-slot={slot.path} disabled={busy} className="studio-reference-slot"><legend>{label}{slot.required ? " · 必填" : " · 可选"}</legend><p>{slot.description}</p><p className="studio-note">{assetKindLabels[slot.kind]} · {slot.mimeTypes.join("、")} · 最大 {(slot.maxBytes / 1024 / 1024).toLocaleString()} MiB</p>
                        <Select aria-label={`${label}选择`} showSearch optionFilterProp="label" allowClear placeholder="选择画布素材或已保存的素材" value={choice} disabled={busy} onChange={(id) => setChoices((items) => ({ ...items, [slot.path]: id }))} options={[{ label: "画布素材", options: sourceNodes.filter((node) => nodeMatches(slot, node)).map((node) => ({ value: `canvas:${node.id}`, label: node.title })) }, { label: "已保存素材", options: uploads.filter((item) => slotAccepts(slot, item.blob, item.kind || "image")).map((item) => ({ value: `upload:${item.id}`, label: `${item.name} · ${item.asset ? item.asset.expires_at * 1000 > Date.now() ? "已上传" : "已过期" : item.error ? "待核对上传" : "已保存在本机"}`, disabled: Boolean(item.asset && item.asset.expires_at * 1000 <= Date.now()) })) }]} style={{ width: "100%" }} />
                        {(canvasSource || record) && <div data-studio-reference-preview={slot.path}><StudioReferencePreview storageKey={canvasSource?.metadata?.storageKey} blob={record?.blob} name={canvasSource?.title || record!.name} identity={client.fingerprint} kind={slot.kind} /></div>}
                        <div className="studio-actions"><label className="studio-upload-button"><Upload size={15} /> 选择本机{assetKindLabels[slot.kind]}<input type="file" aria-label={`上传${label}`} accept={[...slot.mimeTypes, ...(slot.kind === "model" ? [".glb"] : [])].join(",")} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void chooseFile(slot.path, file); }} /></label></div>
                        {record?.error && <p role="status">{record.error}。再次提交会核对原上传记录。</p>}
                        {slot.weightSupported && <label>权重<InputNumber value={weights[slot.path]} onChange={(value) => setWeights((items) => ({ ...items, [slot.path]: value ?? undefined }))} /></label>}
                    </fieldset>;
                })}
                {Object.entries(parameterProperties).filter(([name]) => name !== "n" && !fixedFields.includes(name)).map(([name, schema]) => <label key={name}>{fieldLabels[name] || name}{field(name, schema, parameters[name], (value) => setParameters((values) => ({ ...values, [name]: value })), busy)}</label>)}
                <div className="studio-note">{model.usage.output.audio_policy && <p>声音策略：{model.usage.output.audio_policy.description}</p>}{model.usage.context?.produces && <p>此模型可产出续拍上下文，成功后可供兼容模型在 {model.usage.context.retention_seconds / 3600} 小时内使用。</p>}{model.usage.notes.map((note) => <p key={note}>{note}</p>)}<p>生成完成后的云端素材保留 {model.usage.output_retention_seconds / 3600} 小时；存入本机的副本独立保留。每项使用相同输入与完整规格，结果独立保留。</p></div>
            </>}
        </div>}
    </Drawer>;
}
