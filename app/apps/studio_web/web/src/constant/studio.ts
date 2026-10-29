// A separately built standalone variant retains upstream integrations. Hosted builds are managed by default.
export const MANAGED_STUDIO = import.meta.env.VITE_STUDIO_MODE !== "standalone";

export function requireStandalone() {
    if (MANAGED_STUDIO) throw new Error("托管画布不执行自定义脚本、插件或本地 Agent");
}

export function requireUpstreamGeneration() {
    if (MANAGED_STUDIO) throw new Error("请通过“新建创作”选择服务公布的模型和规格；此旧版生成入口在托管画布中关闭");
}
