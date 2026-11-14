// Mission Control: the demo view. Everything on it comes from real events (/api/events and
// /api/events/history), /api/agents and /api/run/summary. A value the server does not provide is
// shown as "not recorded yet"; nothing is estimated or filled in.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize, Minimize, Pause, Play, RotateCcw } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useDash, useResource } from "@/lib/store";
import { parseEvent, type LiveEvent } from "@/lib/live";
import { breakSteps, deriveMission, latestRun, mergeEvents, type AgentsResponse, type Mission as MissionState, type RunSummary, type StageState } from "@/lib/mission";
import { useReplay } from "@/lib/replay";
import { usePresentation } from "@/lib/present";
import { fmtDuration } from "@/lib/format";
import { Section } from "@/components/Bits";
import { Empty, ErrorState, Loading } from "@/components/States";
import { Graph, type ArcSpec, type NodeStatus, type NodeView } from "@/components/mission/Graph";
import { Agents, BreakIt, Layers, Legend, Loops, Ticker } from "@/components/mission/Panels";

type Polled<T> = { status: "loading" | "ready" | "error" | "unavailable"; data: T | null; error: Error | null };

/** Fetch now and then every `ms` (0 = once). 404 and 501 mean the server lacks the route. */
function usePoll<T>(load: () => Promise<T>, ms: number, key: string): Polled<T> {
  const [state, setState] = useState<Polled<T>>({ status: "loading", data: null, error: null });
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    let dead = false;
    setState({ status: "loading", data: null, error: null });
    const run = () => loader.current().then(
      (data) => { if (!dead) setState({ status: "ready", data, error: null }); },
      (error: Error) => {
        if (dead) return;
        const missing = error instanceof ApiError && (error.status === 404 || error.status === 501);
        setState((s) => (missing ? { status: "unavailable", data: null, error } : s.data ? { ...s, error } : { status: "error", data: null, error }));
      });
    run();
    const t = ms > 0 ? setInterval(run, ms) : null;
    return () => { dead = true; if (t) clearInterval(t); };
  }, [ms, key]);
  return state;
}

function nodeStatus(state: StageState | undefined, api: string | undefined, fromEvents: boolean): NodeStatus {
  if (state?.active) return "running";
  if (state) {
    if (state.cacheHit === true) return "cached";
    const failing = state.checks.some((c) => c.result !== "pass");
    const published = state.phases.some((p) => p.phase === "publish" && p.result === "ok");
    if (failing && !published) return "failed";
    if (published) return "done";
    return "started";
  }
  if (fromEvents) return "pending";
  return ((api as NodeStatus | undefined) ?? "not_run");
}

function buildArcs(m: MissionState, summary: RunSummary | null, names: Set<string>): ArcSpec[] {
  const near = (loop: "repair" | "change_request" | "proposal" | "calibration") => m.count - m.firing[loop] <= 3 && m.firing[loop] > 0;
  const arcs: ArcSpec[] = [];
  const crStages = new Set(["mechanical", "electronics", "firmware", ...m.changeRequests.map((c) => c.stage)]);
  [...crStages].filter((s) => names.has(s) && s !== "architect").forEach((stage, i) => {
    const mine = m.changeRequests.filter((c) => c.stage === stage);
    const last = mine[mine.length - 1];
    arcs.push({
      id: `cr-${stage}`, loop: "change_request", from: stage, to: "architect", row: i % 3,
      label: last ? `${last.id} ${last.status}` : "change request", lit: !!last,
      tone: !last ? "info" : last.status === "accepted" ? "ok" : last.status === "rejected" ? "bad" : "warn",
      pulse: !!last && near("change_request") && m.changeRequests[m.changeRequests.length - 1] === last, count: mine.length,
    });
  });
  if (names.has("integration") && names.has("verify_plan")) {
    const p = m.proposals[m.proposals.length - 1];
    arcs.push({ id: "proposal", loop: "proposal", from: p && names.has(p.stage) ? p.stage : "integration", to: "verify_plan", row: 3, label: "requirement proposals", lit: m.proposals.length > 0, tone: "warn", pulse: near("proposal"), count: m.proposals.length });
  }
  if (names.has("report") && names.has("simulation")) {
    const n = summary?.loops?.calibration?.measurements ?? 0;
    arcs.push({ id: "cal", loop: "calibration", from: "report", to: "simulation", row: 4, label: "measure → calibrate", lit: n > 0, tone: "ok", pulse: false, count: n });
  }
  return arcs;
}

function Detail({ s, onClose }: { s: StageState | undefined; onClose: () => void }) {
  if (!s) return <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">No events recorded yet for this stage. <button onClick={onClose} className="underline">Close</button></p>;
  return (
    <div className="rounded-lg border bg-card p-3 text-sm" aria-label={`Stage ${s.name} detail`}>
      <div className="mb-2 flex items-center justify-between"><h3 className="font-mono font-semibold">{s.name}</h3><button onClick={onClose} className="rounded border px-2 py-0.5 text-xs hover:bg-muted">Close</button></div>
      <table className="w-full text-xs"><thead className="text-left text-muted-foreground"><tr><th className="py-1">Phase</th><th>Attempt</th><th>Result</th><th>Duration</th></tr></thead>
        <tbody>{s.phases.map((p, i) => <tr key={i} className="border-t"><td className="py-1 font-mono">{p.phase}</td><td>{p.attempt}</td><td>{p.result ?? "running"}</td><td className="font-mono">{p.durationMs != null ? fmtDuration(p.durationMs / 1000) : "—"}</td></tr>)}</tbody></table>
      {s.phases.length === 0 && <p className="text-muted-foreground">No phases recorded yet.</p>}
    </div>
  );
}

export function Mission() {
  const { sample, api, liveEvents, connection } = useDash();
  const [present, setPresent] = usePresentation();
  const pipeline = useResource("pipeline", (a) => a.pipeline());
  const history = usePoll(() => api.eventsHistory(), 0, `h${sample}`);
  const [extra, setExtra] = useState<LiveEvent[]>([]);
  const agents = usePoll<AgentsResponse>(() => api.agents(), sample ? 0 : 3000, `a${sample}`);
  const summary = usePoll<RunSummary>(() => api.runSummary(), sample ? 0 : 3000, `s${sample}`);
  const faults = usePoll(() => api.demoFaults(), 0, `f${sample}`);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [breakError, setBreakError] = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const events = useMemo(() => {
    const parsed = (history.data?.events ?? []).map(parseEvent).filter((e): e is LiveEvent => e !== null);
    return latestRun(mergeEvents(parsed, sample ? extra : [...liveEvents, ...extra]));
  }, [history.data, liveEvents, extra, sample]);
  const replay = useReplay(events);
  const replaying = replay.cursor !== null;
  const mission = useMemo(() => deriveMission(replay.shown), [replay.shown]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest("input, select, textarea")) return;
      if (e.key === "p" || e.key === "P") setPresent(!present);
      else if (e.key === "Escape" && present) setPresent(false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [present, setPresent]);

  const names = useMemo(() => {
    const fromApi = pipeline.data?.stages.map((s) => s.name) ?? [];
    return new Set(fromApi.length ? fromApi : Object.keys(mission.stages));
  }, [pipeline.data, mission.stages]);
  const nodes: NodeView[] = useMemo(() => {
    const api_ = new Map((pipeline.data?.stages ?? []).map((s) => [s.name, s]));
    return [...names].map((name) => ({
      name, needs: api_.get(name)?.needs ?? [], state: mission.stages[name] ?? null, attempts: api_.get(name)?.attempts ?? null,
      status: nodeStatus(mission.stages[name], api_.get(name)?.status, replaying || sample === true || events.length > 0),
    }));
  }, [names, pipeline.data, mission.stages, replaying, sample, events.length]);
  // The summary describes the end of the run. While replaying it must not leak that end state
  // into an earlier moment, so replay shows only what the events up to the cursor say.
  const shownSummary = replaying ? null : summary.data;
  const arcs = useMemo(() => buildArcs(mission, shownSummary, names), [mission, shownSummary, names]);

  const lit = { repair: mission.counters.repairs > 0, change_request: mission.changeRequests.length > 0, proposal: mission.proposals.length > 0, calibration: (shownSummary?.loops?.calibration?.measurements ?? 0) > 0 };
  const steps = useMemo(() => breakSteps(replay.shown, mission), [replay.shown, mission]);
  const fault = faults.data?.faults?.[0];
  const unavailable = faults.status === "unavailable" ? "Fault injection is not available on this server yet."
    : faults.status === "ready" && !faults.data?.faults?.length ? "This server has no faults to inject."
    : faults.status === "error" ? "Could not check whether fault injection is available." : null;

  const onBreak = useCallback(async () => {
    setBreakError(null); setBusy(true);
    try {
      if (sample) {
        const r = await fetch("sample/events/fault.json", { cache: "no-store" });
        const list = ((await r.json()) as unknown[]).map(parseEvent).filter((e): e is LiveEvent => e !== null);
        replay.live();
        list.forEach((e, i) => timers.current.push(setTimeout(() => setExtra((x) => x.concat(e)), 700 * (i + 1))));
        timers.current.push(setTimeout(() => setBusy(false), 700 * (list.length + 1)));
        return;
      }
      await api.breakIt(fault?.id);
      replay.live();
    } catch (error) {
      setBreakError(error instanceof ApiError && error.status === 409 ? `A run is already in progress. ${error.message}` : (error as Error).message);
    } finally {
      if (!sample) setBusy(false);
    }
  }, [api, fault?.id, replay, sample]);

  const activeIds = replaying || sample ? new Set(Object.values(mission.stages).filter((s) => s.active).map((s) => s.name)) : undefined;
  const runId = events[0]?.run_id ?? summary.data?.run_id ?? null;
  const running = Object.values(mission.stages).some((s) => s.active);
  const status = running ? "running" : events.length ? (mission.counters.checksFailed && !mission.gate.audit ? "needs attention" : "finished") : "no run yet";

  return (
    <div className={present ? "text-[1.08rem]" : ""} data-present={present}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Mission Control</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {runId != null ? <>Run <b className="font-mono">{String(runId)}</b> · <span data-testid="run-status">{status}</span> · {events.length} real events{replaying ? " · replaying" : ""}</> : "No run recorded yet. Start one with engenia run."}
            {sample && <span className="ml-2 rounded bg-info/10 px-1.5 py-0.5 text-xs text-info">Sample data</span>}
            {connection === "offline" && !sample && <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-xs">Offline</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {!present && events.length > 0 && (replaying ? (
            <button onClick={replay.live} className="rounded-md border px-3 py-1 hover:bg-muted">Back to live</button>
          ) : (
            <button onClick={replay.start} className="flex items-center gap-1.5 rounded-md border px-3 py-1 hover:bg-muted"><RotateCcw className="h-3.5 w-3.5" /> Replay this run</button>))}
          <button onClick={() => setPresent(!present)} aria-pressed={present} className="flex items-center gap-1.5 rounded-md border px-3 py-1 hover:bg-muted">
            {present ? <Minimize className="h-3.5 w-3.5" /> : <Maximize className="h-3.5 w-3.5" />}{present ? "Exit presentation" : "Presentation mode"}</button>
        </div>
      </div>

      {replaying && !present && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border bg-card p-2 text-xs" role="group" aria-label="Replay controls">
          <button onClick={() => (replay.cursor !== null && replay.cursor < events.length ? replay.toggle() : replay.start())} aria-label={replay.playing ? "Pause" : "Play"} className="rounded-md border p-1.5 hover:bg-muted">{replay.playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}</button>
          <button onClick={replay.start} aria-label="Restart" className="rounded-md border p-1.5 hover:bg-muted"><RotateCcw className="h-4 w-4" /></button>
          <input type="range" min={0} max={events.length} value={replay.cursor ?? events.length} aria-label="Position in the run" onChange={(e) => replay.seek(Number(e.target.value))} className="min-w-40 flex-1 accent-[var(--primary)]" />
          <span className="font-mono">{replay.cursor} / {events.length}</span>
          <label className="flex items-center gap-1">Speed <select value={replay.speed} onChange={(e) => replay.setSpeed(Number(e.target.value))} className="rounded border bg-card px-1 py-0.5">{[0.5, 1, 2, 4].map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>
        </div>)}

      {history.status === "error" && events.length === 0 && <div className="mb-4"><ErrorState error={history.error} /></div>}
      {history.status === "loading" && events.length === 0 && !sample ? <Loading text="Loading the run…" /> : (
        <div className="space-y-4">
          <Layers mission={mission} summary={shownSummary} />
          {nodes.length === 0 ? <Empty title="No pipeline yet">The server returned no stages.</Empty>
            : <Graph nodes={nodes} mission={mission} arcs={arcs} selected={selected} onSelect={setSelected} fill={present} />}
          {events.length === 0 && <Empty title="No events recorded yet">Events appear here live while a run is in progress, and stay available to replay afterwards.</Empty>}
          {selected && <Detail s={mission.stages[selected]} onClose={() => setSelected(null)} />}
          <Loops mission={mission} summary={shownSummary} lit={lit} />
          <Section title="Agents">
            {agents.status === "loading" ? <Loading /> : agents.status === "unavailable" ? <Empty title="Agents: not available yet">This server does not provide /api/agents, so no agent cards are shown.</Empty>
              : agents.status === "error" || !agents.data ? <ErrorState error={agents.error} /> : <Agents data={agents.data} activeIds={activeIds} />}
          </Section>
          {!present && <Section title="Break it"><BreakIt steps={steps} onBreak={onBreak} busy={busy} unavailable={unavailable} error={breakError} sample={sample === true} /></Section>}
          {present && <BreakIt steps={steps} onBreak={onBreak} busy={busy} unavailable={unavailable} error={breakError} sample={sample === true} />}
          <Ticker lines={mission.ticker} present={present} />
          {!present && <Section title="The eight rules"><Legend /></Section>}
        </div>)}
    </div>
  );
}
