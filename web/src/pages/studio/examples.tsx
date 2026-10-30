import { useRef, useState } from "react";
import { Alert, Button, Tag } from "antd";
import { AudioLines, Box, Clapperboard, Image, ArrowUpRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { nanoid } from "nanoid";
import { exampleModel, studioExamples, type StudioExample } from "@/lib/studio-examples";
import { describePreset, taskLabels } from "@/services/api/aggregator/capabilities";
import { firstAllowedPreset } from "@/services/api/aggregator/composer";
import { flushStudioDocuments } from "@/services/studio-persistence";
import { newManagedMediaKey } from "@/services/studio-local-media";
import { setImageBlob } from "@/services/image-storage";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useStudioStore } from "@/stores/use-studio-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import "./examples.css";

const sampleFiles = ["robot-subject.png", "robot-style.png"];
const sampleUrl = (name: string) => `${import.meta.env.BASE_URL}examples/${name}`;

export default function StudioExamplesPage() {
    const navigate = useNavigate();
    const catalog = useStudioStore((state) => state.catalog);
    const hydrated = useCanvasStore((state) => state.hydrated);
    const [busy, setBusy] = useState<string>(), [error, setError] = useState("");
    const opening = useRef(false);
    const open = async (example: StudioExample) => {
        if (!hydrated || opening.current) return;
        opening.current = true; setBusy(example.id); setError("");
        try {
            await flushStudioDocuments();
            const originals = await Promise.all(sampleFiles.slice(0, example.references).map(async (name) => {
                const response = await fetch(sampleUrl(name), { credentials: "omit" });
                if (!response.ok || response.headers.get("content-type")?.split(";")[0] !== "image/png") throw new Error("示例图片未能读取，请刷新页面后重试");
                const blob = await response.blob(), bitmap = await createImageBitmap(blob);
                try { return { blob, width: bitmap.width, height: bitmap.height }; }
                finally { bitmap.close(); }
            }));
            const nodes: CanvasNodeData[] = [];
            for (const [index, original] of originals.entries()) {
                const key = newManagedMediaKey("image");
                await setImageBlob(key, original.blob);
                nodes.push({ id: nanoid(), type: CanvasNodeType.Image, title: index ? "参考 B · 机器人造型" : "参考 A · 机器人主体", position: { x: 0, y: index * 420 }, width: 250, height: 250 * original.height / original.width, metadata: { storageKey: key, mimeType: "image/png", bytes: original.blob.size, naturalWidth: original.width, naturalHeight: original.height, status: "success", studioExampleId: example.id } });
            }
            nodes.push({ id: nanoid(), type: CanvasNodeType.Text, title: "创作描述", position: { x: example.references ? 320 : 0, y: 0 }, width: 350, height: 300, metadata: { content: example.prompt || example.steps, status: "success", studioExampleId: example.id } });
            const id = useCanvasStore.getState().importProject({ title: `${example.title} · 案例`, nodes, connections: [], viewport: { x: 40, y: 40, k: 0.8 }, backgroundMode: "dots" });
            await flushStudioDocuments();
            navigate(`/canvas/${id}?example=${encodeURIComponent(example.id)}`);
        } catch (reason) { setError(reason instanceof Error ? reason.message : "案例尚未打开，请重试"); }
        finally { opening.current = false; setBusy(undefined); }
    };
    return <main className="studio-page studio-examples">
        <header className="studio-page-heading"><span className="li-lab">START CREATING</span><h1>从一个案例开始</h1><p>选择创作方向，打开带有描述和参考素材的独立画布。你可以自由修改；确认表单并点击“创建任务”后，才会调用生成服务。</p></header>
        {error && <Alert role="alert" type="error" showIcon title="案例尚未打开" description={error} />}
        <p className="studio-note">示例图片用于演示输入，不是本次生成效果。模型、规格和参考角色来自当前连接；未连接时也可打开案例画布。</p>
        <section className="studio-examples-grid" aria-label="预设案例">
            {studioExamples.map((example) => {
                const model = catalog && exampleModel(example, catalog);
                const Icon = example.taskType === "text_to_audio" ? AudioLines : example.taskType === "image_to_3d" ? Box : example.taskType.includes("video") ? Clapperboard : Image;
                return <article key={example.id} className="studio-card studio-example-card" data-example-id={example.id}>
                    <div className="studio-example-cover" aria-label="案例示意">
                        {example.references ? sampleFiles.slice(0, example.references).map((file, index) => <img key={file} src={sampleUrl(file)} alt={`示例参考 ${index ? "B" : "A"}`} loading="lazy" />) : <Icon size={56} strokeWidth={1.2} />}
                        <Tag>{taskLabels[example.taskType]}</Tag>
                    </div>
                    <h2>{example.title}</h2><p>{example.description}</p><small>{example.steps}</small>
                    <div className="studio-example-availability" role="status">{model && catalog ? <><b>{model.usage.display_name}</b><span>{describePreset(model.usage.presets[firstAllowedPreset(model, catalog.policy)])}</span></> : catalog ? "当前 Key 未发现匹配模型，仍可打开并编辑画布。" : "连接服务后，自动匹配已授权模型与合法规格。"}</div>
                    <Button aria-label={`打开${example.title}案例`} icon={<ArrowUpRight size={16} />} disabled={!hydrated || Boolean(busy)} loading={busy === example.id} onClick={() => void open(example)}>打开案例</Button>
                </article>;
            })}
        </section>
    </main>;
}
