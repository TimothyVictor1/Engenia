import { useEffect, useRef, useState } from "react";
import { replayDelay, type LiveEvent } from "./live";

/** Replay a finished run: how many events are shown (null = all), play state and speed. */
export function useReplay(events: LiveEvent[]) {
  const [cursor, setCursor] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const ref = useRef(events);
  ref.current = events;
  useEffect(() => {
    if (!playing || cursor === null) return;
    if (cursor >= events.length) { setPlaying(false); return; }
    const wait = cursor === 0 ? 300 : replayDelay(events, cursor - 1, speed);
    const t = setTimeout(() => setCursor((c) => (c === null ? c : Math.min(c + 1, ref.current.length))), wait);
    return () => clearTimeout(t);
  }, [playing, cursor, speed, events]);
  return {
    cursor, playing, speed, setSpeed,
    shown: cursor === null ? events : events.slice(0, cursor),
    start: () => { setCursor(0); setPlaying(true); },
    toggle: () => setPlaying((p) => !p),
    seek: (n: number) => { setPlaying(false); setCursor(n); },
    live: () => { setPlaying(false); setCursor(null); },
  };
}
