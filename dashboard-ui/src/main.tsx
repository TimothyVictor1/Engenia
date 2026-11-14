import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App";
import { applyTheme, initialTheme } from "./lib/theme";

// Theme tokens switch on the `dark` and `projector` classes (see lib/theme.ts).
applyTheme(initialTheme());
window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener("change", () => {
  let saved: string | null = null;
  try { saved = localStorage.getItem("engenia-theme"); } catch { /* storage is optional */ }
  if (!new URLSearchParams(location.search).get("theme") && !saved) applyTheme(initialTheme());
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
