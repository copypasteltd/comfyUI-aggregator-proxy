import { MANAGED_STUDIO } from "@/constant/studio";

export function hasAgentUrlBootstrap(hash: string) {
    if (MANAGED_STUDIO) return false;
    const params = new URLSearchParams(hash.replace(/^#/, ""));
    return params.has("agentUrl") || params.has("agentToken");
}

export function readAgentUrlBootstrap(hash: string) {
    if (MANAGED_STUDIO) return null;
    const params = new URLSearchParams(hash.replace(/^#/, ""));
    if (!params.has("agentUrl") && !params.has("agentToken")) return null;
    const url = params.get("agentUrl")?.trim() || "";
    const token = params.get("agentToken")?.trim() || "";
    params.delete("agentUrl");
    params.delete("agentToken");
    const remaining = params.toString();
    return { url, token, remainingHash: remaining ? `#${remaining}` : "" };
}
