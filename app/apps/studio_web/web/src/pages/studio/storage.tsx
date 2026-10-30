import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { Alert, Button, Empty, Popconfirm, Progress, Spin, Tag } from "antd";
import { Database, RefreshCw, Trash2 } from "lucide-react";
import { readLocalStorageUsage, type LocalStorageUsage } from "@/services/local-storage-usage";
import { deleteCachedUpload, readUploadCache, type CachedUpload } from "@/services/studio-upload-cache";
import { notifyStudioTasksChanged } from "@/services/aggregator-engine";
import { useStudioStore } from "@/stores/use-studio-store";

const bytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(2)} MiB` : `${(value / 1024 ** 3).toFixed(2)} GiB`;
const labels: Record<string, string> = { documents: "画布与素材文档", intents: "原任务日志", uploads: "参考图缓存", results: "已保存的生成原件", budgets: "查询预算", profiles: "连接配置", app_state: "旧文档与应用状态", image_files: "图片原件", image_previews: "图片缩略图", media_files: "媒体及离线副本" };

export default function StudioStoragePage() {
    const client = useStudioStore((state) => state.client);
    const profileId = useStudioStore((state) => state.activeProfileId);
    const [usage, setUsage] = useState<LocalStorageUsage>();
    const [cache, setCache] = useState<{ client: typeof client; values: CachedUpload[] }>();
    const [selected, setSelected] = useState<string>();
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const generation = useRef(0);
    const refresh = useCallback(async () => {
        const current = ++generation.current;
        setLoading(true); setError(""); setSelected(undefined);
        try {
            const [next, uploads] = await Promise.all([readLocalStorageUsage(["infinite-canvas", "comfy-aggregator-studio:journal", "comfy-aggregator-studio"]), client && profileId ? readUploadCache({ origin: client.origin, fingerprint: client.fingerprint, profileId }) : []]);
            if (current === generation.current && useStudioStore.getState().client === client) { setUsage(next); setCache({ client, values: uploads }); }
        } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : "本机空间读取失败，请重试"); }
        finally { if (current === generation.current) setLoading(false); }
    }, [client, profileId]);
    useEffect(() => { setNotice(""); void refresh(); return () => { generation.current++; }; }, [refresh]);
    const remove = async (item: CachedUpload) => {
        if (!client || !profileId || busy) return;
        setBusy(true); setError(""); setNotice("");
        try {
            await deleteCachedUpload(item, { origin: client.origin, fingerprint: client.fingerprint, profileId }, () => useStudioStore.getState().client === client);
            notifyStudioTasksChanged();
            if (useStudioStore.getState().client === client) { setNotice("本机参考缓存已清理，云端源图和任务未改变。"); await refresh(); }
        } catch (reason) { if (useStudioStore.getState().client === client) setError(reason instanceof Error ? reason.message : "缓存未清理，请刷新重试"); }
        finally { setBusy(false); setSelected(undefined); }
    };
    const entries = cache && cache.client === client ? cache.values : [];
    const percent = usage?.siteEstimateAvailable && usage.quota ? Math.min(100, usage.usage / usage.quota * 100) : undefined;
    return <main className="studio-page">
        <div className="studio-page-heading"><span className="li-lab">YOUR LOCAL STORAGE</span><h1>给创作留出空间</h1><p>查看当前浏览器的画布、原件和参考缓存。重要作品请另存到设备；云端完成后八小时的保留期限与本机副本分开计算。</p></div>
        <div className="studio-actions"><Button icon={<RefreshCw size={16} />} loading={loading} disabled={busy} onClick={() => void refresh()}>刷新占用</Button><NavLink className="studio-inline-link" to="/assets">管理生成原件</NavLink><NavLink className="studio-inline-link" to="/tasks">备份任务清单</NavLink></div>
        {error && <Alert style={{ marginTop: 20 }} type="error" showIcon title="操作未完成" description={error} />}
        {notice && <Alert style={{ marginTop: 20 }} type="success" showIcon title={notice} />}
        {!usage && loading ? <Spin style={{ marginTop: 24 }} /> : usage && <>
            <div className="studio-model-grid" style={{ marginTop: 24 }}>
                {[['本机记录内容估算', bytes(usage.contentBytes)], ['站点占用估算', usage.siteEstimateAvailable ? bytes(usage.usage) : '当前浏览器未提供'], ['浏览器提供的配额', usage.quota === null ? '当前浏览器未提供' : bytes(usage.quota)]].map(([title, value]) => <section className="studio-card" key={title}><h2>{title}</h2><p className="li-d" style={{ fontSize: 28, overflowWrap: "anywhere" }}>{value}</p></section>)}
            </div>
            {percent !== undefined && <section className="studio-card" style={{ marginTop: 20 }}><h2>站点空间使用 {percent.toFixed(2)}%</h2><Progress percent={percent} showInfo={false} /><p className="studio-note">浏览器估算包含当前站点的其他存储及开销，配额会变化，不等于设备剩余空间。即使配额充足，写入仍可能失败。</p></section>}
            <section className="studio-card" style={{ marginTop: 20 }}><h2><Database size={19} /> 本机记录分布</h2><p className="studio-note">包含嵌套原件的内容字节估算，不含数据库索引等开销；同一浏览器其他连接的记录只计入总体占用。</p>{usage.databases.map((database) => <div key={database.name} style={{ marginTop: 20 }}><p className="studio-break">{database.name} · {database.version ? `v${database.version}` : '尚未创建'}</p>{database.stores.map((store) => <div className="studio-actions" key={store.name} style={{ justifyContent: "space-between", marginBottom: 10 }}><span>{labels[store.name] || store.name}</span><span>{store.records} 条 · {bytes(store.bytes)}</span></div>)}</div>)}</section>
        </>}
        <section className="studio-card" style={{ marginTop: 20 }}><h2>当前连接的参考图缓存</h2><p>清理仅移除本机缓存。已被原任务引用的参考图、上传未确认和无法核对的记录会保留；清理前会再次检查最新任务。正在编辑的参考选择可能需要重新选图。</p>
            {!client ? <Empty description={<NavLink to="/config">连接原服务与 API Key 后管理对应缓存</NavLink>} /> : !entries.length ? <Empty description={loading ? "正在读取参考缓存…" : "当前连接没有参考图缓存"} /> : entries.map((item) => <article key={item.id} style={{ borderTop: "1px solid var(--line)", padding: "18px 0" }}><div className="studio-actions" style={{ justifyContent: "space-between" }}><strong style={{ overflowWrap: "anywhere" }}>{item.name}</strong><Tag>{bytes(item.bytes)}</Tag></div><p className="studio-note">{item.blockedReason || "上传已确认，尚无原任务引用；可清理本机缓存。"}</p><Popconfirm open={selected === item.id && cache?.client === client} onOpenChange={(open) => setSelected(open ? item.id : undefined)} title="清理这份本机参考缓存？" description="清理后需要重新选图才能再次使用；云端源图和任务不变。" okText="清理缓存" cancelText="保留" onConfirm={() => remove(item)} okButtonProps={{ loading: busy }} disabled={Boolean(item.blockedReason) || loading || busy}><Button icon={<Trash2 size={14} />} disabled={Boolean(item.blockedReason) || loading || busy}>清理缓存</Button></Popconfirm></article>)}
        </section>
        <p className="studio-note" style={{ marginTop: 20 }}>普通图片、媒体及离线副本暂不自动回收，避免误删其他画布引用。删除生成原件请使用素材库，仍被画布或任务引用的原件会受到保护。</p>
    </main>;
}
