import type { ThemeConfig } from "antd";
import { theme as antdTheme } from "antd";
import { MANAGED_STUDIO } from "@/constant/studio";
import { lime } from "@/lib/lime-theme";

const neutral = {
    light: {
        primary: "#171717",
        primaryHover: "#000000",
        primaryText: "#ffffff",
        elevatedBg: "#ffffff",
        itemHoverBg: "rgba(23, 23, 23, 0.06)",
        itemSelectedBg: "rgba(23, 23, 23, 0.1)",
        itemSelectedHoverBg: "rgba(23, 23, 23, 0.14)",
        itemText: "#171717",
        tableSelectedBg: "rgba(17, 17, 17, 0.05)",
        tableSelectedHoverBg: "rgba(17, 17, 17, 0.08)",
    },
    dark: {
        primary: "#fafafa",
        primaryHover: "#ffffff",
        primaryText: "#171717",
        elevatedBg: "#1c1917",
        itemHoverBg: "rgba(250, 250, 249, 0.08)",
        itemSelectedBg: "rgba(250, 250, 249, 0.12)",
        itemSelectedHoverBg: "rgba(250, 250, 249, 0.16)",
        itemText: "#fafafa",
        tableSelectedBg: "rgba(255, 255, 255, 0.08)",
        tableSelectedHoverBg: "rgba(255, 255, 255, 0.12)",
    },
};

export function getAntThemeConfig(dark: boolean): ThemeConfig {
    if (MANAGED_STUDIO) return {
        algorithm: antdTheme.defaultAlgorithm,
        cssVar: { key: "comfy-studio-lime" },
        token: { colorPrimary: lime.primary, colorPrimaryHover: lime.hover, colorPrimaryActive: lime.hover, colorInfo: lime.ink, colorInfoBg: lime.canvas, colorInfoBorder: lime.primary, colorInfoText: lime.ink, colorText: lime.ink, colorTextSecondary: lime.muted, colorTextLightSolid: lime.ink, colorLink: lime.ink, colorLinkHover: lime.muted, colorBgLayout: lime.canvas, colorBgContainer: lime.card, colorBgElevated: lime.card, colorBorder: "#dadad0", colorError: "#b33215", colorWarning: lime.warning, controlItemBgActive: lime.primary, controlItemBgHover: lime.canvas, borderRadius: 14, controlHeight: 40, fontFamily: 'Manrope, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif' },
        components: { Button: { primaryShadow: "none", defaultShadow: "none", fontWeight: 650 }, Select: { optionSelectedBg: lime.primary, optionSelectedColor: lime.ink, optionActiveBg: lime.canvas }, Dropdown: { controlItemBgHover: lime.canvas, controlItemBgActive: lime.primary }, Menu: { itemSelectedBg: lime.primary, itemSelectedColor: lime.ink, itemHoverBg: lime.canvas }, Drawer: { colorBgElevated: lime.canvas }, Modal: { contentBg: lime.canvas, headerBg: lime.canvas }, Tag: { defaultBg: lime.primary, defaultColor: lime.ink } },
    };
    const color = dark ? neutral.dark : neutral.light;

    return {
        algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        cssVar: { key: dark ? "infinite-canvas-dark" : "infinite-canvas-light" },
        token: {
            colorPrimary: color.primary,
            colorInfo: color.primary,
            colorLink: color.primary,
            colorLinkHover: color.primaryHover,
            colorLinkActive: color.primary,
            colorTextLightSolid: color.primaryText,
            colorBgElevated: color.elevatedBg,
            controlItemBgHover: color.itemHoverBg,
            controlItemBgActive: color.itemSelectedBg,
            controlItemBgActiveHover: color.itemSelectedHoverBg,
        },
        components: {
            Button: {
                primaryShadow: "none",
            },
            Dropdown: {
                colorBgElevated: color.elevatedBg,
                colorText: color.itemText,
                controlItemBgHover: color.itemHoverBg,
                controlItemBgActive: color.itemSelectedBg,
                controlItemBgActiveHover: color.itemSelectedHoverBg,
            },
            Menu: {
                popupBg: color.elevatedBg,
                itemActiveBg: color.itemSelectedBg,
                itemHoverBg: color.itemHoverBg,
                itemSelectedBg: color.itemSelectedBg,
                itemSelectedColor: color.itemText,
                darkPopupBg: neutral.dark.elevatedBg,
                darkItemHoverBg: neutral.dark.itemHoverBg,
                darkItemSelectedBg: neutral.dark.itemSelectedBg,
                darkItemSelectedColor: neutral.dark.itemText,
            },
            Select: {
                optionActiveBg: color.itemHoverBg,
                optionSelectedBg: color.itemSelectedBg,
                optionSelectedColor: color.itemText,
            },
            Table: {
                rowSelectedBg: color.tableSelectedBg,
                rowSelectedHoverBg: color.tableSelectedHoverBg,
            },
        },
    };
}
