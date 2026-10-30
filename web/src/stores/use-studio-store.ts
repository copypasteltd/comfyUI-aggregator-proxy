import { create } from "zustand";
import localforage from "localforage";
import { nanoid } from "nanoid";
import { AggregatorClient, normalizeOrigin } from "@/services/api/aggregator/client";
import type { Catalog } from "@/services/api/aggregator/types";
import type { IdentityRef } from "@/services/aggregator-journal";

export type StudioProfile = { id: string; name: string; origin: string; fingerprint: string; savedKey?: string };
type StudioState = {
    hydrated: boolean;
    profiles: StudioProfile[];
    activeProfileId: string;
    client: AggregatorClient | null;
    localIdentity: IdentityRef | null;
    catalog: Catalog | null;
    loading: boolean;
    error: string;
    hydrate: () => Promise<void>;
    connect: (origin: string, key: string, remember: boolean, name?: string, profileId?: string) => Promise<void>;
    disconnect: () => Promise<void>;
    refreshCatalog: () => Promise<void>;
};
const storage = localforage.createInstance({ name: "comfy-aggregator-studio", storeName: "profiles", driver: localforage.INDEXEDDB });
let connectionRevision = 0;
let hydration: Promise<void> | undefined;
const saveProfiles = (profiles: StudioProfile[], activeProfileId: string) => storage.setItem("connections", { version: 1, profiles, activeProfileId });

export const useStudioStore = create<StudioState>((set, get) => ({
    hydrated: false, profiles: [], activeProfileId: "", client: null, localIdentity: null, catalog: null, loading: false, error: "",
    hydrate: () => {
        hydration ||= (async () => {
            try {
                const saved = await storage.getItem<{ version: number; profiles: StudioProfile[]; activeProfileId: string }>("connections");
                if (saved) {
                    if (saved.version !== 1 || !Array.isArray(saved.profiles) || !saved.profiles.every((profile) => typeof profile.id === "string" && typeof profile.origin === "string" && typeof profile.fingerprint === "string" && (!profile.savedKey || typeof profile.savedKey === "string"))) throw new Error("连接配置版本不受支持，原数据已保留");
                    set({ profiles: saved.profiles, activeProfileId: saved.activeProfileId });
                    const active = saved.profiles.find((profile) => profile.id === saved.activeProfileId);
                    if (active?.savedKey) await get().connect(active.origin, active.savedKey, true, active.name, active.id);
                }
            } catch (error) { set({ error: error instanceof Error ? error.message : "连接配置读取失败" }); }
            finally { set({ hydrated: true }); }
        })();
        return hydration;
    },
    connect: async (origin, key, remember, name, profileId) => {
        const revision = ++connectionRevision;
        set({ loading: true, error: "", client: null, localIdentity: null, catalog: null });
        try {
            const client = new AggregatorClient(origin, key);
            const existing = get().profiles.find((profile) => profile.origin === client.origin && profile.fingerprint === client.fingerprint);
            const profile: StudioProfile = { id: existing?.id || nanoid(), name: name?.trim() || existing?.name || new URL(client.origin).host, origin: normalizeOrigin(origin), fingerprint: client.fingerprint, ...(remember ? { savedKey: key } : {}) };
            // The original Key unlocks only its verified local copies, not remote API access.
            set({ localIdentity: { profileId: profile.id, origin: client.origin, fingerprint: client.fingerprint } });
            const catalog = await client.catalog();
            if (revision !== connectionRevision) return;
            const previous = get().profiles.find((profile) => profile.id === profileId);
            const profiles = [...get().profiles.filter((item) => item.id !== profile.id).map((item) => {
                if (item.id !== previous?.id) return item;
                const { savedKey: _oldKey, ...metadata } = item;
                return metadata;
            }), profile];
            await saveProfiles(profiles, profile.id);
            if (revision === connectionRevision) set({ profiles, activeProfileId: profile.id, client, catalog });
        } catch (error) {
            if (revision === connectionRevision) set({ error: error instanceof Error ? error.message : "连接失败" });
            throw error;
        } finally { if (revision === connectionRevision) set({ loading: false }); }
    },
    disconnect: async () => {
        ++connectionRevision;
        const profiles = get().profiles.map(({ savedKey: _key, ...profile }) => profile.id === get().activeProfileId ? profile : { ...profile, ...( _key ? { savedKey: _key } : {}) });
        set({ client: null, localIdentity: null, catalog: null, loading: false, error: "", profiles });
        await saveProfiles(profiles, get().activeProfileId);
    },
    refreshCatalog: async () => {
        const { client } = get();
        if (!client) return;
        const revision = connectionRevision;
        set({ loading: true, error: "" });
        try {
            const catalog = await client.catalog();
            if (revision === connectionRevision) set({ catalog });
        } catch (error) { if (revision === connectionRevision) set({ error: error instanceof Error ? error.message : "目录刷新失败" }); }
        finally { if (revision === connectionRevision) set({ loading: false }); }
    },
}));
