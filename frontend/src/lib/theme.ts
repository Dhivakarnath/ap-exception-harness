/**
 * Theme persistence helpers. The design system defines both dark and light on
 * the same tokens (the UI flips by toggling `.dark` on <html>); these functions
 * read/apply the stored choice. Kept out of the component file so fast-refresh
 * stays happy (a module should export only components or only helpers).
 */
export type Theme = "dark" | "light";

const KEY = "ap-theme";

/** The stored theme, defaulting to dark — the control-room default. */
export function initialTheme(): Theme {
  const stored = localStorage.getItem(KEY);
  if (stored === "light" || stored === "dark") return stored;
  return "dark";
}

/** Persist and apply a theme to the document root. */
export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  localStorage.setItem(KEY, theme);
}

/** Apply the stored theme (call before first paint to avoid a flash). */
export function applyStoredTheme(): void {
  document.documentElement.classList.toggle("dark", initialTheme() === "dark");
}
