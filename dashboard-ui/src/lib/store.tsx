// App state: live or sample mode, the connection to /api/events, per-area refresh counters and
// the live-event buffer. Views read data through useResource and never talk to the network.

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from "react";
import { apiReachable, makeApi, resolve, type Api } from "./api";
import { parseEvent, type LiveEvent } from "./live";
import type { Area } from "./types";

const AREAS: Area[] = ["pipeline", "requirements", "contract", "scenarios", "design"];
const MAX_EVENTS = 5000;

export type Connection = "live" | "offline" | "sample";

interface Dash {
  /** null while the first reachability check runs */
  sample: boolean | null;
  setSample: (on: boolean) => void;
  api: Api;
  connection: Connection;
  versions: Record<Area, number>;
  liveEvents: LiveEvent[];
}

const Ctx = createContext<Dash | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const [sample, setSample] = useState<boolean | null>(null);
  const [connection, setConnection] = useState<Connection>("offline");
  const [versions, setVersions] = useState(() => Object.fromEntries(AREAS.map((a) => [a, 0])) as Record<Area, number>);
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([]);
  const pending = useRef(new Set<Area>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (new URLSearchParams(location.search).get("sample") === "1") {
      setSample(true);
      return;
    }
    apiReachable().then((ok) => { if (!cancelled) setSample(!ok); });
    return () => { cancelled = true; };
  }, []);

  const bump = useCallback((areas: Area[]) => {
    areas.forEach((a) => pending.current.add(a));
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { // coalesce bursts of events
      const next = [...pending.current];
      pending.current.clear();
      setVersions((v) => {
        const copy = { ...v };
        next.forEach((a) => { copy[a] += 1; });
        return copy;
      });
    }, 150);
  }, []);

  useEffect(() => {
    if (sample !== false) {
      setConnection(sample ? "sample" : "offline");
      return;
    }
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    let wasOffline = false;
    const connect = () => {
      source = new EventSource(resolve("/api/events", false));
      source.onopen = () => {
        setConnection("live");
        if (wasOffline) { wasOffline = false; bump(AREAS); } // events may have been missed
      };
      // Change notifications arrive as plain messages; the design log arrives as named
      // `design` events, which onmessage never sees, so it needs its own listener.
      source.onmessage = (message) => {
        let data: unknown;
        try { data = JSON.parse(message.data); } catch { return; }
        const changed = (data as { changed?: unknown }).changed;
        if (Array.isArray(changed)) bump(changed.filter((a): a is Area => AREAS.includes(a as Area)));
        const event = parseEvent(data);
        if (event) setLiveEvents((all) => all.concat(event).slice(-MAX_EVENTS));
      };
      source.addEventListener("design", (message) => {
        let data: unknown;
        try { data = JSON.parse((message as MessageEvent).data); } catch { return; }
        const event = parseEvent(data);
        if (event) setLiveEvents((all) => all.concat(event).slice(-MAX_EVENTS));
      });
      source.onerror = () => {
        setConnection("offline");
        wasOffline = true;
        if (source && source.readyState === EventSource.CLOSED && !closed) {
          source.close();
          retry = setTimeout(connect, 3000);
        }
      };
    };
    connect();
    return () => { closed = true; if (retry) clearTimeout(retry); source?.close(); };
  }, [sample, bump]);

  const value = useMemo<Dash>(() => ({
    sample, setSample, api: makeApi(sample === true), connection, versions, liveEvents,
  }), [sample, connection, versions, liveEvents]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useDash(): Dash {
  const value = useContext(Ctx);
  if (!value) throw new Error("useDash outside DashboardProvider");
  return value;
}

export interface Resource<T> {
  status: "loading" | "ready" | "error";
  data: T | null;
  /** set when a refresh failed but older data is still shown */
  error: Error | null;
  reload: () => void;
}

/** Load one API area; refetch when the server reports a change or the mode flips. */
export function useResource<T>(area: Area | null, load: (api: Api) => Promise<T>, extraKey = ""): Resource<T> {
  const { api, sample, versions } = useDash();
  const [state, setState] = useState<Pick<Resource<T>, "status" | "data" | "error">>({ status: "loading", data: null, error: null });
  const token = useRef(0);
  const loader = useRef(load);
  loader.current = load;
  const version = area ? versions[area] : 0;

  const run = useCallback(() => {
    if (sample === null) return;
    const mine = ++token.current;
    loader.current(api).then(
      (data) => { if (mine === token.current) setState({ status: "ready", data, error: null }); },
      (error: Error) => {
        if (mine !== token.current) return;
        setState((s) => (s.data ? { ...s, error } : { status: "error", data: null, error }));
      },
    );
  }, [api, sample]);

  useEffect(() => { setState({ status: "loading", data: null, error: null }); }, [sample, extraKey]);
  useEffect(() => { run(); }, [run, version, extraKey]);
  return { ...state, reload: () => { setState((s) => (s.data ? s : { status: "loading", data: null, error: null })); run(); } };
}
