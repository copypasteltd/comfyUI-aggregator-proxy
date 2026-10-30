import { useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { Alert, App, Button, Empty, Popconfirm, Tag } from "antd";
import { Download, ExternalLink, RefreshCw, Square } from "lucide-react";
import { saveAs } from "file-saver";
import { useStudioStore } from "@/stores/use-studio-store";
import { cancelIntent, jobStatusLabels, localTasks, lookupIntent, refreshIntent, saveRemoteResult, saveResult, submitIntent, submitPreparedBatch, subscribeStudioTasks } from "@/services/aggregator-engine";
import { batchKey, batchSummary } from "@/services/aggregator-batch";
import type { Intent } from "@/services/aggregator-journal";
import { terminalStatuses, type ContentMetadata, type RemoteJob } from "@/services/api/aggregator/types";
import { taskLabels } from "@/services/api/aggregator/capabilities";
import TaskBackup from "./task-backup";

export default function TasksPage() {
    const { message } = App.useApp();
    const client = useStudioStore((state) => state.client);
    const [tasks, setTasks] = useState<Intent[]>([]);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState<string[]>([]);
    const load = useCallback(async () => {
        if (!client) { setTasks([]); return; }
        try { const result = await localTasks(); if (useStudioStore.getState().client === client) { setTasks(result); setError(""); } }
        catch (reason) { setError(reason instanceof Error ? reason.message : "任务读取失败"); }
    }, [client]);
    useEffect(() => { void load(); return subscribeStudioTasks(() => void load()); }, [load]);
    const action = async (id: string, work: () => Promise<unknown>) => {
        setBusy((ids) => [...ids, id]);
        try { await work(); }
        catch (reason) { message.error(reason instanceof Error ? reason.message : "操作未完成"); }
        finally { setBusy((ids) => ids.filter((item) => item !== id)); await load(); }
    };
    return <main className="studio-page"><div className="studio-page-heading"><span className="li-lab">YOUR TASKS</span><h1>创作继续，任务也在继续</h1><p>这里保留当前服务与 API Key 的本机提交记录。刷新或重新打开页面后，使用原连接核对任务；浏览器查询中止不代表设备停止。</p></div>
        <TaskBackup />
        {!client ? <section className="studio-card studio-empty"><Empty description="连接原服务和 API Key，查看此浏览器的任务" /><NavLink to="/config" className="studio-primary-link">连接服务</NavLink></section> : <>
            <div className="studio-actions"><Button icon={<RefreshCw size={16} />} onClick={() => void load()}>刷新本机记录</Button><NavLink to="/task-access" className="studio-inline-link">换Key后接管已知任务</NavLink></div>
            {error && <Alert title={error} type="error" showIcon />}
            <BatchOverview tasks={tasks} />
            <div className="studio-task-list">{tasks.map((intent) => {
                const request = JSON.parse(intent.body);
                const confirmed = !intent.recovered || intent.recoveryVerified === true;
                const terminal = intent.job && terminalStatuses.has(intent.job.status);
                return <article key={intent.id} className="studio-card studio-task-card"><div className="studio-task-heading"><div><span className="li-lab">{taskLabels[request.task_type] || request.task_type}{intent.batch ? ` · 批次第 ${intent.batch.index + 1}/${intent.batch.total} 项` : ""}</span><h2>{request.model}</h2></div><Tag>{!confirmed ? "备份记录待核对" : intent.job ? jobStatusLabels[intent.job.status] || intent.job.status : intent.stage === "prepared" ? "已保存，待提交" : "提交结果待核对"}</Tag></div><code>{intent.job?.id || intent.id}</code><p>{String(request.input?.prompt || "图片生 3D")}</p>
                    {intent.recovered && <p className="studio-note">来自任务清单。{intent.job ? confirmed ? "已核对原任务，云端文件仍按原到期时间保留。" : "请先查询原任务，再读取产物或请求取消；备份中的状态尚未确认。" : "尚无任务 ID，请只读核对原提交。未查到时仍保留原记录，不会补发生成。"}</p>}
                    {intent.error && <Alert title="操作未确认" description={intent.error} type="warning" showIcon />}
                    <div className="studio-actions">{intent.job ? <Button loading={busy.includes(intent.id)} icon={<RefreshCw size={15} />} onClick={() => void action(intent.id, () => refreshIntent(intent.id))}>查询原任务</Button> : !intent.recovered && intent.stage === "prepared" ? <Button loading={busy.includes(intent.id)} onClick={() => void action(intent.id, () => submitIntent(intent.id))}>提交已保存请求</Button> : <Button loading={busy.includes(intent.id)} onClick={() => void action(intent.id, () => lookupIntent(intent.id))}>只读核对原提交</Button>}
                        {intent.projectId && !intent.recovered && <NavLink className="studio-inline-link" to={`/canvas/${intent.projectId}`}><ExternalLink size={14} /> 返回画布</NavLink>}
                        {confirmed && intent.job && !terminal && <Popconfirm title="请求取消这项远端任务？" description="以服务端确认的终态为准。" onConfirm={() => action(intent.id, () => cancelIntent(intent.id))}><Button icon={<Square size={13} />} loading={busy.includes(intent.id)}>请求取消</Button></Popconfirm>}
                        {confirmed && intent.job?.status === "succeeded" && intent.job.artifacts?.map((artifact) => <Button key={artifact.id} icon={<Download size={15} />} loading={busy.includes(intent.id)} onClick={() => void action(intent.id, async () => {
                            const result = await saveResult(intent.id, artifact.id);
                            const extensions: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4", "video/webm": "webm", "audio/wav": "wav", "audio/flac": "flac", "audio/mpeg": "mp3", "audio/ogg": "ogg", "model/gltf-binary": "glb" };
                            saveAs(result.blob, `${artifact.id}.${extensions[result.metadata.media_type] || "bin"}`);
                        })}>校验并保存文件</Button>)}
                    </div>{intent.cancelRequested && !terminal && <p className="studio-note">已请求取消，等待服务端确认。</p>}
                </article>;
            })}</div>{!tasks.length && <Empty description="此浏览器尚无该连接的画布任务" />}<ServerHistory />
        </>}
    </main>;
}

function BatchOverview({ tasks }: { tasks: Intent[] }) {
    const { message } = App.useApp();
    const [busy, setBusy] = useState<string[]>([]);
    const groups = new Map<string, Intent[]>();
    for (const task of tasks) if (task.batch) { const key = batchKey(task); groups.set(key, [...(groups.get(key) || []), task]); }
    if (!groups.size) return null;
    const labels = { prepared: "未发送", unknown: "结果未知", active: "进行中", succeeded: "已完成", failed: "失败", cancelled: "已取消", expired: "已过期", unverified: "备份待核对" };
    return <section aria-label="批次概览" className="studio-task-list">{[...groups].map(([key, items]) => {
        const first = items[0];
        let counts: ReturnType<typeof batchSummary>;
        try { counts = batchSummary(items); }
        catch { return <Alert key={key} type="error" showIcon title="批次关联需要核对" description="原记录已保留，未提交批次；请先导出任务清单并逐项核对。" />; }
        return <article key={key} className="studio-card studio-task-card" aria-label={`批次 ${first.batch!.id}`}><span className="li-lab">BATCH</span><h2>{JSON.parse(first.body).model} · {first.batch!.total} 项创作</h2><p>已保留 {items.length}/{first.batch!.total} 项记录，各项结果独立保存。</p>
            <div className="studio-actions">{Object.entries(counts).filter(([, count]) => count > 0).map(([name, count]) => <Tag key={name}>{labels[name as keyof typeof labels]} {count}</Tag>)}</div>
            {items.length !== first.batch!.total && <p className="studio-note">批次记录不完整，请结合原任务历史核对；不会自动补建缺少的成员。</p>}
            {counts.prepared > 0 && <div className="studio-actions"><Button loading={busy.includes(key)} onClick={() => void (async () => {
                setBusy((values) => [...values, key]);
                try { const count = await submitPreparedBatch(first.batch!.id, first.projectId, first.identity); message.success(`本次已确认 ${count} 项，其他原记录保留`); }
                catch (error) { message.warning(error instanceof Error ? error.message : "批次提交已暂停，原记录保留"); }
                finally { setBusy((values) => values.filter((value) => value !== key)); }
            })()}>继续提交未发送项（{counts.prepared}）</Button></div>}
            <p className="studio-note">未知项请在下方只读核对；继续提交会跳过已发送、终态及备份恢复记录。</p>
        </article>;
    })}</section>;
}

function ServerHistory() {
    const client = useStudioStore((state) => state.client);
    const [jobs, setJobs] = useState<RemoteJob[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [metadata, setMetadata] = useState<Record<string, ContentMetadata>>({});
    useEffect(() => { setJobs([]); setCursor(null); setLoaded(false); setMetadata({}); setError(""); }, [client]);
    const action = async (work: () => Promise<void>) => {
        if (busy || !client) return;
        setBusy(true); setError("");
        try { await work(); } catch (reason) { setError(reason instanceof Error ? reason.message : "服务端历史读取失败"); }
        finally { setBusy(false); }
    };
    const load = async (next?: string) => {
        if (!client) return;
        const { data: page } = await client.jobs(next);
        if (!Array.isArray(page.data) || page.has_more !== (page.next_cursor !== null) || page.data.some((job) => !/^job_[a-f0-9]{32}$/.test(job.id))) throw new Error("服务端任务分页格式不受支持");
        if (useStudioStore.getState().client !== client) return;
        setJobs((old) => [...new Map([...(next ? old : []), ...page.data].map((job) => [job.id, job])).values()]);
        setCursor(page.next_cursor); setLoaded(true);
    };
    return <section style={{ marginTop: 48 }}><span className="li-lab">SERVER HISTORY</span><h2>读取原服务的任务</h2><p className="studio-note">换浏览器后也可查询当前 Key 有权访问的历史。这里仅查询已有任务；没有本机日志时不会补发生成。全局和分渠道队列请使用管理后台。</p>
        <Button loading={busy} onClick={() => void action(() => load())}>读取服务端历史</Button>
        {error && <Alert title={error} type="warning" showIcon style={{ marginTop: 16 }} />}
        {loaded && !jobs.length && <Empty description="该 Key 暂无服务端任务" />}
        <div className="studio-task-list">{jobs.map((job) => <article key={job.id} className="studio-card studio-task-card"><div className="studio-task-heading"><h3>{job.model}</h3><Tag>{jobStatusLabels[job.status] || job.status}</Tag></div><code>{job.id}</code><p>{taskLabels[job.task_type]} · {new Date(job.created_at * 1000).toLocaleString()}</p><Button loading={busy} onClick={() => void action(async () => {
            const { data: current } = await client!.job(job.id);
            if (useStudioStore.getState().client === client) setJobs((values) => values.map((value) => value.id === current.id ? current : value));
        })}>查询任务与产物</Button>
            {job.artifacts?.map((artifact) => <div key={artifact.id} style={{ marginTop: 16 }}><Button loading={busy} onClick={() => void action(async () => {
                const value = await client!.metadata(artifact.id);
                if (useStudioStore.getState().client === client) setMetadata((old) => ({ ...old, [artifact.id]: value }));
            })}>查看产物信息</Button>{metadata[artifact.id] && <><p>{metadata[artifact.id].media_type} · {(metadata[artifact.id].bytes / 1024 / 1024).toFixed(2)} MiB · 云端保留至 {new Date(metadata[artifact.id].expires_at * 1000).toLocaleString()}</p><Popconfirm title="校验并载入这个原件？" description="会下载上述完整文件，占用浏览器内存与本机存储。完成后可在素材页另存。" onConfirm={() => action(async () => { await saveRemoteResult(job.id, artifact.id); })}><Button loading={busy}>校验并载入本机</Button></Popconfirm></>}</div>)}
        </article>)}</div>{cursor && <Button loading={busy} onClick={() => void action(() => load(cursor))}>读取下一页</Button>}
    </section>;
}
