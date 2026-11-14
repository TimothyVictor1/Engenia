import { useState } from "react";
import { useDash, useResource } from "@/lib/store";
import { Card, KV } from "@/components/Bits";
import { Gate, PageHeader } from "@/components/States";
import { ModelViewer } from "@/components/ModelViewer";

// The buoy builds hull + lid, the deck enclosure body + lid, the sensor pod a tube and two caps,
// the bracket and the thruster duct one part each; a file a project lacks is skipped.
const FILES = ["hull.stl", "body.stl", "lid.stl", "tube.stl", "cap_rear.stl", "cap_front.stl",
  "bracket.stl", "duct.stl"];

function Facts({ stage, title }: { stage: string; title: string }) {
  const res = useResource("design", (api) => api.stage(stage), stage);
  return <Card><h2 className="mb-2 text-sm font-semibold">{title}</h2>
    <Gate res={res}>{(d) => <KV data={d.facts} empty="No facts reported." />}</Gate></Card>;
}

export function Design() {
  const { api, versions } = useDash();
  const [lid, setLid] = useState(true);
  const files = FILES.map((name) => ({ name, url: api.fileUrl("mechanical", name, versions.design) }));
  return (
    <>
      <PageHeader title="Design" subtitle="The printed parts from the mechanical stage (hull, body, tube, caps, bracket or duct), with the facts the stages reported." />
      <div className="grid gap-4 lg:grid-cols-[1fr_26rem]">
        <div>
          <div className="mb-2 flex gap-2">
            <button onClick={() => setLid(!lid)} aria-pressed={lid} className="rounded-md border px-3 py-1 text-xs hover:bg-muted aria-pressed:border-primary aria-pressed:bg-accent">Lid</button>
          </div>
          <ModelViewer files={files} hidden={lid ? [] : ["lid.stl"]} height={480} />
        </div>
        <div className="space-y-4"><Facts stage="mechanical" title="Mechanical" /><Facts stage="electronics" title="Electronics" /></div>
      </div>
    </>
  );
}
