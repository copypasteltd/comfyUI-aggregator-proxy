import { useRef, useState } from "react";
import { Alert, App, Button, Modal, Tag } from "antd";
import { Download, Upload } from "lucide-react";
import { saveAs } from "file-saver";
import { useStudioStore } from "@/stores/use-studio-store";
import { notifyStudioTasksChanged } from "@/services/aggregator-engine";
import { exportTaskJournal, restoreTaskJournal } from "@/services/task-journal-recovery";
import { readTaskJournal, type TaskJournalBackup } from "@/services/task-journal-format";

export default function TaskBackup() {
    const { message } = App.useApp();
    const { client, profiles, activeProfileId } = useStudioStore();
    const profile = profiles.find((item) => item.id === activeProfileId);
    const input = useRef<HTMLInputElement>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [preview, setPreview] = useState<{ source: string; filename: string; value: TaskJournalBackup } | null>(null);
    const act = async (work: () => Promise<void>) => {
        setBusy(true); setError("");
        try { await work(); }
        catch (reason) { setError(reason instanceof DOMException && reason.name === "QuotaExceededError" ? "本机存储空间不足，任务清单未导入；请先另存重要作品，释放空间后重新导入。" : reason instanceof Error ? reason.message : "任务清单操作未完成，原记录已保留"); }
        finally { setBusy(false); }
    };
    return <section className="studio-card" style={{ marginBottom: 24 }} aria-label="任务清单备份">
        <h2>备份任务清单</h2>
        <p className="studio-note" style={{ marginTop: 12 }}>保存原提交和任务编号，供换浏览器后核对。文件含原提示词，请妥善保管；作品和原件请另外导出画布ZIP或另存文件。</p>
        <div className="studio-actions">
            <Button icon={<Download size={15} />} disabled={!profile || busy} onClick={() => void act(async () => {
                const source = await exportTaskJournal({ profileId: profile!.id, origin: profile!.origin, fingerprint: profile!.fingerprint });
                saveAs(new Blob([source], { type: "application/json" }), "comfy-studio-tasks.json");
                message.success("任务清单已交给浏览器下载；不含媒体原件");
            })}>导出任务清单</Button>
            <Button icon={<Upload size={15} />} disabled={!client || busy} onClick={() => input.current?.click()}>导入任务清单</Button>
            <input ref={input} hidden type="file" accept=".json,application/json" aria-label="选择任务清单" onChange={(event) => {
                const file = event.target.files?.[0]; event.target.value = "";
                if (file && client) void act(async () => {
                    setPreview(null);
                    const source = await file.text();
                    const value = readTaskJournal(source, client);
                    if (useStudioStore.getState().client !== client) throw new Error("连接已改变，请使用原连接重新选择清单");
                    setPreview({ source, filename: file.name, value });
                });
            }} />
        </div>
        {!client && <p className="studio-note" style={{ marginTop: 12 }}>已保存的连接可以离线导出；导入前请连接原服务及原API Key。</p>}
        {error && !preview && <Alert style={{ marginTop: 16 }} title="清单操作未完成" description={error} type="error" showIcon />}
        <Modal title="确认恢复任务清单" open={Boolean(preview)} width={720} destroyOnHidden onCancel={() => { if (!busy) { setPreview(null); setError(""); } }} footer={<div className="studio-actions" style={{ justifyContent: "flex-end" }}><Button disabled={busy} onClick={() => { setPreview(null); setError(""); }}>取消</Button><Button type="primary" disabled={!client} loading={busy} onClick={() => void act(async () => {
            if (!preview || !client || !activeProfileId) throw new Error("请连接原服务及原API Key");
            const result = await restoreTaskJournal(preview.source, { profileId: activeProfileId, origin: client.origin, fingerprint: client.fingerprint }, () => useStudioStore.getState().client === client);
            notifyStudioTasksChanged(); setPreview(null);
            message.success(`已恢复${result.added}条，保留本机已有${result.kept}条；请主动核对原任务`);
        })}>仅导入记录</Button></div>}>
            {preview && <div style={{ display: "grid", gap: 16, overflowWrap: "anywhere" }}>
                <p>{preview.filename} · {preview.value.tasks.length}条记录 · 有任务ID {preview.value.tasks.filter((task) => task.job).length}条 · 无任务ID {preview.value.tasks.filter((task) => !task.job).length}条</p>
                <p className="studio-note">原服务：{preview.value.identity.origin}</p>
                <Alert title="导入后请主动核对" description="现有记录保留；导入不发起生成或查询。已有任务ID可主动查询，无任务ID的备份只读核对原提交；批次关联不会允许补发生成。" type="info" showIcon />
                {error && <Alert title="未导入，原记录已保留" description={error} type="error" showIcon />}
                <ul style={{ maxHeight: "40vh", overflowY: "auto", margin: 0, padding: 0, listStyle: "none" }}>{preview.value.tasks.map((task) => <li key={task.id} style={{ marginBottom: 16 }}><b>{JSON.parse(task.body).model}</b> <Tag>{task.job ? "有任务ID" : "待核对"}</Tag><div style={{ fontSize: 12 }}>{task.job?.id || task.id}</div></li>)}</ul>
            </div>}
        </Modal>
    </section>;
}
