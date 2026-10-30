import { useEffect, useState } from "react";
import { App, Button, Popconfirm } from "antd";
import { Box, Download, RefreshCw, Sparkles } from "lucide-react";
import { saveAs } from "file-saver";
import { registerNodeDefinitions } from "@/lib/canvas/node-registry";
import type { CanvasNodeContext } from "@/types/canvas-plugin";
import { journalGet, type Intent } from "@/services/aggregator-journal";
import { cancelIntent, jobStatusLabels, lookupIntent, refreshIntent, saveResult, stopLocalWait, submitIntent, subscribeStudioTasks } from "@/services/aggregator-engine";
import { terminalStatuses } from "@/services/api/aggregator/types";
import { getMediaBlob } from "@/services/file-storage";
import { useStudioStore } from "@/stores/use-studio-store";
import { StudioModel3dPreview } from "@/components/studio-model3d-preview";

function TaskContent({ ctx }: { ctx: CanvasNodeContext }) {
    const [intent, setIntent] = useState<Intent>();
    const [busy, setBusy] = useState(false);
    const client = useStudioStore((state) => state.client);
    const { message } = App.useApp();
    useEffect(() => {
        let active = true;
        const load = async () => {
            const value = ctx.node.metadata?.studioIntentId ? await journalGet("intents", ctx.node.metadata.studioIntentId) : undefined;
            if (active) setIntent(value?.identity.fingerprint === client?.fingerprint && value?.identity.origin === client?.origin ? value : undefined);
        };
        void load().catch(() => {}); const off = subscribeStudioTasks(() => void load().catch(() => {}));
        return () => { active = false; off(); };
    }, [ctx.node.metadata?.studioIntentId, client]);
    const act = async (work: () => Promise<unknown>) => {
        if (busy) return; setBusy(true);
        try { await work(); } catch (reason) { message.error(reason instanceof Error ? reason.message : "操作未确认"); }
        finally { setBusy(false); }
    };
    return <div className="studio-node-card" style={{ color: ctx.theme.node.text }} onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        <Sparkles size={26} /><strong>{intent?.job ? jobStatusLabels[intent.job.status] || intent.job.status : intent?.stage === "prepared" && !intent.recovered ? "已保存，待提交" : "提交记录待核对"}</strong>
        {intent?.batch && <small>批次第 {intent.batch.index + 1}/{intent.batch.total} 项</small>}
        <small>{intent?.job?.id || "请连接创建此任务的原服务与 Key"}</small>
        {intent?.error && <p role="status">{intent.error}{intent.pollPaused ? "。自动查询已暂停，可手动继续。" : ""}</p>}
        {intent?.job?.error && <p role="status">{intent.job.error.message || intent.job.error.code}</p>}
        {intent && <div className="studio-actions">
            {intent.stage === "submitting" && <Button size="small" onClick={() => { stopLocalWait(intent.id); message.info("已停止本页等待，远端任务状态仍需核对"); }}>停止本页等待</Button>}
            <Button size="small" loading={busy} icon={<RefreshCw size={13} />} onClick={() => void act(() => intent.job ? refreshIntent(intent.id) : intent.stage === "prepared" && !intent.recovered ? submitIntent(intent.id) : lookupIntent(intent.id))}>{intent.job ? "继续查询" : intent.stage === "prepared" && !intent.recovered ? "提交已保存请求" : "只读核对原提交"}</Button>
            {intent.job && !terminalStatuses.has(intent.job.status) && <Popconfirm title="请求取消原任务？" description="等待服务端确认后才显示已取消。" onConfirm={() => act(() => cancelIntent(intent.id))}><Button size="small" loading={busy}>取消</Button></Popconfirm>}
            {intent.job?.status === "succeeded" && intent.job.artifacts?.map((artifact) => <Button size="small" key={artifact.id} type="primary" loading={busy} onClick={() => void act(() => saveResult(intent.id, artifact.id))}>载入产物</Button>)}
        </div>}
        {intent?.job?.status === "succeeded" && <small>云端产物有保留期限，请及时载入或保存原件。</small>}
    </div>;
}

function Model3dContent({ ctx }: { ctx: CanvasNodeContext }) {
    const { message } = App.useApp();
    return <div className="studio-node-card" onPointerDown={(event) => event.stopPropagation()}>
        <Box size={56} strokeWidth={1} /><strong>3D 模型 · GLB</strong><span>{((ctx.node.metadata?.bytes || 0) / 1024 / 1024).toFixed(2)} MiB · 已存本机</span>
        {ctx.node.metadata?.storageKey && <StudioModel3dPreview storageKey={ctx.node.metadata.storageKey} name={ctx.node.metadata.studioContentId || ctx.node.id} bytes={ctx.node.metadata.bytes || 0} />}
        <Button icon={<Download size={15} />} onClick={() => void (async () => {
            const blob = ctx.node.metadata?.storageKey ? await getMediaBlob(ctx.node.metadata.storageKey) : undefined;
            if (!blob || blob.type !== "model/gltf-binary") throw new Error("GLB 原件不可读；服务产物需使用原服务和 Key，导入的离线快照需包含文件");
            saveAs(blob, `${ctx.node.metadata?.studioContentId || ctx.node.id}.glb`);
        })().catch((reason) => message.error(reason instanceof Error ? reason.message : "原件读取失败"))}>保存 GLB 原件</Button>
    </div>;
}

// Audited built-ins are registered directly. Managed mode never loads plugin source.
registerNodeDefinitions([
    { type: "studio:task", title: "生成任务", icon: <Sparkles size={16} />, defaultSize: { width: 340, height: 260 }, showInCreateMenu: false, hidePanel: true, Content: TaskContent },
    { type: "studio:model3d", title: "3D 模型", icon: <Box size={16} />, defaultSize: { width: 340, height: 280 }, showInCreateMenu: false, hidePanel: true, Content: Model3dContent },
]);
