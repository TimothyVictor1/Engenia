import { useEffect, useMemo, useState } from "react";
import { useDash, useResource } from "@/lib/store";
import { go } from "@/lib/route";
import type { PipelineStage } from "@/lib/types";
import { fmtDuration, fmtLimit, fmtNumber } from "@/lib/format";
import { Card, KV } from "@/components/Bits";
import { Empty, Gate, Loading, ErrorState, PageHeader } from "@/components/States";
import { StatusBadge, tone } from "@/components/StatusBadge";

const W = 120, H = 52, GAP_X = 36, GAP_Y = 22, PAD = 10;

/** Column = dependency depth; rows keep API order and are pulled toward their neighbours. */
export function layout(stages: PipelineStage[]) {
  const byName = new Map(stages.map((s) => [s.name, s]));
  const needs = (s: PipelineStage) => s.needs.filter((n) => byName.has(n));
  const depth = new Map<string, number>();
  const visit = (name: string, trail: Set<string>): number => {
    if (depth.has(name)) return depth.get(name)!;
    if (trail.has(name)) return 0; // cycle guard: the API never sends one
    trail.add(name);
    const parents = needs(byName.get(name)!);
    const d = parents.length ? 1 + Math.max(...parents.map((n) => visit(n, trail))) : 0;
    trail.delete(name);
    depth.set(name, d);
    return d;
  };
  stages.forEach((s) => visit(s.name, new Set()));
  const columns: string[][] = [];
  stages.forEach((s) => (columns[depth.get(s.name)!] ??= []).push(s.name));
  const nodes = new Map<string, { x: number; y: number }>();
  columns.forEach((names, c) => names.forEach((n, r) => nodes.set(n, { x: PAD + c * (W + GAP_X), y: PAD + r * (H + GAP_Y) })));
  const width = PAD * 2 + columns.length * W + (columns.length - 1) * GAP_X;
  const height = PAD * 2 + Math.max(...columns.map((c) => c.length)) * (H + GAP_Y) - GAP_Y;
  return { nodes, width, height, edges: stages.flatMap((s) => needs(s).map((n) => [n, s.name] as const)) };
}

function Graph({ stages, selected, onSelect }: { stages: PipelineStage[]; selected: string | null; onSelect: (n: string | null) => void }) {
  const { nodes, width, height, edges } = useMemo(() => layout(stages), [stages]);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="min-w-[720px] w-full" role="group" aria-label="Pipeline stages and their dependencies">
      {edges.map(([from, to]) => {
        const a = nodes.get(from)!, b = nodes.get(to)!;
        const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, dx = Math.max(12, (x2 - x1) / 2);
        const active = selected === from || selected === to;
        return <path key={`${from}>${to}`} d={`M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`}
          fill="none" strokeWidth={active ? 2 : 1} className={active ? "stroke-primary" : "stroke-border"} />;
      })}
      {stages.map((s) => {
        const p = nodes.get(s.name)!;
        const on = s.name === selected;
        return (
          <g key={s.name} transform={`translate(${p.x},${p.y})`} tabIndex={0} role="button" aria-pressed={on}
            aria-label={`${s.name}, status ${s.status.replace("_", " ")}`} className="cursor-pointer outline-none"
            onClick={() => onSelect(on ? null : s.name)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(on ? null : s.name); } }}>
            <rect width={W} height={H} rx={6} className={`fill-card ${on ? "stroke-primary" : "stroke-border"}`} strokeWidth={on ? 2 : 1}
              strokeDasharray={s.status === "blocked" ? "4 3" : s.status === "pending" ? "1 3" : undefined} />
            <rect x={1} y={1} width={4} height={H - 2} rx={2} className={tone(s.status).bar.split(" ")[0].replace("bg-", "fill-")} />
            <text x={14} y={22} className="fill-foreground font-mono text-[11px] font-medium">{s.name}</text>
            <text x={14} y={39} className="fill-muted-foreground text-[10px]">{s.status.replace("_", " ")}</text>
          </g>
        );
      })}
    </svg>
  );
}

function StagePanel({ stage, onClose }: { stage: PipelineStage; onClose: () => void }) {
  const detail = useResource(null, (api) => api.stage(stage.name), stage.name + stage.status);
  return (
    <aside aria-label={`Stage ${stage.name}`} className="rounded-lg border bg-card p-4 lg:w-96 lg:shrink-0">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="font-mono text-sm font-semibold">{stage.name}</h2>
          <div className="mt-1 flex items-center gap-2"><StatusBadge status={stage.status} />
            {stage.key && <span className="font-mono text-[10px] text-muted-foreground" title={stage.key}>{stage.key.slice(0, 19)}</span>}</div>
        </div>
        <button onClick={onClose} className="rounded-md border px-2 py-1 text-xs hover:bg-muted" aria-label="Close stage details">Close</button>
      </div>
      {stage.needs.length > 0 && <p className="mb-3 text-xs text-muted-foreground">needs {stage.needs.join(", ")}</p>}
      {stage.message && <p className="mb-3 text-xs">{stage.message}</p>}
      {detail.status === "loading" ? <Loading /> : detail.status === "error" || !detail.data ? <ErrorState error={detail.error} onRetry={detail.reload} /> : (
        <>
          <h3 className="mb-1 text-xs font-semibold">Facts</h3>
          <KV data={detail.data.facts} empty="No facts reported." />
          <h3 className="mb-1 mt-4 text-xs font-semibold">Verdicts</h3>
          {detail.data.verdicts.length === 0 ? <p className="text-sm text-muted-foreground">No verdicts for this stage.</p> : (
            <ul className="space-y-2">
              {detail.data.verdicts.map((v, i) => (
                <li key={`${v.check}-${i}`} className="rounded-md border p-2 text-xs">
                  <div className="flex items-center justify-between"><span className="font-mono">{v.check}</span><StatusBadge status={v.result} /></div>
                  <div className="mt-1 text-muted-foreground">{v.requirement ?? "no requirement"} · {v.evidence_level}</div>
                  <div className="font-mono">measured {v.measured == null ? "—" : fmtNumber(v.measured)} · limit {fmtLimit(v.limit)}</div>
                  {v.message && <div className="mt-1">{v.message}</div>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </aside>
  );
}

function RunTable({ stages }: { stages: PipelineStage[] }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  return (
    <div className="mt-6 overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr>
          {["Stage", "Status", "Attempts", "Time", "Note"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
        <tbody>{stages.map((s) => (
          <tr key={s.name} className="border-t">
            <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-normal">{s.name}</th>
            <td className="px-3 py-1.5"><StatusBadge status={s.status} /></td>
            <td className="px-3 py-1.5 font-mono text-xs">{s.attempts ?? "—"}</td>
            <td className="px-3 py-1.5 font-mono text-xs">{fmtDuration(s.duration_s)}</td>
            <td className="px-3 py-1.5 text-xs text-muted-foreground">{s.message ?? ""}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

export function Pipeline({ param }: { param: string }) {
  const res = useResource("pipeline", (api) => api.pipeline());
  const { sample } = useDash();
  const selected = param || null;
  return (
    <>
      <PageHeader title="Pipeline" subtitle="The stages, left to right by dependency. Select one for its facts and verdicts." />
      <Gate res={res}>{(data) => {
        const stages = data.stages ?? [];
        if (!stages.length) return <Empty title="No stages">The pipeline has not been planned yet.</Empty>;
        const counts = new Map<string, number>();
        stages.forEach((s) => counts.set(s.status, (counts.get(s.status) ?? 0) + 1));
        const stage = stages.find((s) => s.name === selected) ?? null;
        const idle = !sample && stages.every((s) => s.status === "not_run");
        return (
          <>
            <p className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
              <span>{stages.length} stages</span>
              {[...counts].map(([k, n]) => <span key={k} className="flex items-center gap-1.5"><StatusBadge status={k} count={n} /></span>)}
              {data.run && <span className="font-mono text-xs">run {data.run.id} · {data.run.status}</span>}
            </p>
            {idle && <Empty title="No run yet">Nothing has been built for this project. Run <code className="font-mono">engenia run</code>, or switch on Sample data to look around.</Empty>}
            <div className="mt-4 flex flex-col gap-4 lg:flex-row">
              <Card className="min-w-0 flex-1 overflow-x-auto"><Graph stages={stages} selected={stage?.name ?? null} onSelect={(n) => go("pipeline", n ?? "")} /></Card>
              {stage ? <StagePanel stage={stage} onClose={() => go("pipeline")} />
                : selected && <p className="text-sm text-muted-foreground">There is no stage named “{selected}”.</p>}
            </div>
            {stages.some((s) => s.attempts != null) && <RunTable stages={stages} />}
          </>
        );
      }}</Gate>
    </>
  );
}
