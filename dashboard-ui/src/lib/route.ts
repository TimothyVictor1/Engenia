import { useEffect, useState } from "react";

export const VIEWS = ["mission", "pipeline", "requirements", "design", "contract", "scenarios", "studio"] as const;
export type View = (typeof VIEWS)[number];

export function parseHash(hash: string): { view: View; param: string } {
  const [, view = "", param = ""] = hash.replace(/^#/, "").split("/");
  let decoded = param;
  try { decoded = decodeURIComponent(param); } catch { /* keep the raw text */ }
  return { view: (VIEWS as readonly string[]).includes(view) ? (view as View) : "mission", param: decoded };
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (view: View, param = "") => { location.hash = `#/${view}${param ? `/${encodeURIComponent(param)}` : ""}`; };
