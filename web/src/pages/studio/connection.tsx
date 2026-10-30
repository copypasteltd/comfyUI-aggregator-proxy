import { useState } from "react";
import { Alert, Button, Checkbox, Input, Select } from "antd";
import { ArrowRight, KeyRound, Link2, LogOut, RefreshCw } from "lucide-react";
import { useStudioStore } from "@/stores/use-studio-store";
import { NavLink } from "react-router-dom";

export default function ConnectionPage() {
    const state = useStudioStore();
    const active = state.profiles.find((profile) => profile.id === state.activeProfileId);
    const defaultOrigin = import.meta.env.VITE_AGGREGATOR_ORIGIN || "";
    const [origin, setOrigin] = useState(active?.origin || defaultOrigin);
    const [name, setName] = useState(active?.name || "");
    const [key, setKey] = useState("");
    const [remember, setRemember] = useState(false);
    const [selected, setSelected] = useState<string | undefined>(active?.id);
    const submit = async (event: React.FormEvent) => {
        event.preventDefault();
        try { await state.connect(origin, key, remember, name, selected); setKey(""); setSelected(useStudioStore.getState().activeProfileId); }
        catch { /* The shared error is rendered below without logging credentials. */ }
    };
    return <main className="studio-page studio-connection-page">
        <div className="studio-page-heading"><span className="li-lab">YOUR GENERATION SERVICE</span><h1>连接创作能力</h1><p>使用管理员分配的业务 API Key，读取你有权调用的模型、工作流说明和输出规格。</p></div>
        <div className="studio-connection-grid">
            <form onSubmit={submit} className="studio-card studio-form">
                <h2><Link2 size={20} /> 服务连接</h2>
                {state.profiles.length > 0 && <label>已保存的连接<Select aria-label="已保存的连接" value={selected} allowClear placeholder="新建连接" options={state.profiles.map((profile) => ({ value: profile.id, label: `${profile.name} · ${profile.fingerprint.slice(0, 8)}` }))} onChange={(id) => {
                    setSelected(id); const profile = state.profiles.find((item) => item.id === id);
                    setOrigin(profile?.origin || defaultOrigin); setName(profile?.name || ""); setKey(""); setRemember(false);
                }} /></label>}
                <label>连接名称<Input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：我的 ComfyUI 服务" autoComplete="off" /></label>
                <label>服务地址<Input required value={origin} onChange={(event) => setOrigin(event.target.value)} placeholder="http://服务器:端口" inputMode="url" autoComplete="url" /></label>
                <p className="studio-note">填写原 API 服务地址，可与本画布的访问地址不同。画布直接调用该服务，不提供请求转发。</p>
                <label>业务 API Key<Input.Password required value={key} onChange={(event) => setKey(event.target.value)} placeholder="输入管理员提供的 API Key" autoComplete="off" /></label>
                <Checkbox checked={remember} onChange={(event) => setRemember(event.target.checked)}>在此浏览器保存 API Key（仅限可信设备）</Checkbox>
                <p className="studio-note">默认仅在当前页面内存中使用。勾选后，Key 会保存在此浏览器的本地数据库；退出连接会移除当前连接保存的 Key。</p>
                {state.error && <Alert title="连接未完成" description={state.error} type="error" showIcon />}
                <Button htmlType="submit" type="primary" size="large" loading={state.loading} icon={<ArrowRight size={17} />}>连接并读取模型</Button>
            </form>
            <section className="studio-card studio-connection-summary">
                <div className="studio-service-icon"><KeyRound size={30} /></div><h2>{state.client ? "服务已连接" : "让创作回到画布上"}</h2>
                {state.client ? <><p className="studio-break">{state.client.origin}</p><div className="studio-metric"><b className="li-d">{state.catalog?.models.length || 0}</b><span>当前授权的已发布模型</span></div><p>目录表示当前授权能力；任务能否开始执行仍取决于队列、设备和额度。</p>
                    {!state.catalog?.releaseGuard && <Alert title="此服务当前仅可查看模型" description="该 API 未声明提交所需的发布校验能力。请使用已支持此能力的原服务；画布不会修改 API。" type="warning" showIcon />}
                    <div className="studio-actions"><Button icon={<RefreshCw size={15} />} loading={state.loading} onClick={() => void state.refreshCatalog()}>刷新目录</Button><Button icon={<LogOut size={15} />} onClick={() => void state.disconnect().catch(() => {})}>退出连接</Button><a className="studio-inline-link" href={`${state.client.origin}/admin/`} target="_blank" rel="noopener noreferrer">打开管理后台</a></div>
                </> : <p>图片、视频、音频和 3D 使用同一套任务与素材流程。选择模型、添加参考、提交后即可继续整理画布。</p>}
                <div className="studio-scope-note">业务 Key 用于生成和内容访问。渠道、Workflow、队列与 Key 的管理仍由 Admin 完成。</div>
                <NavLink to="/storage" className="studio-inline-link">查看本机存储空间</NavLink>
                <NavLink to="/task-access" className="studio-inline-link">换Key后接管已知任务</NavLink>
            </section>
        </div>
    </main>;
}
