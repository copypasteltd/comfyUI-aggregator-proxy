import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { Alert, Button } from "antd";
import { NavLink, useLocation } from "react-router-dom";
import { Boxes, BookOpen, CircleHelp, Infinity, KeyRound, Layers3, ListTodo, Moon, PanelsTopLeft, Sun } from "lucide-react";
import { useThemeStore } from "@/stores/use-theme-store";
import { useStudioStore } from "@/stores/use-studio-store";
import { useStudioRuntime } from "@/hooks/use-studio-runtime";
import { getStudioSaveState, retryStudioSave, subscribeStudioSaveState } from "@/services/studio-persistence";

export default function StudioLayout({ children }: { children: ReactNode }) {
    const client = useStudioStore((state) => state.client);
    const catalog = useStudioStore((state) => state.catalog);
    const hydrate = useStudioStore((state) => state.hydrate);
    const location = useLocation();
    const save = useSyncExternalStore(subscribeStudioSaveState, getStudioSaveState);
    const theme = useThemeStore((state) => state.theme);
    const setTheme = useThemeStore((state) => state.setTheme);
    useStudioRuntime();
    useEffect(() => { void hydrate(); }, [hydrate]);
    return <div className="studio-shell">
        <a className="studio-skip" href="#studio-content">跳到主要内容</a>
        <header className="studio-header">
            <NavLink to="/canvas" className="li-brand studio-brand"><i><Infinity size={21} /></i><span>青柠画布<small>COMFY STUDIO</small></span></NavLink>
            <nav className="studio-navigation" aria-label="主导航">
                <NavLink to="/canvas" aria-label="画布"><PanelsTopLeft size={17} /><span>画布</span></NavLink>
                <NavLink to="/models" aria-label="模型"><Boxes size={17} /><span>模型</span></NavLink>
                <NavLink to="/tasks" aria-label="任务"><ListTodo size={17} /><span>任务</span></NavLink>
                <NavLink to="/assets" aria-label="素材"><Layers3 size={17} /><span>素材</span></NavLink>
                <NavLink to="/examples" aria-label="案例"><BookOpen size={17} /><span>案例</span></NavLink>
            </nav>
            <div className="studio-header-actions">
                <Button type="text" className="studio-theme-toggle" aria-label={theme === "dark" ? "切换到浅色" : "切换到深色"} title={theme === "dark" ? "切换到浅色" : "切换到深色"} aria-pressed={theme === "dark"} icon={theme === "dark" ? <Sun size={18} /> : <Moon size={18} />} onClick={() => setTheme(theme === "dark" ? "light" : "dark")} />
                <NavLink to="/config" className={`studio-connection${client ? " connected" : ""}`} title={client ? `${client.origin} · ${catalog?.models.length || 0} 个授权模型` : "连接你的生成服务"}><KeyRound size={16} /><span>{client ? "已连接" : "连接服务"}</span></NavLink>
            </div>
        </header>
        {save.error && <Alert className="studio-save-error" role="alert" type="error" showIcon title="本机编辑尚未保存" description={save.error} action={save.canRetry ? <Button onClick={() => { void retryStudioSave().catch(() => {}); }}>重试保存</Button> : undefined} />}
        <div id="studio-content" tabIndex={-1} className={`studio-content${location.pathname.startsWith("/canvas/") ? " studio-editor" : ""}`}>{children}</div>
        {!location.pathname.startsWith("/canvas/") && <footer className="studio-footer"><span>基于 basketikun / infinite-canvas · MIT</span><span><CircleHelp size={13} /> 画布和已保存素材存于当前浏览器</span></footer>}
    </div>;
}
