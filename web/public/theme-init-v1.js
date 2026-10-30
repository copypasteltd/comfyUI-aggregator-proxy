// Runs before paint under the existing script-src 'self' policy.
try {
    var studioSavedTheme = JSON.parse(localStorage.getItem("infinite-canvas:theme_store") || "{}");
    var studioTheme = studioSavedTheme.state && studioSavedTheme.state.theme === "dark" ? "dark" : "light";
    document.documentElement.classList.toggle("dark", studioTheme === "dark");
    document.documentElement.style.colorScheme = studioTheme;
} catch (error) {}
