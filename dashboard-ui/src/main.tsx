import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App";

// The theme tokens switch on a `dark` class; follow the system setting (?theme=light|dark wins).
const query = new URLSearchParams(location.search).get("theme");
const media = window.matchMedia("(prefers-color-scheme: dark)");
const apply = () => document.documentElement.classList.toggle("dark", query ? query === "dark" : media.matches);
apply();
media.addEventListener("change", apply);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
