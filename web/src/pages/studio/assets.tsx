import { useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { Alert, App, Button, Drawer, Empty, Popconfirm, Tag } from "antd";
import { Box, Download, Image, Music2, RefreshCw, Trash2, Video } from "lucide-react";
import { saveAs } from "file-saver";
import { localResultIndex, type LocalResult } from "@/services/aggregator-journal";
import { deleteUnreferencedResult } from "@/services/studio-documents";
import { flushStudioDocuments } from "@/services/studio-persistence";
import { getStudioResult, mediaExtension, releaseStudioMedia, studioMediaUrl } from "@/services/studio-media";
import { subscribeStudioTasks } from "@/services/aggregator-engine";
import { collectMediaStorageKeys } from "@/services/file-storage";
import { useStudioStore } from "@/stores/use-studio-store";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useAssetStore } from "@/stores/use-asset-store";
import { StudioModel3dPreview } from "@/components/studio-model3d-preview";

type Entry = Omit<LocalResult, "blob">;
export default function StudioAssetsPage() {
    const { message } = App.useApp();
    const client = useStudioStore((state) => state.client);
    const identity = useStudioStore((state) => state.client || state.localIdentity);
    const [entries, setEntries] = useState<Entry[]>([]);
    const [error, setError] = useState("");
    const [preview, setPreview] = useState<{ entry: Entry; url: string }>();
    const load = useCallback(async () => {
        try { const values = await localResultIndex(); const state = useStudioStore.getState(); if ((state.client || state.localIdentity) === identity) setEntries(values.filter((item) => item.version === 1 && item.identity.fingerprint === identity?.fingerprint && item.identity.origin === identity?.origin).sort((a, b) => b.savedAt - a.savedAt)); }
        catch (reason) { setError(reason instanceof Error ? reason.message : "素材目录读取失败"); }
    }, [identity]);
    useEffect(() => { setPreview(undefined); void load(); const off = subscribeStudioTasks(() => void load()); return () => { off(); releaseStudioMedia(); }; }, [load]);
    const action = async (work: () => Promise<void>) => { try { setError(""); await work(); } catch (reason) { message.error(reason instanceof Error ? reason.message : "操作未完成"); } };
    return <main className="studio-page"><div className="studio-page-heading"><span className="li-lab">YOUR LOCAL LIBRARY</span><h1>留住每一次创作</h1><p>这里是已校验并存入当前浏览器的原件。云端到期不会主动删除这些副本；浏览器清理或设备丢失仍可能移除本机数据，请另存重要作品。</p></div>
        {!identity ? <section className="studio-card studio-empty"><Empty description="填写原服务地址与 API Key，打开对应本机素材" /><NavLink to="/config" className="studio-primary-link">连接服务</NavLink></section> : <>
            {!client && <Alert type="info" showIcon title="本机素材可离线查看" description="已保存原件可以预览和另存；生成及云端操作需要重新连接服务。" />}
            <div className="studio-actions"><Button icon={<RefreshCw size={16} />} onClick={() => void load()}>刷新目录</Button><Tag>{entries.length} 个原件 · {(entries.reduce((sum, item) => sum + item.metadata.bytes, 0) / 1024 / 1024).toFixed(2)} MiB</Tag><NavLink to="/storage" className="studio-inline-link">存储空间</NavLink></div>
            {error && <Alert type="error" showIcon title={error} />}
            <div className="studio-model-grid" style={{ marginTop: 24 }}>{entries.map((entry) => {
                const metadata = entry.metadata;
                const Icon = metadata.media_type === "model/gltf-binary" ? Box : metadata.media_type.startsWith("video/") ? Video : metadata.media_type.startsWith("audio/") ? Music2 : Image;
                return <article key={entry.id} className="studio-card studio-model-card"><div className="studio-model-type"><Icon size={28} /><span>{metadata.media_type}</span></div><h2 style={{ overflowWrap: "anywhere" }}>{metadata.id}</h2><p>{(metadata.bytes / 1024 / 1024).toFixed(2)} MiB<br />云端{metadata.expires_at * 1000 > Date.now() ? `保留至 ${new Date(metadata.expires_at * 1000).toLocaleString()}` : "已到保留期限"}<br />本机原件独立保留</p><div className="studio-actions">
                    {metadata.media_type === "model/gltf-binary" ? <StudioModel3dPreview storageKey={entry.id} name={metadata.id} bytes={metadata.bytes} /> : <Button onClick={() => void action(async () => { const url = await studioMediaUrl(entry.id); if (!url) throw new Error("本机原件无法读取"); setPreview({ entry, url }); })}>预览</Button>}
                    <Button icon={<Download size={14} />} onClick={() => void action(async () => { const result = await getStudioResult(entry.id); if (!result) throw new Error("请使用原连接读取本机原件"); saveAs(result.blob, `${metadata.id}.${mediaExtension(metadata.media_type)}`); })}>另存原件</Button>
                    <Popconfirm title="删除此浏览器的原件？" description="不会取消远端任务或延长云端期限。仍被画布或素材引用的原件会保留。" onConfirm={() => action(async () => {
                        await flushStudioDocuments();
                        const used = collectMediaStorageKeys({ projects: useCanvasStore.getState().projects, assets: useAssetStore.getState().assets });
                        if (used.has(entry.id)) throw new Error("该原件仍被画布或素材引用，请先移除引用");
                        if (!client || useStudioStore.getState().client !== client) throw new Error("连接已变化，请重新载入素材目录");
                        await deleteUnreferencedResult(entry.id, { profileId: useStudioStore.getState().activeProfileId!, origin: client.origin, fingerprint: client.fingerprint }); releaseStudioMedia(entry.id); await load();
                    })}><Button aria-label="删除本机原件" disabled={!client} icon={<Trash2 size={14} />} /></Popconfirm>
                </div></article>;
            })}</div>{!entries.length && <Empty description="在任务卡中载入产物后，会在这里保留本机原件" />}
        </>}
        <Drawer open={Boolean(preview)} title="本机原件预览" onClose={() => { if (preview) releaseStudioMedia(preview.entry.id); setPreview(undefined); }} size="large">
            {preview && <><p>{preview.entry.metadata.media_type} · 已存本机</p>{preview.entry.metadata.media_type.startsWith("image/") ? <img src={preview.url} alt="生成原件" style={{ width: "100%", objectFit: "contain" }} /> : preview.entry.metadata.media_type.startsWith("video/") ? <video src={preview.url} controls playsInline preload="metadata" style={{ width: "100%" }} /> : <audio src={preview.url} controls preload="metadata" style={{ width: "100%" }} />}<p className="studio-note">若浏览器不能解码此格式，请使用“另存原件”，在支持该格式的播放器中打开。</p></>}
        </Drawer>
    </main>;
}
