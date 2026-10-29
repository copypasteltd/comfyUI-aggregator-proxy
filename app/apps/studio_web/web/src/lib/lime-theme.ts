// Tokens copied from the immutable 04 · 青柠 reference; adaptations live outside reference/.
export const lime = { canvas: "#f2f1ec", ink: "#0c0c0c", muted: "#55554f", faint: "#8a8a83", primary: "#d2f53c", hover: "#c2ea1e", warning: "#ff5b35", card: "#ffffff", line: "rgba(12,12,12,.1)" };
export const limeCanvasTheme = {
    canvas: { background: lime.canvas, dot: "rgba(12,12,12,.22)", line: "rgba(12,12,12,.08)", selectionStroke: lime.ink, selectionFill: "rgba(210,245,60,.24)" },
    node: { label: lime.muted, fill: lime.card, panel: lime.card, stroke: "#dadad0", activeStroke: lime.ink, placeholder: lime.faint, text: lime.ink, muted: lime.muted, faint: lime.faint },
    toolbar: { panel: lime.card, border: "#dadad0", item: lime.ink, itemHover: "#f2f1ec", activeBg: lime.primary, activeText: lime.ink },
} as const;
