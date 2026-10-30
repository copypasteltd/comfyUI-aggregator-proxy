import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Alert, App, Button, ConfigProvider, Empty, Popconfirm, Tag } from "antd";
import zhCN from "antd/es/locale/zh_CN";
import { ArrowLeft, Download, RefreshCw, Trash2 } from "lucide-react";
import { saveAs } from "file-saver";
import "antd/dist/reset.css";
import "./styles/globals.css";
import "./styles/studio.css";
import { getAntThemeConfig } from "./lib/app-theme";
import { editorProbes, probeStudioEditor, subscribeEditorProbes, validEditorClaim } from "./services/studio-editor-session";
import { deleteUnusedLocalMedia, readCleanupOriginal, readCleanupSnapshot, type CleanupFile, type CleanupSnapshot } from "./services/studio-local-media-cleanup";

const bytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 ** 2).toFixed(2)} MiB`;

function MaintenancePage() {
    const [snapshot, setSnapshot] = useState<CleanupSnapshot>();
    const [probes, setProbes] = useState(editorProbes);
    const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false);
    const [error, setError] = useState(""), [notice, setNotice] = useState("");
    const refreshRevision = useRef(0);
    const refresh = useCallback(async () => {
        const revision = ++refreshRevision.current;
        setLoading(true);
        try {
            const next = await readCleanupSnapshot();
            if (revision !== refreshRevision.current) return;
            setSnapshot(next); setError("");
            next.editors.forEach(probeStudioEditor);
        } catch (reason) { if (revision === refreshRevision.current) setError(reason instanceof Error ? reason.message : "检查未完成，文件继续保留"); }
        finally { if (revision === refreshRevision.current) setLoading(false); }
    }, []);
    useEffect(() => {
        const released = new Set(editorProbes().filter((probe) => probe.state === "released").map((probe) => probe.id));
        void refresh();
        const unsubscribe = subscribeEditorProbes(() => {
            const next = editorProbes(); setProbes(next);
            const changed = next.filter((probe) => probe.state === "released" && !released.has(probe.id));
            changed.forEach((probe) => released.add(probe.id));
            if (changed.length) void refresh();
        });
        return () => { ++refreshRevision.current; unsubscribe(); };
    }, [refresh]);
    const remove = async (files: CleanupFile[]) => {
        if (busy) return;
        setBusy(true); setError(""); setNotice("");
        try {
            const deleted = await deleteUnusedLocalMedia(files);
            setNotice(`已清理 ${deleted.files} 份本机原件，内容合计 ${bytes(deleted.bytes)}。浏览器实际占用可能稍后更新。`);
            await refresh();
        } catch (reason) { setError(reason instanceof Error ? reason.message : "未删除任何原件，请重新检查"); }
        finally { setBusy(false); }
    };
    const download = async (file: CleanupFile) => {
        setBusy(true); setError("");
        try {
            const extension = ({ "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "video/mp4": "mp4", "video/webm": "webm", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg", "model/gltf-binary": "glb" } as Record<string, string>)[file.mimeType] || "bin";
            saveAs(await readCleanupOriginal(file.id), `${file.id.replace(/:/g, "-")}.${extension}`);
        }
        catch (reason) { setError(reason instanceof Error ? reason.message : "备份未完成"); }
        finally { setBusy(false); }
    };
    const candidates = snapshot?.files.filter((file) => !file.references.length) || [];
    return <div className="studio-shell" style={{ minHeight: "100dvh", height: "auto" }}>
        <main className="studio-page" style={{ width: "100%" }}>
            <div className="studio-actions"><a className="studio-inline-link" href={`${import.meta.env.BASE_URL}storage`}><ArrowLeft size={16} /> 返回画布</a><span className="li-lab">LOCAL ORIGINALS</span></div>
            <div className="studio-page-heading" style={{ marginTop: 28 }}><h1>本机安全清理</h1><p>新版上传与导入的原件在这里手动回收。先保存并离开其他画布页面，再检查引用。建议先下载重要原件；删除后无法从本机恢复。</p></div>
            <Alert type="info" showIcon title="保护画布、回收站和原任务" description="只清理新版统一保存且没有引用的原件。旧版原件、生成原件、参考上传缓存和缩略图在此保留。清理不会调用生成服务。" />
            <div className="studio-actions" style={{ marginTop: 22 }}><Button icon={<RefreshCw size={16} />} loading={loading} disabled={busy} onClick={() => void refresh()}>重新检查</Button><Popconfirm title={`清理 ${candidates.length} 份未引用原件？`} description="删除时重新核对全部引用；任一文件状态变化则整批保留。请确认重要原件已经另存。" okText="清理原件" cancelText="保留" onConfirm={() => remove(candidates)} disabled={busy || loading || !snapshot?.canDelete || !candidates.length}><Button icon={<Trash2 size={16} />} disabled={busy || loading || !snapshot?.canDelete || !candidates.length} loading={busy}>清理全部未引用原件</Button></Popconfirm></div>
            {error && <Alert style={{ marginTop: 20 }} type="error" showIcon title="操作未完成，原记录已保留" description={error} />}
            {notice && <Alert style={{ marginTop: 20 }} type="success" showIcon title={notice} />}
            {!!snapshot?.editors.length && <section className="studio-card" style={{ marginTop: 20 }}><h2>仍有页面占用，暂不清理</h2><p>请在其他画布页面保存并离开，或关闭包含画布历史页的标签。浏览器确认连接释放后会更新；不会按时间强制解除占用。</p>{snapshot.editors.map((entry, index) => {
                const known = validEditorClaim(entry), probe = known ? probes.find((value) => value.id === entry.id) : undefined;
                return <p className="studio-break" key={known ? entry.id : index}>{known ? entry.id : "未知页面记录"} · {probe?.message || "无法确认该页面已离开，文件继续保留"}</p>;
            })}</section>}
            {!!snapshot?.issues.length && <Alert style={{ marginTop: 20 }} type="warning" showIcon title="引用未能完整核对，暂不清理" description={snapshot.issues.join("；")} />}
            {snapshot && <section className="studio-card" style={{ marginTop: 20 }}><h2>新版本机原件 · {snapshot.files.length} 份</h2><p className="studio-note">未引用 {candidates.length} 份 · {bytes(candidates.reduce((total, file) => total + file.bytes, 0))}。引用会在实际删除时重新核对。</p>{!snapshot.files.length ? <Empty description="暂无新版本机原件，旧版文件继续保留" /> : snapshot.files.map((file) => <article key={file.id} style={{ borderTop: "1px solid var(--line)", padding: "20px 0" }}>
                <div className="studio-actions" style={{ justifyContent: "space-between" }}><strong className="studio-break">{file.kind === "image" ? "图片原件" : "媒体原件"}</strong><Tag>{bytes(file.bytes)}</Tag></div>
                <p className="studio-break studio-note">{file.id}</p><p>{file.references.length ? `保留 · ${file.references.join("、")}` : "未发现已保存引用"}</p>
                <div className="studio-actions"><Button icon={<Download size={15} />} disabled={busy || loading} onClick={() => void download(file)}>下载原件备份</Button><Popconfirm title="清理这份本机原件？" description="删除后无法从本机恢复。重要原件请先下载。" okText="清理原件" cancelText="保留" onConfirm={() => remove([file])} disabled={busy || loading || !snapshot.canDelete || !!file.references.length}><Button icon={<Trash2 size={15} />} disabled={busy || loading || !snapshot.canDelete || !!file.references.length}>清理原件</Button></Popconfirm></div>
            </article>)}</section>}
        </main>
    </div>;
}

document.body.style.fontFamily = 'Manrope, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif';
document.body.style.overflow = "auto";
createRoot(document.getElementById("root")!).render(<ConfigProvider locale={zhCN} theme={getAntThemeConfig(false)}><App><MaintenancePage /></App></ConfigProvider>);
