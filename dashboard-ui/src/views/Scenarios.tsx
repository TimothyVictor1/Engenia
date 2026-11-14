import { useResource } from "@/lib/store";
import { fmtNumber } from "@/lib/format";
import { Card, KV } from "@/components/Bits";
import { Empty, Gate, PageHeader } from "@/components/States";

const FIELDS: [keyof import("@/lib/types").Scenario, string, string][] = [
  ["wave_height_m", "Wave height", "m"], ["wave_period_s", "Wave period", "s"], ["depth_m", "Water depth", "m"],
  ["water_density", "Water density", "kg/m³"], ["water_temp_c", "Water temperature", "°C"],
];

export function Scenarios() {
  const res = useResource("scenarios", (api) => api.scenarios());
  return (
    <>
      <PageHeader title="Scenarios" subtitle="Conditions the design is stressed against, and the requirements each one targets." />
      <Gate res={res}>{({ scenarios }) => !scenarios?.length
        ? <Empty title="No scenarios">The red-team stage has not proposed any scenarios yet.</Empty>
        : <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{scenarios.map((s) => (
          <Card key={s.name} className="flex flex-col gap-3">
            <h2 className="font-mono text-sm font-semibold">{s.name}</h2>
            <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
              {FIELDS.filter(([k]) => s[k] != null).map(([k, label, unit]) => (
                <div key={k} className="contents"><dt className="text-muted-foreground">{label}</dt>
                  <dd className="text-right font-mono text-xs">{fmtNumber(s[k])} <span className="text-muted-foreground">{unit}</span></dd></div>))}
            </dl>
            {s.extra && Object.keys(s.extra).length > 0 && <div className="border-t pt-2"><KV data={s.extra} /></div>}
            {s.rationale && <p className="text-sm text-muted-foreground">{s.rationale}</p>}
            {s.targets && s.targets.length > 0 && <div className="mt-auto flex flex-wrap gap-1">{s.targets.map((id) => (
              <a key={id} href={`#/requirements/${encodeURIComponent(id)}`} className="rounded border px-1.5 font-mono text-xs hover:border-primary hover:text-primary">{id}</a>))}</div>}
          </Card>))}</div>}</Gate>
    </>
  );
}
