import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "projector";
const KEY = "engenia-theme";
const systemDark = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
const valid = (v: string | null): v is Theme => v === "light" || v === "dark" || v === "projector";

/** ?theme= wins, then the saved choice, then the system setting. */
export function initialTheme(): Theme {
  const query = new URLSearchParams(location.search).get("theme");
  if (valid(query)) return query;
  try { const saved = localStorage.getItem(KEY); if (valid(saved)) return saved; } catch { /* storage is optional */ }
  return systemDark() ? "dark" : "light";
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("projector", theme === "projector");
  root.classList.toggle("light", theme !== "dark");
  root.dataset.theme = theme;
  window.dispatchEvent(new Event("engenia-theme"));
}

/** The projector theme: light background, heavier borders and larger type for a room. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) || initialTheme());
  useEffect(() => {
    const sync = () => setTheme((document.documentElement.dataset.theme as Theme) || "light");
    window.addEventListener("engenia-theme", sync);
    return () => window.removeEventListener("engenia-theme", sync);
  }, []);
  const set = useCallback((next: Theme) => {
    applyTheme(next);
    try { localStorage.setItem(KEY, next); } catch { /* storage is optional */ }
  }, []);
  /** Toggle projector on and off, returning to the theme before it. */
  const toggleProjector = useCallback(() => {
    if (theme === "projector") set(systemDark() ? "dark" : "light");
    else set("projector");
  }, [theme, set]);
  return { theme, set, projector: theme === "projector", toggleProjector };
}
