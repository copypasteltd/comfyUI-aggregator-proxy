import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { Alert, Button, Empty, Tag } from "antd";
import { Download, Search } from "lucide-react";
import { saveAs } from "file-saver";
import { readLocalMediaInventory, type LocalMediaInventory } from "@/services/local-media-inventory";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useAssetStore } from "@/stores/use-asset-store";

const size = (bytes: number | null) => bytes === null ? "大小无法确认" : `${bytes.toLocaleString()} B`;

export function LocalMediaInspector() {
    const [inventory, setInventory] = useState<LocalMediaInventory>(), [loading, setLoading] = useState(false), [error, setError] = useState("");
    const generation = useRef(0);
    useEffect(() => () => { generation.current++; }, []);
    const inspect = async () => {
        const current = ++generation.current;
        setLoading(true); setError(""); setInventory(undefined);
        try {
            const canvas = useCanvasStore.getState();
            const value = await readLocalMediaInventory({ projects: canvas.projects, deletedProjects: canvas.deletedProjects, assets: useAssetStore.getState().assets });
            if (current === generation.current) setInventory(value);
        } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : "本机引用检查未完成，文件保持原样"); }
        finally { if (current === generation.current) setLoading(false); }
    };
    return <section className="studio-card" style={{ marginTop: 20 }}>
        <h2>本机原件引用检查</h2>
        <p>检查普通图片、媒体原件与已保存画布、回收站、素材和历史记录的关联。检查只读取本机数据，不读取原件内容或请求生成服务，也不删除文件。</p>
        <div className="studio-actions"><Button icon={<Search size={15} />} loading={loading} onClick={() => void inspect()}>检查本机引用</Button><NavLink className="studio-inline-link" to="/canvas">备份画布作品</NavLink>
            {inventory && <Button icon={<Download size={15} />} onClick={() => saveAs(new Blob([JSON.stringify({ format: "canvas-local-media-inventory", version: 1, ...inventory }, null, 2)], { type: "application/json" }), "canvas-local-media-inventory.json")}>导出核对清单</Button>}
        </div>
        {error && <Alert type="error" showIcon style={{ marginTop: 16 }} title="引用检查未完成" description={error} />}
        {inventory && <>
            <p className="studio-note" style={{ marginTop: 16 }}>检查时间：{new Date(inventory.capturedAt).toLocaleString()}。这是分别读取各库得到的快照，其他页面可能继续编辑；未发现引用不等于可以删除。</p>
            <div className="studio-actions"><Tag>{inventory.files.length} 份普通原件</Tag><Tag>{inventory.originalBytes.toLocaleString()} B 已知原件内容</Tag><Tag>{inventory.previewRecords} 条缩略图记录</Tag><Tag color={inventory.missing.length ? "warning" : undefined}>{inventory.missing.length} 个缺失引用</Tag></div>
            {inventory.warnings.map((warning) => <Alert key={warning} style={{ marginTop: 12 }} type="warning" showIcon title={warning} />)}
            <details style={{ marginTop: 20 }}><summary>普通原件及引用位置（{inventory.files.length}）</summary>
                {!inventory.files.length ? <Empty description="未发现普通图片或媒体原件" /> : inventory.files.map((file) => <article key={`${file.store}:${file.key}`} style={{ padding: "16px 0", borderBottom: "1px solid var(--line)" }}>
                    <p className="studio-break"><strong>{file.key}</strong></p><p className="studio-note studio-break">{file.store} · {size(file.bytes)} · {file.mediaType || "类型未声明"}</p>
                    <p>{file.references.length ? `已找到引用：${file.references.join("、")}` : "引用尚未确认：可能用于其他页面、未保存编辑或暂存导入，原件保留。"}</p>
                </article>)}
            </details>
            <details style={{ marginTop: 20 }}><summary>有引用但缺少普通原件（{inventory.missing.length}）</summary>
                {!inventory.missing.length ? <p className="studio-note">本次扫描未发现缺失的普通原件；仍需实际打开或导出重要作品确认。</p> : inventory.missing.map((file) => <article key={file.key} style={{ padding: "16px 0" }}><p className="studio-break"><strong>{file.key}</strong></p><p>引用位置：{file.references.join("、")}。缩略图不能替代原件；请从已有备份恢复，勿清空原文档。</p></article>)}
            </details>
            <p className="studio-note" style={{ marginTop: 20 }}>生成原件仍在“本机素材”管理。核对清单包含本机文件标识、字节数和引用分类，不包含原件内容；请按项目资料保管。跨库自动回收尚未启用。</p>
        </>}
    </section>;
}
