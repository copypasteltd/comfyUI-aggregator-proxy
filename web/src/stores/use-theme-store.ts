import { create } from "zustand";
import { persist } from "zustand/middleware";
import { MANAGED_STUDIO } from "@/constant/studio";

export type ThemeName = "light" | "dark";

type ThemeStore = {
    theme: ThemeName;
    setTheme: (theme: ThemeName) => void;
};

export const useThemeStore = create<ThemeStore>()(
    persist(
        (set) => ({
            theme: MANAGED_STUDIO ? "light" : "dark",
            setTheme: (theme) => set({ theme }),
        }),
        { name: "infinite-canvas:theme_store", merge: (saved, current) => {
            const theme = (saved as Partial<ThemeStore> | undefined)?.theme;
            return { ...current, ...(theme === "light" || theme === "dark" ? { theme } : {}) };
        } },
    ),
);
