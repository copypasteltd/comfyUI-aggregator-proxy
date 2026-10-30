import { useEffect, useState, type ComponentType } from "react";
import { App, Alert, Button, Modal, Spin } from "antd";
import { Box, Download } from "lucide-react";
import { saveAs } from "file-saver";
import { getMediaBlob } from "@/services/file-storage";
import { useStudioStore } from "@/stores/use-studio-store";
import { inspectStudioGlb } from "@/lib/studio-glb";

export function StudioModel3dPreview({ storageKey, name, bytes }: { storageKey: string; name: string; bytes: number }) {
    const identity = useStudioStore((state) => state.client || state.localIdentity);
    const { message } = App.useApp();
    const [request, setRequest] = useState<{ identity: typeof identity; storageKey: string }>();
    const open = Boolean(request && request.identity === identity && request.storageKey === storageKey);
    const [error, setError] = useState("");
    const [loaded, setLoaded] = useState<{ request: typeof request; data: ArrayBuffer; notes: string[]; Viewer: ComponentType<{ data: ArrayBuffer }> }>();
    useEffect(() => {
        let active = true;
        setLoaded(undefined); setError("");
        if (open) void (async () => {
            const blob = await getMediaBlob(storageKey);
            if (!blob || blob.type !== "model/gltf-binary") throw new Error("GLB 原件不可读；请连接原服务和 Key，或导入含原件的离线快照");
            if (!active) return;
            const data = await blob.arrayBuffer(), notes = inspectStudioGlb(data);
            if (!active) return;
            const { default: Viewer } = await import("./studio-model3d-viewer");
            if (active) setLoaded({ request, data, notes, Viewer });
        })().catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "GLB 读取失败，可另存原件检查"); });
        return () => { active = false; };
    }, [open, request, storageKey, identity]);
    return <>
        <Button icon={<Box size={15} />} onClick={() => setRequest({ identity, storageKey })}>交互预览</Button>
        <Modal open={open} title="3D 原件预览" width={960} style={{ top: 24, paddingBottom: 24 }} styles={{ body: { maxHeight: "calc(100dvh - 220px)", overflowY: "auto" } }} destroyOnHidden onCancel={() => setRequest(undefined)} footer={<Button icon={<Download size={15} />} onClick={() => void (async () => {
            const blob = await getMediaBlob(storageKey);
            const state = useStudioStore.getState();
            if (!blob || blob.type !== "model/gltf-binary" || (state.client || state.localIdentity) !== identity) throw new Error("原件不可读或连接已变化，请重新打开预览");
            saveAs(blob, `${name}.glb`);
        })().catch((reason) => message.error(reason instanceof Error ? reason.message : "原件读取失败"))}>保存 GLB 原件</Button>}>
            <p>{(bytes / 1024 / 1024).toFixed(2)} MiB · 本机原件 · 关闭预览会释放三维资源</p>
            <p className="studio-note" style={{ marginBottom: 16 }}>拖动旋转、滚轮缩放；触屏单指旋转、双指缩放或平移。大型模型可能占用较多内存，可随时关闭或另存原件。</p>
            {open && (error ? <Alert type="warning" showIcon title="暂时无法交互预览" description={error} /> : loaded && loaded.request === request ? <>
                {loaded.notes.length > 0 && <Alert type="info" title={`未展示的可选扩展：${loaded.notes.join("、")}`} />}
                <loaded.Viewer data={loaded.data} />
            </> : <Spin tip="正在读取本机 GLB"><div style={{ height: 240 }} /></Spin>)}
        </Modal>
    </>;
}
