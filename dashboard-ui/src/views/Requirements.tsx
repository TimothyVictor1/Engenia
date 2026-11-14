import { useMemo, useState } from "react";
import { useResource } from "@/lib/store";
import type { Requirement } from "@/lib/types";
import { fmtLimit, fmtNumber } from "@/lib/format";
import { Empty, Gate, PageHeader } from "@/components/States";
import { EvidenceTag, StatusBadge } from "@/components/StatusBadge";

const ORDER = ["fail", "error", "unverified", "pass"] as const;

export function Requirements({ param }: { param: string }) {
  const res = useResource("requirements", (api) => api.requirements());
  const [filter, setFilter] = useState<string>("all");
  return (
    <>
      <PageHeader title="Requirements" subtitle="Each requirement with its limit and the latest verdict from the checks." />
      <Gate res={res}>{({ requirements }) => <List items={requirements ?? []} filter={filter} setFilter={setFilter} open={param} />}</Gate>
    </>
  );
}

function List({ items, filter, setFilter, open }: { items: Requirement[]; filter: string; setFilter: (f: string) => void; open: string }) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    items.forEach((r) => { c[r.status] = (c[r.status] ?? 0) + 1; });
    return c;
  }, [items]);
  if (!items.length) return <Empty title="No requirements">This project has no approved requirement files in spec/.</Empty>;
  const shown = items.filter((r) => filter === "all" || r.status === filter);
  return (
    <>
      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Filter by status">
        {["all", ...ORDER.filter((s) => counts[s])].map((s) => (
          <button key={s} onClick={() => setFilter(s)} aria-pressed={filter === s}
            className="rounded-md border px-2.5 py-1 text-xs hover:bg-muted aria-pressed:border-primary aria-pressed:bg-accent">
            {s === "all" ? `all ${items.length}` : `${s} ${counts[s]}`}
          </button>))}
      </div>
      {shown.length === 0 ? <Empty title="Nothing matches this filter" /> : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr>
              {["ID", "Requirement", "Limit", "Measured", "Status"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody>{shown.map((r) => (
              <tr key={r.id} id={r.id} className={`border-t align-top ${open === r.id ? "bg-accent/50" : ""}`}>
                <th scope="row" className="whitespace-nowrap px-3 py-2 text-left font-mono text-xs font-medium">{r.id}</th>
                <td className="px-3 py-2"><div>{r.statement}</div>
                  <div className="mt-1 font-mono text-[10px] text-muted-foreground">{r.method} · affects {r.affects.join(", ")}</div>
                  {r.verdict?.message && <div className="mt-1 text-xs text-muted-foreground">{r.verdict.message}</div>}</td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs tabular-nums">{fmtLimit(r.limit)}{r.unit && r.unit !== "fits" ? ` ${r.unit}` : ""}</td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs tabular-nums">{r.verdict ? <>{r.verdict.measured == null ? "—" : fmtNumber(r.verdict.measured)}{r.verdict.measured != null && r.unit && r.unit !== "fits" ? <span className="ml-1 text-muted-foreground">{r.unit}</span> : null}<EvidenceTag level={r.verdict.evidence_level} /></> : "—"}</td>
                <td className="px-3 py-2"><StatusBadge status={r.status} /></td>
              </tr>))}</tbody>
          </table>
        </div>)}
    </>
  );
}
