// Tokens copied from the immutable 04 · 青柠 reference; adaptations live outside reference/.
export const lime = { canvas: "#f2f1ec", ink: "#0c0c0c", muted: "#55554f", faint: "#8a8a83", primary: "#d2f53c", hover: "#c2ea1e", warning: "#ff5b35", card: "#ffffff", line: "rgba(12,12,12,.1)" };
export const limeThemes = {
    light: { ...lime, border: "#dadad0", error: "#b33215", selection: "rgba(210,245,60,.24)" },
    dark: { ...lime, canvas: "#171914", ink: "#f2f1ec", muted: "#b9bdb2", faint: "#919787", card: "#22251f", line: "rgba(242,241,236,.14)", border: "#444a3d", error: "#ff9b80", selection: "rgba(210,245,60,.16)" },
};
export const limeCanvasTheme = {
    canvas: { background: lime.canvas, dot: "rgba(12,12,12,.22)", line: "rgba(12,12,12,.08)", selectionStroke: lime.ink, selectionFill: "rgba(210,245,60,.24)" },
    node: { label: lime.muted, fill: lime.card, panel: lime.card, stroke: "#dadad0", activeStroke: lime.ink, placeholder: lime.faint, text: lime.ink, muted: lime.muted, faint: lime.faint },
    toolbar: { panel: lime.card, border: "#dadad0", item: lime.ink, itemHover: "#f2f1ec", activeBg: lime.primary, activeText: lime.ink },
} as const;
export const limeDarkCanvasTheme = {
    canvas: { background: limeThemes.dark.canvas, dot: "rgba(242,241,236,.22)", line: "rgba(242,241,236,.08)", selectionStroke: lime.primary, selectionFill: limeThemes.dark.selection },
    node: { label: limeThemes.dark.muted, fill: limeThemes.dark.card, panel: limeThemes.dark.card, stroke: limeThemes.dark.border, activeStroke: lime.primary, placeholder: limeThemes.dark.faint, text: limeThemes.dark.ink, muted: limeThemes.dark.muted, faint: limeThemes.dark.faint },
    toolbar: { panel: limeThemes.dark.card, border: limeThemes.dark.border, item: limeThemes.dark.ink, itemHover: limeThemes.dark.canvas, activeBg: lime.primary, activeText: lime.ink },
} as const;
