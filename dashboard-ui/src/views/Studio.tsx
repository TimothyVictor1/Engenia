// Live studio: what the agents are doing right now, or a replay of a finished run. Everything on
// screen is derived from the event list (deriveStudio), so the replay of the first N events is
// exactly what the live view showed after N events.

import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";
import { useDash } from "@/lib/store";
import { deriveStudio, parseEvent, replayDelay, type LiveEvent, type Span } from "@/lib/live";
import { fmtNumber } from "@/lib/format";
import { Card, Section } from "@/components/Bits";
import { Empty, ErrorState, Loading, PageHeader } from "@/components/States";
import { ModelViewer } from "@/components/ModelViewer";
import { StatusBadge } from "@/components/StatusBadge";

const OUTCOME_BAR: Record<Span["outcome"], string> = {
  pass: "bg-success", fail: "bg-destructive", error: "bg-warning", repair: "bg-warning", running: "bg-info animate-pulse",
};

function useSampleEvents(wanted: boolean) {
  const [state, setState] = useState<{ status: "idle" | "loading" | "ready" | "error"; events: LiveEvent[]; error: Error | null }>({ status: "idle", events: [], error: null });
  useEffect(() => {
    if (!wanted || state.status !== "idle") return;
    setState({ status: "loading", events: [], error: null });
    fetch("sample/live-events.json", { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`Server answered ${r.status}`); return r.json(); })
      .then((list: unknown) => {
        const events = (Array.isArray(list) ? list : []).map(parseEvent).filter((e): e is LiveEvent => e !== null);
        setState({ status: "ready", events, error: null });
      })
      .catch((error: Error) => setState({ status: "error", events: [], error }));
  }, [wanted, state.status]);
  return { ...state, retry: () => setState({ status: "idle", events: [], error: null }) };
}

function Timeline({ spans, start, end }: { spans: Span[]; start: number; end: number }) {
  const stages = [...new Set(spans.map((s) => s.stage))];
  const total = Math.max(end - start, 1);
  return (
    <div className="space-y-1.5" role="list" aria-label="Stage timeline">
      {stages.map((stage) => (
        <div key={stage} role="listitem" className="flex items-center gap-3">
          <span className="w-24 shrink-0 truncate font-mono text-xs">{stage}</span>
          <div className="relative h-5 flex-1 rounded bg-muted/60">
            {spans.filter((s) => s.stage === stage).map((s) => (
              <div key={s.attempt} title={`${stage} attempt ${s.attempt}: ${s.outcome}`}
                className={`absolute top-0 h-5 min-w-2 rounded text-[10px] leading-5 text-white ${OUTCOME_BAR[s.outcome]}`}
                style={{ left: `${((s.start - start) / total) * 100}%`, width: `${Math.max(((s.end - s.start) / total) * 100, 1.5)}%` }}>
                <span className="px-1">#{s.attempt}</span>
              </div>))}
          </div>
        </div>))}
    </div>
  );
}

function Code({ code }: { code: NonNullable<ReturnType<typeof deriveStudio>["code"]> }) {
  const [diff, setDiff] = useState(true);
  const changed = code.diff.some((l) => l.startsWith("+") || l.startsWith("-"));
  const lines = diff && changed ? code.diff : code.code.split("\n").map((l) => ` ${l}`);
  return (
    <Card className="p-0">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="font-mono text-xs">{code.path}</span>
        <div className="flex gap-1 text-xs">
          {(["Diff", "Full"] as const).map((l) => (
            <button key={l} aria-pressed={(l === "Diff") === diff} onClick={() => setDiff(l === "Diff")}
              className="rounded border px-2 py-0.5 hover:bg-muted aria-pressed:border-primary aria-pressed:bg-accent">{l}</button>))}
        </div>
      </div>
      <pre className="max-h-96 overflow-auto p-3 font-mono text-xs leading-5" aria-label={`Code for ${code.path}`}>
        {lines.map((l, i) => (
          <div key={i} className={l.startsWith("+") ? "bg-success/10 text-success" : l.startsWith("-") ? "bg-destructive/10 text-destructive" : ""}>
            {diff && changed ? l : l.slice(1)}{"\n"}
          </div>))}
      </pre>
    </Card>
  );
}

function FeedItem({ e }: { e: LiveEvent }) {
  const d = e.data;
  const time = new Date(e.ts).toISOString().slice(11, 19);
  const text = (v: unknown) => (v == null ? "" : String(v));
  let body;
  if (e.kind === "verdict") {
    body = (<span className="flex flex-wrap items-center gap-2"><StatusBadge status={text(d.result) || "unknown"} />
      <span className="font-mono">{text(d.check)}</span>{d.requirement ? <span className="text-muted-foreground">{text(d.requirement)}</span> : null}
      {d.measured != null && <span className="font-mono">measured {fmtNumber(d.measured)}</span>}
      {d.message ? <span className="text-muted-foreground">{text(d.message)}</span> : null}</span>);
  } else if (e.kind === "repair") {
    body = <span><b className="text-warning">Repair</b> {text(d.summary ?? d.reason ?? d.message)}</span>;
  } else if (e.kind === "tool_call") body = <span className="font-mono">{text(d.name ?? d.tool)}({text(d.args ?? d.input)})</span>;
  else if (e.kind === "tool_result") body = <span className="text-muted-foreground">{text(d.summary ?? d.result ?? d.output)}</span>;
  else if (e.kind === "decision") body = <span>Decision: {text(d.title)}</span>;
  else if (e.kind === "code") body = <span className="text-muted-foreground">wrote {text(d.path)}</span>;
  else if (e.kind === "preview") body = <span className="text-muted-foreground">preview updated</span>;
  else body = <span>{text(d.text ?? d.message)}</span>;
  const tinted = e.kind === "repair" ? "border-warning/50 bg-warning/5" : e.kind === "verdict" && d.result === "fail" ? "border-destructive/40 bg-destructive/5" : "";
  return (
    <li className={`flex gap-3 rounded-md border px-2.5 py-1.5 text-xs ${tinted}`}>
      <span className="shrink-0 font-mono text-muted-foreground">{time}</span>
      <span className="w-20 shrink-0 font-mono text-muted-foreground">{e.stage}#{e.attempt}</span>
      <span className="min-w-0 flex-1 break-words">{body}</span>
    </li>
  );
}

export function Studio() {
  const { liveEvents, sample, api, connection } = useDash();
  const [mode, setMode] = useState<"live" | "replay">(sample ? "replay" : "live");
  const [useSample, setUseSample] = useState(false);
  const sampleEvents = useSampleEvents(sample === true || useSample);
  const source = sample || useSample ? sampleEvents.events : liveEvents;
  const [cursor, setCursor] = useState<number | null>(null); // null = show everything
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const shown = mode === "live" || cursor === null ? source : source.slice(0, cursor);
  const studio = useMemo(() => deriveStudio(shown), [shown]);

  const sourceRef = useRef(source);
  sourceRef.current = source;
  useEffect(() => {
    if (!playing || cursor === null) return;
    if (cursor >= source.length) { setPlaying(false); return; }
    const wait = cursor === 0 ? 300 : replayDelay(source, cursor - 1, speed);
    const t = setTimeout(() => setCursor((c) => (c === null ? c : Math.min(c + 1, sourceRef.current.length))), wait);
    return () => clearTimeout(t);
  }, [playing, cursor, speed, source]);

  const startReplay = () => { setMode("replay"); setCursor(0); setPlaying(true); };
  const files = (studio.preview?.files ?? []).map((name) => ({ name, url: api.fileUrl(studio.preview!.stage, name, studio.preview ? Date.parse(studio.preview.ts) % 100000 : 0) }));

  if ((sample || useSample) && sampleEvents.status === "loading") return <><PageHeader title="Live studio" subtitle="" /><Loading text="Loading the sample run…" /></>;
  if ((sample || useSample) && sampleEvents.status === "error") return <><PageHeader title="Live studio" subtitle="" /><ErrorState error={sampleEvents.error} onRetry={sampleEvents.retry} /></>;

  return (
    <>
      <PageHeader title="Live studio" subtitle="Follow the agents as they design: timeline, 3D model, code, decisions and verdicts. Replay any run."
        right={<div className="flex items-center gap-2 text-xs">
          {(["live", "replay"] as const).map((m) => (
            <button key={m} aria-pressed={mode === m} disabled={m === "replay" && source.length === 0}
              onClick={() => { setMode(m); setPlaying(false); if (m === "replay") setCursor(source.length); else setCursor(null); }}
              className="rounded-md border px-3 py-1 capitalize hover:bg-muted aria-pressed:border-primary aria-pressed:bg-accent disabled:opacity-40">{m}</button>))}
        </div>} />

      {source.length === 0 ? (
        <Empty title="No live events yet">
          {connection === "live" ? "The backend publishes events while a run is in progress. Start one with " : "Not connected to a server. Start one with "}
          <code className="font-mono">engenia run</code>.
          <span className="mt-3 block"><button onClick={() => { setUseSample(true); setMode("replay"); setCursor(0); setPlaying(true); }} className="rounded-md border px-3 py-1 text-xs font-medium hover:bg-muted">Replay a sample run</button></span>
        </Empty>
      ) : (
        <>
          {mode === "replay" && (
            <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border bg-card p-2 text-xs" role="group" aria-label="Replay controls">
              <button onClick={() => (cursor !== null && cursor < source.length ? setPlaying(!playing) : startReplay())} aria-label={playing ? "Pause" : "Play"} className="rounded-md border p-1.5 hover:bg-muted">
                {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}</button>
              <button onClick={startReplay} aria-label="Restart" className="rounded-md border p-1.5 hover:bg-muted"><RotateCcw className="h-4 w-4" /></button>
              <input type="range" min={0} max={source.length} value={cursor ?? source.length} aria-label="Position in the run"
                onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)); }} className="min-w-40 flex-1 accent-[var(--primary)]" />
              <span className="font-mono">{cursor ?? source.length} / {source.length}</span>
              <label className="flex items-center gap-1">Speed
                <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="rounded border bg-card px-1 py-0.5">
                  {[0.5, 1, 2, 4].map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>
            </div>)}

          <Section title="Stage timeline">
            {studio.spans.length ? <Timeline spans={studio.spans} start={studio.start} end={studio.end} /> : <Empty title="Nothing has started yet" />}
          </Section>
          <div className="mt-8 grid gap-6 lg:grid-cols-2">
            <section><h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">3D model</h2>
              {files.length ? <ModelViewer files={files} height={340} /> : <Empty title="No preview yet">The mechanical agent has not rendered a model.</Empty>}</section>
            <section><h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">CAD code</h2>
              {studio.code ? <Code code={studio.code} /> : <Empty title="No code yet">The latest generated file and its diff appear here.</Empty>}</section>
          </div>
          <Section title="Design decisions">
            {studio.decisions.length === 0 ? <Empty title="No decisions recorded yet" /> : (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{studio.decisions.map((d, i) => (
                <Card key={`${d.ts}-${i}`} className="flex flex-col gap-2 text-sm">
                  <div className="flex items-start justify-between gap-2"><h3 className="font-semibold">{d.title || "Untitled decision"}</h3><span className="font-mono text-[10px] text-muted-foreground">{d.stage}</span></div>
                  <p><span className="text-muted-foreground">Choice: </span>{d.choice || "—"}</p>
                  {d.reason && <p className="text-muted-foreground">{d.reason}</p>}
                  {d.rejected.length > 0 && <div><p className="text-xs font-medium">Rejected</p><ul className="ml-4 list-disc text-xs text-muted-foreground">
                    {d.rejected.map((r, j) => <li key={j}>{r.option}{r.why ? ` — ${r.why}` : ""}</li>)}</ul></div>}
                  {d.requirements.length > 0 && <div className="mt-auto flex flex-wrap gap-1">{d.requirements.map((id) => (
                    <a key={id} href={`#/requirements/${encodeURIComponent(id)}`} className="rounded border px-1.5 font-mono text-xs hover:border-primary hover:text-primary">{id}</a>))}</div>}
                </Card>))}</div>)}
          </Section>
          <Section title="Events"><ol className="space-y-1">{[...studio.feed].reverse().slice(0, 200).map((e, i) => <FeedItem key={`${e.ts}-${i}`} e={e} />)}</ol></Section>
        </>
      )}
    </>
  );
}
