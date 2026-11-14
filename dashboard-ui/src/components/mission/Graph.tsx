// The zoomable pipeline graph: stages grouped in the three verification bands, each stage with its
// phase strip, parallel lanes, cache hits, and the feedback loops drawn as arcs that light up when
// they fire. Layout positions are configuration; every state shown comes from events or the API.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Minus, Plus, Maximize2 } from "lucide-react";
import { PHASES, type LoopId, type Mission, type StageState } from "@/lib/mission";
import { fmtDuration } from "@/lib/format";
import { Tip } from "./Tip";

const W = 196, H = 112, GX = 78, GY = 18, PAD = 20, BAND_TOP = 34, ARC_ROOM = 150;
const COLUMN: Record<string, number> = {
  verify_plan: 0, architect: 1, mechanical: 2, electronics: 2, firmware: 2, redteam: 2, testplan: 2,
  simprep: 3, integration: 3, simulation: 4, final: 5, report: 6,
};
const BANDS = [
  { id: "before", title: "BEFORE", sub: "verify the plan", cols: [0, 0] },
  { id: "during", title: "DURING", sub: "scripts check every stage", cols: [1, 4] },
  { id: "after", title: "AFTER", sub: "gate and final audit", cols: [5, 6] },
] as const;
const BAND_TINT: Record<string, string> = { before: "bg-info/5", during: "bg-muted/40", after: "bg-success/5" };

export type NodeStatus = "pending" | "running" | "done" | "cached" | "failed" | "blocked" | "started" | "not_run";

export interface NodeView {
  name: string; needs: string[]; status: NodeStatus; state: StageState | null; attempts: number | null;
}

const PHASE_TONE = { ok: "bg-success", fail: "bg-destructive", skipped: "bg-muted-foreground/30" } as const;

function PhaseStrip({ state, detailed }: { state: StageState | null; detailed: boolean }) {
  return (
    <div className="mt-1.5 flex gap-[3px]" role="list" aria-label="Phases">
      {PHASES.map((p) => {
        const runs = (state?.phases ?? []).filter((r) => r.phase === p);
        const last = runs[runs.length - 1];
        const active = state?.active === p;
        const cls = active ? "bg-info animate-pulse" : last?.result ? PHASE_TONE[last.result] : "bg-border";
        const ms = runs.reduce((n, r) => n + (r.durationMs ?? 0), 0);
        return (
          <div key={p} role="listitem" title={`${p}${runs.length ? ` · ${runs.length}×` : ""}${ms ? ` · ${fmtDuration(ms / 1000)}` : ""}`} className="min-w-0 flex-1">
            <div className={`h-2 rounded-sm ${cls} ${!last && !active ? "opacity-60" : ""}`} data-phase={p} data-state={active ? "active" : last?.result ?? "idle"} />
            {detailed && <div className="mt-0.5 truncate text-center font-mono text-[7px] leading-none text-muted-foreground">{p.slice(0, 3)}</div>}
            {detailed && ms > 0 && <div className="truncate text-center font-mono text-[7px] leading-none text-muted-foreground">{fmtDuration(ms / 1000).replace(" ", "")}</div>}
          </div>
        );
      })}
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = {
  running: "text-info", done: "text-success", cached: "text-success", failed: "text-destructive",
  blocked: "text-muted-foreground", pending: "text-muted-foreground", started: "text-info", not_run: "text-muted-foreground",
};

function Node({ n, x, y, k, selected, onSelect }: { n: NodeView; x: number; y: number; k: number; selected: boolean; onSelect: () => void }) {
  const s = n.state;
  const detailed = k >= 1.4;
  const dur = s && s.phases.length ? s.phases.reduce((t, p) => t + (p.durationMs ?? 0), 0) : null;
  return (
    <div data-stage={n.name} data-status={n.status} style={{ left: x, top: y, width: W, height: H }}
      className={`absolute rounded-lg border bg-card p-2.5 text-left shadow-sm transition-shadow ${selected ? "ring-2 ring-primary" : ""} ${n.status === "running" ? "border-info shadow-info/30 shadow-md" : ""} ${n.status === "failed" ? "border-destructive/60" : ""} ${n.status === "blocked" || n.status === "pending" ? "border-dashed opacity-80" : ""}`}>
      <button onClick={onSelect} aria-pressed={selected} aria-label={`${n.name}: ${n.status}`} className="absolute inset-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-primary" />
      <div className="pointer-events-none relative flex items-center justify-between gap-1">
        <span className="truncate font-mono text-[15px] font-semibold">{n.name}</span>
        <span className={`font-mono text-[11px] font-medium ${STATUS_CLASS[n.status] ?? ""}`}>{n.status === "running" && s?.active ? s.active : n.status.replace("_", " ")}</span>
      </div>
      <div className="pointer-events-none relative mt-0.5 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
        {s?.cacheHit === true && <span className="rounded-full border border-success/50 px-1.5 text-success">cache hit</span>}
        {s?.cacheHit === false && <span className="rounded-full border px-1.5">cache miss</span>}
        {n.attempts != null && n.attempts > 1 || (s && s.attempts > 1) ? <span className="rounded-full border border-warning/60 px-1.5">attempt {Math.max(s?.attempts ?? 1, n.attempts ?? 1)}</span> : null}
        {dur ? <span className="font-mono">{fmtDuration(dur / 1000)}</span> : null}
      </div>
      <div className="pointer-events-none relative"><PhaseStrip state={s} detailed={detailed} /></div>
      <div className="pointer-events-none relative mt-1.5 flex flex-wrap gap-1" aria-label="Scripted checks">
        {(s?.checks ?? []).slice(0, detailed ? 8 : 5).map((c) => (
          <span key={c.check} title={`${c.check}: ${c.result}`} className={`inline-flex h-4 w-4 items-center justify-center rounded text-[10px] font-bold ${c.result === "pass" ? "bg-success/15 text-success" : "bg-destructive/15 text-destructive"}`}>{c.result === "pass" ? "✓" : "✗"}</span>
        ))}
        {detailed && (s?.checks ?? []).slice(0, 8).map((c) => <span key={`l-${c.check}`} className="font-mono text-[8px] text-muted-foreground">{c.check}</span>)}
      </div>
    </div>
  );
}

export interface ArcSpec { id: string; loop: LoopId; from: string; to: string; label: string; lit: boolean; pulse: boolean; tone: "ok" | "bad" | "warn" | "info"; count: number; row: number }

const TONE_STROKE = { ok: "stroke-success", bad: "stroke-destructive", warn: "stroke-warning", info: "stroke-info" } as const;
const TONE_FILL = { ok: "fill-success", bad: "fill-destructive", warn: "fill-warning", info: "fill-info" } as const;

export function Graph({ nodes, mission, arcs, selected, onSelect, fill }: {
  nodes: NodeView[]; mission: Mission; arcs: ArcSpec[]; selected: string | null; onSelect: (n: string | null) => void; fill: boolean;
}) {
  const place = useMemo(() => {
    const rows = new Map<number, number>();
    const pos = new Map<string, { x: number; y: number }>();
    const depth = new Map(nodes.map((n) => [n.name, n.name in COLUMN ? COLUMN[n.name] : 1 + (n.needs.length ? 1 : 0)]));
    nodes.forEach((n) => {
      const col = depth.get(n.name)!;
      const row = rows.get(col) ?? 0;
      rows.set(col, row + 1);
      pos.set(n.name, { x: PAD + col * (W + GX), y: BAND_TOP + PAD + row * (H + GY) });
    });
    const cols = Math.max(...depth.values(), 0) + 1;
    const maxRows = Math.max(...rows.values(), 1);
    const width = PAD * 2 + cols * W + (cols - 1) * GX;
    const nodesHeight = BAND_TOP + PAD * 2 + maxRows * H + (maxRows - 1) * GY;
    return { pos, width, nodesHeight, height: nodesHeight + ARC_ROOM, cols, rows };
  }, [nodes]);

  const viewport = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [touched, setTouched] = useState(false);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  const fit = useCallback(() => {
    const el = viewport.current;
    if (!el) return;
    const k = Math.min(el.clientWidth / place.width, el.clientHeight / place.height, 1.4) || 1;
    setView({ k, x: (el.clientWidth - place.width * k) / 2, y: Math.max(0, (el.clientHeight - place.height * k) / 2) });
    setTouched(false);
  }, [place.width, place.height]);
  useLayoutEffect(() => { fit(); }, [fit, fill]);
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const ro = new ResizeObserver(() => { if (!touched) fit(); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fit, touched]);

  const zoomAt = useCallback((factor: number, cx?: number, cy?: number) => {
    const el = viewport.current;
    setTouched(true);
    setView((v) => {
      const k = Math.min(3, Math.max(0.25, v.k * factor));
      const px = cx ?? (el ? el.clientWidth / 2 : 0), py = cy ?? (el ? el.clientHeight / 2 : 0);
      return { k, x: px - ((px - v.x) / v.k) * k, y: py - ((py - v.y) / v.k) * k };
    });
  }, []);
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [zoomAt]);

  const byName = useMemo(() => new Map(nodes.map((n) => [n.name, n])), [nodes]);
  const anchor = (name: string, side: "bottom" | "top" | "right" | "left") => {
    const p = place.pos.get(name);
    if (!p) return null;
    return side === "bottom" ? { x: p.x + W / 2, y: p.y + H } : side === "top" ? { x: p.x + W / 2, y: p.y }
      : side === "right" ? { x: p.x + W, y: p.y + H / 2 } : { x: p.x, y: p.y + H / 2 };
  };

  const bandBox = (cols: readonly [number, number]) => ({ left: PAD + cols[0] * (W + GX) - 14, width: (cols[1] - cols[0] + 1) * W + (cols[1] - cols[0]) * GX + 28 });
  const lanes = nodes.filter((n) => COLUMN[n.name] === 2);
  const activeLanes = lanes.filter((n) => n.status === "running").length;

  return (
    <div className="relative">
      <div ref={viewport} role="region" aria-label="Pipeline graph. Scroll to zoom, drag to pan."
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === "+" || e.key === "=") zoomAt(1.2); else if (e.key === "-") zoomAt(1 / 1.2); else if (e.key === "0") fit(); else if (e.key.startsWith("Arrow")) { setTouched(true); setView((v) => ({ ...v, x: v.x + (e.key === "ArrowLeft" ? 40 : e.key === "ArrowRight" ? -40 : 0), y: v.y + (e.key === "ArrowUp" ? 40 : e.key === "ArrowDown" ? -40 : 0) })); } }}
        onPointerDown={(e) => { if ((e.target as HTMLElement).closest("button")) return; drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); }}
        onPointerMove={(e) => { const d = drag.current; if (d) { setTouched(true); setView((v) => ({ ...v, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y })); } }}
        onPointerUp={() => { drag.current = null; }}
        className={`engineering-grid relative overflow-hidden rounded-lg border bg-surface outline-none focus-visible:ring-2 focus-visible:ring-primary ${fill ? "h-[calc(100vh-24rem)] min-h-96" : "h-[max(34rem,min(56rem,calc(100vh-13rem)))]"} cursor-grab active:cursor-grabbing`}>
        <div style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, transformOrigin: "0 0", width: place.width, height: place.height }} className="absolute left-0 top-0">
          {BANDS.map((b) => { const box = bandBox(b.cols); return (
            <div key={b.id} data-band={b.id} style={{ left: box.left, width: box.width, top: 6, height: place.nodesHeight - 12 }} className={`absolute rounded-xl border border-dashed ${BAND_TINT[b.id]}`}>
              <div className="absolute left-3 top-1.5 font-mono text-[11px] font-semibold tracking-[0.14em] text-muted-foreground">{b.title} <span className="font-normal normal-case tracking-normal">· {b.sub}</span></div>
            </div>); })}
          {lanes.length > 1 && (() => { const p = place.pos.get(lanes[0].name)!; return (
            <div style={{ left: p.x - 8, top: p.y - 12, width: 4, height: lanes.length * (H + GY) - GY + 24 }} className="absolute rounded bg-primary/30" aria-hidden="true" />); })()}
          {lanes.length > 1 && (() => { const p = place.pos.get(lanes[0].name)!; return (
            <div style={{ left: p.x, top: p.y - 24 }} className="absolute font-mono text-[10px] text-primary" data-testid="lanes">{lanes.length} parallel lanes · {activeLanes} active</div>); })()}
          <svg width={place.width} height={place.height} className="pointer-events-none absolute left-0 top-0" aria-hidden="true">
            <defs>{(["ok", "bad", "warn", "info"] as const).map((t) => (
              <marker key={t} id={`arrow-${t}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" className={TONE_FILL[t]} /></marker>))}
              <marker id="arrow-idle" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" className="fill-border" /></marker></defs>
            {nodes.flatMap((n) => n.needs.filter((d) => byName.has(d)).map((d) => {
              const a = anchor(d, "right"), b = anchor(n.name, "left");
              if (!a || !b) return null;
              const dx = Math.max(16, (b.x - a.x) / 2);
              const started = !!byName.get(d)?.state && !!n.state;
              return <path key={`${d}>${n.name}`} d={`M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x - 1},${b.y}`} fill="none" strokeWidth={started ? 1.6 : 1} className={started ? "stroke-primary/60" : "stroke-border"} markerEnd="url(#arrow-idle)" />;
            }))}
            {nodes.map((n) => {
              const a = anchor(n.name, "top")!;
              const r = n.state?.repairs ?? 0;
              const x1 = a.x + 26, x2 = a.x - 26;
              return <g key={`rep-${n.name}`} data-arc="repair" data-lit={r > 0}>
                <path d={`M${x1},${a.y} C${x1 + 10},${a.y - 16} ${x2 - 10},${a.y - 16} ${x2},${a.y}`} fill="none" strokeWidth={r > 0 ? 2 : 1} strokeDasharray={r > 0 ? undefined : "3 3"} className={r > 0 ? `${TONE_STROKE.warn} ${mission.firing.repair && n.state?.repairs ? "" : ""}` : "stroke-border"} markerEnd={r > 0 ? "url(#arrow-warn)" : undefined} />
                {r > 0 && <text x={a.x} y={a.y - 15} textAnchor="middle" className="fill-warning font-mono text-[10px] font-bold">↻ {r}</text>}
              </g>;
            })}
            {arcs.map((arc) => {
              const from = anchor(arc.from, "bottom"), to = anchor(arc.to, "bottom");
              if (!from || !to) return null;
              const y = place.nodesHeight + 18 + arc.row * 26;
              const mid = (from.x + to.x) / 2;
              const cls = arc.lit ? TONE_STROKE[arc.tone] : "stroke-border";
              return <g key={arc.id} data-arc={arc.loop} data-lit={arc.lit} data-pulse={arc.pulse}>
                <path d={`M${from.x},${from.y} C${from.x},${y + 20} ${to.x},${y + 20} ${to.x},${to.y + 2}`} fill="none" strokeWidth={arc.lit ? 2.4 : 1.2} strokeDasharray={arc.lit ? undefined : "4 4"} className={`${cls} ${arc.pulse ? "animate-pulse" : ""}`} markerEnd={arc.lit ? `url(#arrow-${arc.tone})` : "url(#arrow-idle)"} />
                <text x={mid} y={y + 15} textAnchor="middle" className={`font-mono text-[11px] ${arc.lit ? "fill-foreground font-semibold" : "fill-muted-foreground"}`}>{arc.label}{arc.count ? ` · ${arc.count}` : ""}</text>
              </g>;
            })}
          </svg>
          {nodes.map((n) => { const p = place.pos.get(n.name)!; return <Node key={n.name} n={n} x={p.x} y={p.y} k={view.k} selected={selected === n.name} onSelect={() => onSelect(selected === n.name ? null : n.name)} />; })}
        </div>
      </div>
      <div className="absolute bottom-2 right-2 flex flex-col gap-1">
        <Tip text="Scroll or pinch to zoom; drag to pan. Zoom in to read each stage's phase timings." align="right">
          <button onClick={() => zoomAt(1.25)} aria-label="Zoom in" className="rounded-md border bg-card p-1.5 hover:bg-muted"><Plus className="h-4 w-4" /></button></Tip>
        <button onClick={() => zoomAt(1 / 1.25)} aria-label="Zoom out" className="rounded-md border bg-card p-1.5 hover:bg-muted"><Minus className="h-4 w-4" /></button>
        <button onClick={fit} aria-label="Fit to view" className="rounded-md border bg-card p-1.5 hover:bg-muted"><Maximize2 className="h-4 w-4" /></button>
      </div>
      <p className="pointer-events-none absolute bottom-2 left-3 text-[11px] text-muted-foreground">{Math.round(view.k * 100)}%</p>
    </div>
  );
}
