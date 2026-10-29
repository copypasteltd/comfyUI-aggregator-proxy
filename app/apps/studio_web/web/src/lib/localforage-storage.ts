import localforage from "localforage";
import type { StateStorage } from "zustand/middleware";
import { clearStudioDocument, isStudioDocument, readStudioDocument, writeStudioDocument } from "@/services/studio-persistence";

localforage.config({
    name: "infinite-canvas",
    storeName: "app_state",
});

export const localForageStorage: StateStorage = {
    getItem: async (name) => {
        if (typeof window === "undefined") return null;
        if (isStudioDocument(name)) return readStudioDocument(name);
        try {
            return (await localforage.getItem<string>(name)) || null;
        } catch {
            return window.localStorage.getItem(name);
        }
    },
    setItem: async (name, value) => {
        if (typeof window === "undefined") return;
        if (isStudioDocument(name)) { writeStudioDocument(name, value); return; }
        try {
            await localforage.setItem(name, value);
        } catch {
            window.localStorage.setItem(name, value);
        }
    },
    removeItem: async (name) => {
        if (typeof window === "undefined") return;
        if (isStudioDocument(name)) { clearStudioDocument(name); return; }
        try {
            await localforage.removeItem(name);
        } catch {
            window.localStorage.removeItem(name);
        }
    },
};
