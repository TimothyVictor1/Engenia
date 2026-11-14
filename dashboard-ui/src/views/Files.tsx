// Files: every file the stages built, with download and an inline preview. Text, JSON and CSV are
// shown as text; STL is shown in 3D; a STEP file shows the STL the build wrote next to it.

import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Download, FileText, Package } from "lucide-react";
import { useDash, useResource } from "@/lib/store";
import { fetchFile } from "@/lib/api";
import type { FileEntry, FilesList } from "@/lib/types";
import { Empty, ErrorState, Gate, Loading, PageHeader } from "@/components/States";
import { go } from "@/lib/route";

const ModelViewer = lazy(() => import("@/components/ModelViewer").then((m) => ({ default: m.ModelViewer })));
const TEXT_LIMIT = 1024 * 1024;
const CSV_ROWS = 200;

export const fmtSize = (n: number): string =>
  n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} kB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

/** Split one CSV line, honouring quotes. Preview only: not a general parser. */
export function parseCsv(text: string, maxRows = CSV_ROWS): string[][] {
  const rows: string[][] = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line) continue;
    const cells: string[] = [];
    let cur = "", quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const c = line[i];
      if (quoted) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i += 1; } else if (c === '"') quoted = false; else cur += c;
      } else if (c === '"') quoted = true;
      else if (c === ",") { cells.push(cur); cur = ""; } else cur += c;
    }
    cells.push(cur);
    rows.push(cells);
    if (rows.length >= maxRows + 1) break;
  }
  return rows;
}

function Preview({ stage, file, siblings }: { stage: string; file: FileEntry; siblings: FileEntry[] }) {
  const { api, versions } = useDash();
  const url = api.fileUrl(stage, file.path, versions.design);
  const stem = file.path.replace(/\.[^.]+$/, "");
  const mesh = file.type === "step" ? siblings.find((f) => f.type === "stl" && f.path === `${stem}.stl`) : file.type === "stl" ? file : undefined;
  const textual = file.type === "text" || file.type === "json" || file.type === "csv";
  const [state, setState] = useState<{ kind: "loading" } | { kind: "text"; text: string; cut: boolean } | { kind: "error"; message: string } | { kind: "missing" }>({ kind: "loading" });
  useEffect(() => {
    if (!textual) return;
    if (file.size > TEXT_LIMIT * 4) { setState({ kind: "error", message: `This file is ${fmtSize(file.size)}; download it to read it.` }); return; }
    let dead = false;
    setState({ kind: "loading" });
    fetchFile(url).then(
      (buffer) => {
        if (dead) return;
        if (buffer === "missing") { setState({ kind: "missing" }); return; }
        const text = new TextDecoder().decode(buffer.slice(0, TEXT_LIMIT));
        setState({ kind: "text", text, cut: buffer.byteLength > TEXT_LIMIT });
      },
      (e: Error) => { if (!dead) setState({ kind: "error", message: e.message }); },
    );
    return () => { dead = true; };
  }, [url, textual, file.size]);

  if (mesh) {
    return (
      <div>
        {file.type === "step" && <p className="mb-2 text-xs text-muted-foreground">STEP files are shown as the mesh the build exported beside them: <span className="font-mono">{mesh.path}</span>.</p>}
        <Suspense fallback={<Loading text="Loading 3D viewer…" />}><ModelViewer files={[{ name: mesh.name, url: api.fileUrl(stage, mesh.path, versions.design) }]} height={380} /></Suspense>
      </div>
    );
  }
  if (file.type === "step") return <Empty title="No 3D preview for this STEP file">The build did not write a matching .stl next to it. Download the file to open it in CAD software.</Empty>;
  if (file.type === "image") return <img src={url} alt={file.name} className="max-h-96 rounded border" />;
  if (!textual) return <Empty title="No preview for this file type">Download it to open it.</Empty>;
  if (state.kind === "loading") return <Loading text="Loading preview…" />;
  if (state.kind === "missing") return <Empty title="This file is no longer there">It was removed since the list was loaded. Refresh the list.</Empty>;
  if (state.kind === "error") return <ErrorState error={new Error(state.message)} />;
  const cutNote = state.cut ? <p className="mt-2 text-xs text-muted-foreground">Showing the first {fmtSize(TEXT_LIMIT)}. Download the file for the rest.</p> : null;
  if (file.type === "csv") {
    const rows = parseCsv(state.text);
    return (
      <div>
        <div className="max-h-96 overflow-auto rounded border"><table className="w-full text-xs">
          <thead className="sticky top-0 bg-muted text-left"><tr>{(rows[0] ?? []).map((c, i) => <th key={i} className="px-2 py-1 font-medium">{c}</th>)}</tr></thead>
          <tbody>{rows.slice(1).map((r, i) => <tr key={i} className="border-t">{r.map((c, j) => <td key={j} className="px-2 py-1 font-mono">{c}</td>)}</tr>)}</tbody></table></div>
        {rows.length > CSV_ROWS && <p className="mt-2 text-xs text-muted-foreground">Showing the first {CSV_ROWS} rows.</p>}{cutNote}
      </div>
    );
  }
  let shown = state.text;
  if (file.type === "json" && !state.cut) { try { shown = JSON.stringify(JSON.parse(state.text), null, 2); } catch { /* show it as written */ } }
  return <div><pre aria-label={`Contents of ${file.name}`} className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border bg-surface p-3 font-mono text-xs">{shown || "(empty file)"}</pre>{cutNote}</div>;
}

function useAllLists(stages: string[]) {
  const { api, versions, sample } = useDash();
  const [lists, setLists] = useState<Record<string, { files: FileEntry[] } | { error: Error }>>({});
  const [loading, setLoading] = useState(true);
  const key = stages.join(",");
  const version = versions.pipeline;
  useEffect(() => {
    let dead = false;
    setLoading(true);
    Promise.all(stages.map((s) => api.files(s).then((l: FilesList) => [s, { files: l.files }] as const, (error: Error) => [s, { error }] as const)))
      .then((pairs) => { if (!dead) { setLists(Object.fromEntries(pairs)); setLoading(false); } });
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, api, version, sample]);
  return { lists, loading };
}

const TYPE_TONE: Record<string, string> = { stl: "bg-primary/10 text-primary", step: "bg-primary/10 text-primary", json: "bg-info/10 text-info", csv: "bg-success/10 text-success" };

export function Files({ param }: { param?: string }) {
  const { api } = useDash();
  const pipeline = useResource("pipeline", (a) => a.pipeline());
  const release = useResource(null, (a) => a.release());
  const stages = useMemo(() => pipeline.data?.stages.map((s) => s.name) ?? [], [pipeline.data]);
  const { lists, loading } = useAllLists(stages);
  const stage = param && stages.includes(param) ? param : stages.find((s) => "files" in (lists[s] ?? {}) && (lists[s] as { files: FileEntry[] }).files.length) ?? stages[0] ?? null;
  const entry = stage ? lists[stage] : undefined;
  const files = entry && "files" in entry ? entry.files : [];
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { setOpen(null); }, [stage]);
  const current = files.find((f) => f.path === open) ?? null;
  const allZip = api.zipUrl("all");
  const stageZip = stage ? api.zipUrl(stage) : null;
  const btn = "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted";
  const off = "pointer-events-none opacity-50";

  return (
    <>
      <PageHeader title="Files" subtitle="Everything each stage built: preview it here or download it."
        right={<div className="flex flex-wrap gap-2">
          <a href={allZip ?? undefined} aria-disabled={!allZip} title={allZip ? "The whole build and src as one zip" : "Downloads need a running server"} className={`${btn} ${allZip ? "" : off}`}><Package className="h-3.5 w-3.5" /> Download all (zip)</a>
          {release.data?.available && api.releaseUrl() && <a href={api.releaseUrl() ?? undefined} className={`${btn} border-success/50 text-success`}><Download className="h-3.5 w-3.5" /> Download release{release.data.name ? ` (${fmtSize(release.data.size ?? 0)})` : ""}</a>}
        </div>} />
      <Gate res={pipeline}>{() => stages.length === 0 ? <Empty title="No stages">The pipeline has not been planned yet.</Empty> : (
        <div className="grid gap-4 lg:grid-cols-[14rem_1fr]">
          <nav aria-label="Stages" className="flex gap-1 overflow-x-auto lg:flex-col">
            {stages.map((s) => {
              const l = lists[s];
              const n = l && "files" in l ? l.files.length : null;
              return <a key={s} href={`#/files/${s}`} aria-current={s === stage ? "page" : undefined}
                className="flex items-center justify-between gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted aria-[current=page]:bg-accent aria-[current=page]:font-medium aria-[current=page]:text-foreground">
                <span className="font-mono text-xs">{s}</span><span className="font-mono text-[11px]">{loading && n === null ? "…" : l && "error" in l ? "!" : n}</span></a>;
            })}
          </nav>
          <div className="min-w-0">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="font-mono text-sm font-semibold">{stage}</h2>
              <a href={stageZip ?? undefined} aria-disabled={!stageZip || files.length === 0} className={`${btn} ${stageZip && files.length ? "" : off}`}><Download className="h-3.5 w-3.5" /> Download {stage}.zip</a>
            </div>
            {loading && !entry ? <Loading /> : entry && "error" in entry ? <ErrorState error={entry.error} onRetry={() => go("files", stage ?? "")} />
              : files.length === 0 ? <Empty title="Nothing built yet">{stage} has not written any files. Run the pipeline to produce them.</Empty> : (
                <div className="overflow-x-auto rounded-lg border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr>{["Name", "Type", "Size", "Modified", ""].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                    <tbody>{files.map((f) => (
                      <tr key={f.path} className={`border-t ${open === f.path ? "bg-accent/50" : ""}`}>
                        <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-normal">{f.path}</th>
                        <td className="px-3 py-1.5"><span className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${TYPE_TONE[f.type] ?? "bg-muted text-muted-foreground"}`}>{f.type}</span></td>
                        <td className="px-3 py-1.5 font-mono text-xs">{fmtSize(f.size)}</td>
                        <td className="px-3 py-1.5 font-mono text-xs" title={f.modified}>{f.modified.replace("T", " ").replace(/\+00:00$/, " UTC")}</td>
                        <td className="whitespace-nowrap px-3 py-1.5 text-right">
                          <button onClick={() => setOpen(open === f.path ? null : f.path)} aria-pressed={open === f.path} aria-label={`Preview ${f.path}`} className={`${btn} mr-1.5 aria-pressed:border-primary`}><FileText className="h-3.5 w-3.5" /> Preview</button>
                          <a href={stage ? api.fileUrl(stage, f.path) : undefined} download={f.name} aria-label={`Download ${f.path}`} className={btn}><Download className="h-3.5 w-3.5" /> Download</a></td>
                      </tr>))}</tbody>
                  </table>
                </div>)}
            {current && stage && <div className="mt-4 rounded-lg border bg-card p-3" aria-label={`Preview of ${current.path}`}>
              <div className="mb-2 flex items-center justify-between"><h3 className="font-mono text-xs font-semibold">{current.path}</h3><button onClick={() => setOpen(null)} className="rounded border px-2 py-0.5 text-xs hover:bg-muted">Close</button></div>
              <Preview stage={stage} file={current} siblings={files} /></div>}
          </div>
        </div>)}</Gate>
    </>
  );
}
