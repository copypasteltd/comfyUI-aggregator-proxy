import { useMemo, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { Alert, Button, Drawer, Empty, Input, Select, Tag } from "antd";
import { ArrowUpRight, Box, Image, Music2, RefreshCw, Video } from "lucide-react";
import { describePreset, taskLabels } from "@/services/api/aggregator/capabilities";
import type { CatalogModel } from "@/services/api/aggregator/types";
import { useStudioStore } from "@/stores/use-studio-store";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";

export default function ModelsPage() {
    const navigate = useNavigate();
    const { client, catalog, loading, error, refreshCatalog } = useStudioStore();
    const [search, setSearch] = useState("");
    const [type, setType] = useState("all");
    const [detail, setDetail] = useState<CatalogModel | null>(null);
    const models = useMemo(() => (catalog?.models || []).filter((model) => (type === "all" || model.task_type === type) && `${model.id} ${model.usage?.display_name || ""} ${model.usage?.description || ""}`.toLowerCase().includes(search.toLowerCase())), [catalog, search, type]);
    return <main className="studio-page">
        <div className="studio-page-heading"><span className="li-lab">MODELS & WORKFLOWS</span><h1>找到这次创作的模型</h1><p>规格、参考图要求和使用说明来自服务当前发布。选择完整规格组合，保留每个工作流的能力。</p></div>
        {!client ? <section className="studio-card studio-empty"><Empty description="先连接服务，再查看你有权调用的模型" /><NavLink className="studio-primary-link" to="/config">连接服务 <ArrowUpRight size={17} /></NavLink></section> : <>
            <div className="studio-filter-row"><Input.Search aria-label="搜索模型" placeholder="搜索名称、别名或说明" value={search} onChange={(event) => setSearch(event.target.value)} allowClear /><Select aria-label="任务类型" value={type} onChange={setType} options={[{ label: "全部类型", value: "all" }, ...Object.entries(taskLabels).map(([value, label]) => ({ value, label }))]} /><Button icon={<RefreshCw size={16} />} loading={loading} onClick={() => void refreshCatalog()}>刷新</Button></div>
            {error && <Alert title={error} type="error" showIcon />}
            <div className="studio-model-grid">{models.map((model) => {
                const Icon = model.task_type === "image_to_3d" ? Box : model.task_type.includes("audio") ? Music2 : model.task_type.includes("video") ? Video : Image;
                return <button className="studio-card studio-model-card" key={model.id} onClick={() => setDetail(model)}>
                    <div className="studio-model-type"><Icon size={22} /><span>{taskLabels[model.task_type] || model.task_type}</span><ArrowUpRight size={17} /></div><h2>{model.usage?.display_name || model.id}</h2><code>{model.id}</code><p>{model.usage?.description || model.unavailable_reason}</p>
                    <div className="studio-model-presets">{model.usage?.presets?.slice(0, 3).map((preset, index) => <span key={index}>{describePreset(preset)}</span>)}</div>
                    {model.unavailable_reason ? <span className="studio-model-warning">{model.unavailable_reason}</span> : <span className="studio-model-meta">{model.usage.references.exact_reference_count ? `${model.usage.references.exact_reference_count} 张参考` : model.usage.references.slots.some((slot) => slot.required) ? "需要源图片" : "文字输入"}<i /> 完成后保留 8 小时</span>}
                </button>;
            })}</div>
            {!models.length && <Empty description="当前筛选下没有可发现的模型" />}
        </>}
        <Drawer open={Boolean(detail)} onClose={() => setDetail(null)} title={detail?.usage?.display_name || detail?.id} size="large">
            {detail && <div className="studio-model-detail"><code>{detail.id}</code>{detail.unavailable_reason && <Alert title={detail.unavailable_reason} type="warning" showIcon />}<p>{detail.usage?.description}</p>
                {detail.usage && <><Button type="primary" disabled={Boolean(detail.unavailable_reason)} onClick={() => { const id = useCanvasStore.getState().createProject(detail.usage.display_name); navigate(`/canvas/${id}?model=${encodeURIComponent(detail.id)}`); }}>用此模型新建画布</Button><h3>可用规格</h3>{detail.usage.presets.map((preset, index) => <Tag key={index}>{describePreset(preset)}</Tag>)}<h3>输入要求</h3>{detail.usage.references.slots.map((slot) => <p key={slot.path}><b>{slot.required ? "必填" : "可选"} · {slot.path}</b><br />{slot.description}{slot.weight_supported ? "（支持权重）" : ""}</p>)}<h3>参数说明</h3>{detail.usage.parameter_details.map((parameter) => <p key={parameter.path}><b>{parameter.path}</b><br />{parameter.description}</p>)}<h3>使用说明</h3><ul>{detail.usage.notes.map((note) => <li key={note}>{note}</li>)}</ul><p>{detail.usage.availability_note}</p><h3>调用示例</h3><pre>{JSON.stringify(detail.usage.example_request, null, 2)}</pre></>}
            </div>}
        </Drawer>
    </main>;
}
