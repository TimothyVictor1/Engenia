import { useCallback, useEffect, useState } from "react";

const read = () => new URLSearchParams(location.search).get("present") === "1";

/** Presentation mode: chrome hidden, bigger type. Kept in the URL so a reload stays in it. */
export function usePresentation() {
  const [on, setOn] = useState(read);
  useEffect(() => {
    const sync = () => setOn(read());
    window.addEventListener("engenia-present", sync);
    window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("engenia-present", sync); window.removeEventListener("popstate", sync); };
  }, []);
  const set = useCallback((next: boolean) => {
    const url = new URL(location.href);
    if (next) url.searchParams.set("present", "1"); else url.searchParams.delete("present");
    history.replaceState(null, "", url);
    window.dispatchEvent(new Event("engenia-present"));
    try {
      if (next) void document.documentElement.requestFullscreen?.();
      else if (document.fullscreenElement) void document.exitFullscreen();
    } catch { /* fullscreen is optional */ }
  }, []);
  return [on, set] as const;
}
