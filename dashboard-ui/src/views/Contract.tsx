import { useResource } from "@/lib/store";
import type { ContractData } from "@/lib/types";
import { flatten, fmtValue } from "@/lib/format";
import { Card, KV, Section } from "@/components/Bits";
import { Empty, Gate, PageHeader } from "@/components/States";
import { StatusBadge } from "@/components/StatusBadge";

const SOURCE_TEXT = {
  project: "The contract defined by this project.",
  default: "The default contract. The project has not defined its own.",
  none: "No contract exists for this project yet.",
};
const UNITS: [string, string][] = [["_mwh_per_day", "mWh/day"], ["_mah", "mAh"], ["_mm", "mm"], ["_kb", "kB"], ["_mw", "mW"], ["_ms", "ms"], ["_g", "g"]];
const NOISE = new Set(["dry", "used", "total", "outer", "budget", "actual", "measured"]);

function splitKey(key: string) {
  const bare = key.replace(/_(max|min)$/, "");
  for (const [suffix, unit] of UNITS) if (bare.endsWith(suffix)) return { base: bare.slice(0, -suffix.length), unit };
  return { base: bare, unit: "" };
}
const tokens = (base: string) => base.split(/[_.]/).filter((t) => t && !NOISE.has(t));

/** The reported fact that matches a budget key: same words, fewest extra words. */
function findFact(facts: Record<string, unknown> | undefined, budgetKey: string) {
  const want = tokens(splitKey(budgetKey).base);
  if (!want.length) return null;
  let best: { key: string; value: unknown; extra: number } | null = null;
  for (const [key, value] of flatten(facts)) {
    const have = tokens(splitKey(key).base);
    if (!want.every((t) => have.includes(t))) continue;
    const extra = have.length - want.length;
    if (!best || extra < best.extra) best = { key, value, extra };
  }
  return best;
}

const Quantity = ({ value, unit }: { value: unknown; unit: string }) => (
  <span className="font-mono text-xs">{Array.isArray(value) && value.every((x) => typeof x === "number") ? value.join(" × ") : fmtValue(value)}
    {unit && <span className="ml-1 text-muted-foreground">{unit}</span>}</span>
);

function Row({ label, budget, facts, budgetKey }: { label: string; budget: unknown; facts: Record<string, unknown> | undefined; budgetKey: string }) {
  const { unit } = splitKey(budgetKey);
  const found = findFact(facts, budgetKey);
  return (
    <tr className="border-t">
      <th scope="row" className="px-3 py-1.5 text-left text-sm font-normal">{label}</th>
      <td className="px-3 py-1.5"><Quantity value={budget} unit={unit} /></td>
      <td className="px-3 py-1.5">{found ? <><Quantity value={found.value} unit={unit} /><span className="ml-2 font-mono text-[10px] text-muted-foreground">{found.key}</span></>
        : <span className="text-xs text-muted-foreground">not reported</span>}</td>
    </tr>
  );
}

function Budgets({ data }: { data: ContractData }) {
  const c = data.contract!;
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr>
          <th className="px-3 py-2 font-medium">Item</th><th className="px-3 py-2 font-medium">Budget (contract)</th><th className="px-3 py-2 font-medium">Reported (stage facts)</th></tr></thead>
        <tbody>
          {Object.entries(c.budgets ?? {}).flatMap(([stage, items]) => [
            <tr key={stage} className="border-t bg-muted/30"><th colSpan={3} scope="colgroup" className="px-3 py-1 text-left font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{stage}</th></tr>,
            ...Object.entries(items ?? {}).map(([k, v]) => <Row key={`${stage}.${k}`} label={splitKey(k).base.replace(/_/g, " ")} budget={v} facts={data.facts?.[stage]} budgetKey={k} />),
          ])}
          {Object.keys(c.totals ?? {}).length > 0 && <tr className="border-t bg-muted/30"><th colSpan={3} scope="colgroup" className="px-3 py-1 text-left font-mono text-[11px] uppercase tracking-wider text-muted-foreground">totals</th></tr>}
          {Object.entries(c.totals ?? {}).map(([k, v]) => <Row key={`t.${k}`} label={`${splitKey(k).base.replace(/_/g, " ")} (${/_min$/.test(k) ? "min" : "max"})`} budget={v} facts={data.facts?.integration} budgetKey={k} />)}
        </tbody>
      </table>
    </div>
  );
}

export function Contract() {
  const res = useResource("contract", (api) => api.contract());
  return (
    <>
      <PageHeader title="Contract" subtitle="Budgets every stage must respect, next to what the stages reported." />
      <Gate res={res}>{(data) => {
        const c = data.contract;
        const version = c?.version;
        const requests = data.change_requests ?? [];
        const decided = new Map((data.decisions ?? []).map((d) => [d.id, d]));
        const count = (s: string) => requests.filter((r) => r.status === s).length;
        return (
          <>
            <p className="mb-4 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>Contract source</span><span className="rounded bg-muted px-1.5 font-mono text-xs text-foreground">{data.source}</span>
              <span>{SOURCE_TEXT[data.source] ?? ""}</span>{version != null && <span>· version {version}</span>}
            </p>
            {!c ? <Empty title="No contract">There are no budgets to compare yet.</Empty> : (
              <>
                <Section title="Budgets and reported facts"><Budgets data={data} /></Section>
                {Object.keys(c.interfaces ?? {}).length > 0 && <Section title="Interfaces"><div className="grid gap-4 sm:grid-cols-2">
                  {Object.entries(c.interfaces ?? {}).map(([name, values]) => <Card key={name}><h3 className="mb-2 font-mono text-xs font-semibold">{name}</h3><KV data={values} /></Card>)}</div></Section>}
              </>
            )}
            <Section title="Change requests">
              {requests.length === 0 ? <Empty title="No change requests">No stage has asked for a budget change.</Empty> : (
                <>
                  <p className="mb-3 flex gap-4 text-sm">{(["open", "accepted", "rejected"] as const).map((s) => <span key={s} className="flex items-center gap-1.5"><StatusBadge status={s} count={count(s)} /></span>)}</p>
                  <ul className="space-y-2">{requests.map((r) => (
                    <li key={r.id} className="rounded-lg border bg-card p-3 text-sm">
                      <div className="flex flex-wrap items-center gap-3"><span className="font-mono text-xs font-medium">{r.id}</span>
                        <span>{r.from_stage} → <span className="font-mono text-xs">{r.path}</span></span>
                        <span className="text-muted-foreground">requested {fmtValue(r.requested)}</span><span className="ml-auto"><StatusBadge status={r.status} /></span></div>
                      {r.reason && <p className="mt-1 text-muted-foreground">Asked because: {r.reason}</p>}
                      {decided.has(r.id) && <p className="mt-1">Decision: {decided.get(r.id)!.reason} (contract version {decided.get(r.id)!.contract_version})</p>}
                    </li>))}</ul>
                </>)}
            </Section>
            <Section title="Mismatches">
              {(data.mismatches ?? []).length === 0 ? <Empty title="No mismatches">The stages agree with each other and with the contract.</Empty> : (
                <ul className="space-y-2">{data.mismatches!.map((m, i) => (
                  <li key={`${m.check}-${i}`} className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3 text-sm">
                    <span className="font-mono text-xs font-medium">{m.check}</span><span>{m.message}</span>
                    <span className="ml-auto text-xs text-muted-foreground">{m.stages.join(", ")} · found by {m.source}</span></li>))}</ul>)}
            </Section>
          </>
        );
      }}</Gate>
    </>
  );
}
