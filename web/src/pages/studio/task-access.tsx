import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { Alert, App, Button, Empty, Modal, Popconfirm, Tag } from "antd";
import { Download, KeyRound, RefreshCw, Upload } from "lucide-react";
import { saveAs } from "file-saver";
import { useStudioStore } from "@/stores/use-studio-store";
import { associateKnownTask, notifyStudioTasksChanged, removeTaskAccess, subscribeStudioTasks, taskAccessCandidates } from "@/services/aggregator-engine";
import type { Intent } from "@/services/aggregator-journal";
import { canReadTask, taskAccessReason } from "@/services/studio-task-access";
import { readTaskJournalFromOrigin } from "@/services/task-journal-format";
import { exportTaskJournal, restoreOriginalTaskJournal } from "@/services/task-journal-recovery";

export default function TaskAccessPage() {
    const { message } = App.useApp();
    const { client, activeProfileId } = useStudioStore();
    const [records, setRecords] = useState<{ client: typeof client; values: Intent[] }>();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [preview, setPreview] = useState<{ client: NonNullable<typeof client>; source: string; value: ReturnType<typeof readTaskJournalFromOrigin> }>();
    const input = useRef<HTMLInputElement>(null);
    const operation = useRef(0);
    const controller = useRef<AbortController>(null);
    const refresh = useCallback(async () => {
        if (!client) return;
        const values = await taskAccessCandidates();
        if (useStudioStore.getState().client === client) setRecords({ client, values });
    }, [client]);
    useEffect(() => {
        ++operation.current; controller.current?.abort(); setBusy(false); setError(""); setPreview(undefined);
        const load = () => void refresh().catch((reason) => { if (useStudioStore.getState().client === client) setError(reason instanceof Error ? reason.message : "记录读取失败"); });
        const off = subscribeStudioTasks(load); load();
        return () => { ++operation.current; controller.current?.abort(); off(); };
    }, [client, refresh]);
    const act = async (work: (signal: AbortSignal) => Promise<unknown>) => {
        if (busy || !client) return;
        const sequence = ++operation.current;
        const owned = new AbortController(); controller.current = owned;
        setBusy(true); setError("");
        try { await work(owned.signal); }
        catch (reason) { if (operation.current === sequence && useStudioStore.getState().client === client) setError(reason instanceof Error ? reason.message : "接管未完成，原记录已保留"); }
        finally { if (operation.current === sequence) { setBusy(false); controller.current = null; await refresh().catch(() => {}); } }
    };
    const values = records?.client === client ? records.values : [];
    const groups = [...new Map(values.map((task) => [task.identity.fingerprint, task.identity])).values()];
    return <main className="studio-page">
        <div className="studio-page-heading"><span className="li-lab">CONTINUE WITH YOUR NEW KEY</span><h1>接管已知任务</h1><p>换Key后，先向原服务核对每项任务的读取权限。原提交和未知记录保留，不重新生成。</p></div>
        {!client ? <section className="studio-card"><Empty description="请先连接新API Key" /><NavLink className="studio-inline-link" to="/config">连接服务</NavLink></section> : <>
            <section className="studio-card" aria-label="任务接管说明"><h2><KeyRound size={20} /> 原任务，继续使用</h2><p className="studio-note" style={{ marginTop: 12 }}>只有点击确认才用当前Key查询原任务。关联后可继续查询、请求取消或读取产物，仍受服务端权限和原到期时间约束。没有任务ID的记录不能换Key补发。</p>
                <div className="studio-actions"><Button icon={<RefreshCw size={15} />} disabled={busy} onClick={() => void act(refresh)}>刷新本机记录</Button><Button icon={<Upload size={15} />} disabled={busy} onClick={() => input.current?.click()}>恢复旧身份任务清单</Button><NavLink className="studio-inline-link" to="/tasks">返回任务</NavLink></div>
                <input ref={input} hidden type="file" accept=".json,application/json" aria-label="选择旧身份任务清单" onChange={(event) => {
                    const file = event.target.files?.[0]; event.target.value = "";
                    if (file) void act(async () => {
                        const source = await file.text();
                        const value = readTaskJournalFromOrigin(source, client.origin);
                        if (useStudioStore.getState().client !== client) throw new Error("连接已改变，请重新选择清单");
                        setPreview({ client, source, value });
                    });
                }} />
                <p className="studio-note" style={{ marginTop: 12 }}>任务清单按原身份备份；新Key的访问关联不写入清单。下载会按新身份保存本机副本，旧缓存保留。解除关联不取消任务、不删除原记录或已保存文件。</p>
            </section>
            {error && <Alert title="未完成，原记录已保留" description={error} type="warning" showIcon style={{ marginTop: 24 }} />}
            <div className="studio-task-list">{groups.map((identity) => <section key={identity.fingerprint} className="studio-card" aria-label={`原身份 ${identity.fingerprint.slice(0, 8)}`}><span className="li-lab">ORIGINAL CONNECTION</span><h2>原身份 · {identity.fingerprint.slice(0, 8)}</h2><Button icon={<Download size={15} />} disabled={busy} onClick={() => void act(async () => { const source = await exportTaskJournal(identity); saveAs(new Blob([source], { type: "application/json" }), `comfy-studio-tasks-${identity.fingerprint.slice(0, 8)}.json`); })}>导出原身份清单</Button>
                {values.filter((task) => task.identity.fingerprint === identity.fingerprint).map((task) => {
                    let model = "原任务";
                    try { model = JSON.parse(task.body).model || model; } catch { /* Retain damaged records for explicit recovery. */ }
                    const reason = taskAccessReason(task, { profileId: activeProfileId, origin: client.origin, fingerprint: client.fingerprint });
                    const associated = canReadTask(task, client);
                    return <article key={task.id} style={{ borderTop: "1px solid var(--line)", paddingTop: 24, marginTop: 24, overflowWrap: "anywhere" }} aria-label={`接管任务 ${task.id}`}><h3>{model}</h3><p><code>{task.job?.id || task.id}</code></p><Tag>{associated ? "已核对并关联" : task.job ? "待新Key核对" : "无任务ID，保留原身份"}</Tag>
                        {reason ? <p className="studio-note">{reason}</p> : <div className="studio-actions"><Popconfirm title="用当前Key核对并接管这项任务？" description="仅查询原任务；服务确认权限后建立本机关联，不重新生成。" disabled={busy} onConfirm={() => act(async (signal) => { await associateKnownTask(task, signal); if (useStudioStore.getState().client === client) message.success("已核对原任务，可以继续查询"); })}><Button type={associated ? "default" : "primary"} disabled={busy}>{associated ? "重新核对权限" : "核对并接管"}</Button></Popconfirm>{associated && <Popconfirm title="解除当前Key的本机关联？" description="原记录、远程任务和已保存文件保留。" disabled={busy} onConfirm={() => act(() => removeTaskAccess(task))}><Button disabled={busy}>解除关联</Button></Popconfirm>}</div>}
                    </article>;
                })}
            </section>)}</div>
            {!values.length && <section className="studio-card" style={{ marginTop: 24 }}><Empty description="本机没有该服务的其他身份任务，可恢复原任务清单后逐项核对" /></section>}
        </>}
        <Modal title="恢复原身份记录" open={Boolean(preview && preview.client === client)} destroyOnHidden onCancel={() => { if (!busy) setPreview(undefined); }} footer={<div className="studio-actions" style={{ justifyContent: "flex-end" }}><Button disabled={busy} onClick={() => setPreview(undefined)}>取消</Button><Button type="primary" loading={busy} onClick={() => void act(async () => {
            if (!preview || preview.client !== client) throw new Error("连接已变化，请重新预览");
            const result = await restoreOriginalTaskJournal(preview.source, client.origin, () => useStudioStore.getState().client === client);
            notifyStudioTasksChanged(); setPreview(undefined); message.success(`已恢复${result.added}条，保留已有${result.kept}条；请逐项核对已知任务`);
        })}>仅恢复原记录</Button></div>}>
            {preview && <div style={{ display: "grid", gap: 16, overflowWrap: "anywhere" }}><p>{preview.value.tasks.length}条原记录 · 已知任务 {preview.value.tasks.filter((task) => task.job).length}条 · 未知提交 {preview.value.tasks.filter((task) => !task.job).length}条</p><p>原身份 · {preview.value.identity.fingerprint.slice(0, 8)}</p><Alert type="info" showIcon title="此步骤不建立访问权限" description="保留原身份并仅导入记录；不自动查询、上传或生成。恢复后须逐项用新Key核对，未知提交仍不能接管。" />{error && <Alert type="error" title={error} />}</div>}
        </Modal>
    </main>;
}
