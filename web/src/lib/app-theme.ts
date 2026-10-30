import type { ThemeConfig } from "antd";
import { theme as antdTheme } from "antd";
import { MANAGED_STUDIO } from "@/constant/studio";
import { lime, limeThemes } from "@/lib/lime-theme";

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
    if (MANAGED_STUDIO) {
        const color = limeThemes[dark ? "dark" : "light"];
        return {
            algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
            cssVar: { key: dark ? "comfy-studio-lime-dark" : "comfy-studio-lime-light" },
            token: { colorPrimary: lime.primary, colorPrimaryHover: lime.hover, colorPrimaryActive: lime.hover, colorInfo: color.ink, colorInfoBg: color.canvas, colorInfoBorder: lime.primary, colorInfoText: color.ink, colorText: color.ink, colorTextSecondary: color.muted, colorTextLightSolid: lime.ink, colorLink: color.ink, colorLinkHover: color.muted, colorBgLayout: color.canvas, colorBgContainer: color.card, colorBgElevated: color.card, colorBorder: color.border, colorError: color.error, colorWarning: lime.warning, controlItemBgActive: color.selection, controlItemBgHover: color.canvas, borderRadius: 14, controlHeight: 40, fontFamily: 'Manrope, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif' },
            components: { Button: { primaryShadow: "none", defaultShadow: "none", fontWeight: 650, defaultColor: color.ink, defaultHoverColor: color.ink, defaultActiveColor: color.ink, defaultHoverBorderColor: color.ink, defaultActiveBorderColor: color.ink, defaultHoverBg: color.canvas, defaultActiveBg: color.canvas, primaryColor: lime.ink }, Select: { optionSelectedBg: lime.primary, optionSelectedColor: lime.ink, optionActiveBg: color.canvas }, Dropdown: { colorBgElevated: color.card, controlItemBgHover: color.canvas, controlItemBgActive: color.selection }, Menu: { itemSelectedBg: color.selection, itemSelectedColor: color.ink, itemHoverBg: color.canvas }, Drawer: { colorBgElevated: color.canvas }, Modal: { contentBg: color.canvas, headerBg: color.canvas }, Tag: { defaultBg: color.canvas, defaultColor: color.ink } },
        };
    }
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
