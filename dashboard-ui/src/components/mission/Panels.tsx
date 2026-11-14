import { useEffect, useRef } from "react";
import { Bot, ScrollText, Zap } from "lucide-react";
import { type Agent, type AgentsResponse, type BreakStep, type Mission, type RunSummary, type StageState, type TickerLine } from "@/lib/mission";
import { Empty } from "@/components/States";
import { Tip } from "./Tip";

const NOT_RECORDED = "not recorded yet";

// ---------------------------------------------------------------- verification layers

const WHY = {
  verify: "A different model from the designer checks the requirements and writes the checks, so the design agent never sets its own exam.",
  approval: "A person approves the exact requirements and checks. The hash means nothing can change later without a new approval.",
  scripts: "Scripts, not models, decide pass or fail after every stage, so a stage cannot mark its own homework.",
  integration: "Checks that the board fits the bay, the budgets add up and the pins match before anything is simulated.",
  G1: "Every check in the approved plan is run again by the verifier it names, so nothing is skipped.",
  G2: "Each verdict is recomputed from its measured value and limit, so an edited result cannot pass.",
  G3: "Every stage is rebuilt from source in a scratch copy and must match byte for byte, so results are reproducible.",
  G4: "Every approved requirement must have a pass or fail verdict, so none is silently left unchecked.",
  G5: "Each verifier is shown a known-bad input and must fail it, so a checker that cannot fail is caught.",
  audit: "The independent verifier confirms every requirement has a result before a report is produced.",
} as const;

type Mark = "pass" | "fail" | "running" | "none";
const MARK_CLASS: Record<Mark, string> = {
  pass: "border-success/50 bg-success/10 text-success", fail: "border-destructive/50 bg-destructive/10 text-destructive",
  running: "border-info/50 bg-info/10 text-info animate-pulse", none: "border-dashed text-muted-foreground",
};
const MARK_GLYPH: Record<Mark, string> = { pass: "✓", fail: "✗", running: "…", none: "–" };

function Step({ label, why, mark, detail }: { label: string; why: string; mark: Mark; detail?: string }) {
  return (
    <Tip text={`${why}${detail ? ` — ${detail}` : mark === "none" ? ` (${NOT_RECORDED})` : ""}`}>
      <span data-mark={mark} data-step={label} className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${MARK_CLASS[mark]}`}>
        <b>{MARK_GLYPH[mark]}</b>{label}
      </span>
    </Tip>
  );
}

const markOf = (result: string | undefined | null): Mark => (result === "pass" ? "pass" : result === "fail" || result === "error" ? "fail" : result === "running" ? "running" : "none");

export function Layers({ mission, summary }: { mission: Mission; summary: RunSummary | null }) {
  const vp = mission.stages.verify_plan as StageState | undefined;
  const vpSummary = summary?.before?.verify_plan ?? null;
  const verifyMark: Mark = vpSummary?.result ? markOf(vpSummary.result) : vp?.phases.some((p) => p.phase === "check" && p.result === "ok") ? "pass" : vp?.active ? "running" : "none";
  const approval = mission.approvals.spec ?? mission.approvals.checks;
  const approvedByApi = summary?.before?.approval ?? null;
  const approvalMark: Mark = approval ? (approval.approved ? "pass" : "fail") : approvedByApi ? (approvedByApi.approved ? "pass" : "fail") : "none";
  const hash = (approval?.hash ?? approvedByApi?.hash ?? "").replace("sha256:", "").slice(0, 8);
  const integ = mission.stages.integration?.checks ?? [];
  const checks = Object.values(mission.stages).flatMap((s) => s.checks);
  const passed = checks.filter((c) => c.result === "pass").length;
  const failed = checks.length - passed;
  const scriptMark: Mark = checks.length === 0 ? "none" : failed ? "fail" : "pass";
  const gateSteps = ["G1", "G2", "G3", "G4", "G5"] as const;
  const gateFromSummary = (k: string) => summary?.after?.gate?.find((g) => g.step === k);
  const audit = mission.gate.audit ?? summary?.after?.audit;
  const band = "flex flex-wrap items-center gap-2 rounded-lg border p-2.5";
  return (
    <div className="grid gap-2 lg:grid-cols-[auto_1fr_auto]" aria-label="Verification layers">
      <div className={`${band} bg-info/5`} data-layer="before">
        <span className="font-mono text-[11px] font-semibold tracking-[0.14em] text-muted-foreground">BEFORE</span>
        <Step label="Independent verify-plan" why={WHY.verify} mark={verifyMark} detail={vpSummary?.model ? `${vpSummary.model} (${vpSummary.family ?? "family unknown"})` : undefined} />
        <Step label={`Human approval${hash ? ` ${hash}` : ""}`} why={WHY.approval} mark={approvalMark} detail={hash ? `hash ${hash}` : undefined} />
      </div>
      <div className={`${band} bg-muted/40`} data-layer="during">
        <span className="font-mono text-[11px] font-semibold tracking-[0.14em] text-muted-foreground">DURING</span>
        <Step label={checks.length ? `Scripted checks ${passed}✓${failed ? ` ${failed}✗` : ""}` : "Scripted checks"} why={WHY.scripts} mark={scriptMark} />
        <Step label="Integration check" why={WHY.integration} mark={integ.length ? (integ.every((c) => c.result === "pass") ? "pass" : "fail") : "none"} />
      </div>
      <div className={`${band} bg-success/5`} data-layer="after">
        <span className="font-mono text-[11px] font-semibold tracking-[0.14em] text-muted-foreground">AFTER</span>
        {gateSteps.map((k) => <Step key={k} label={k} why={WHY[k]} mark={markOf(mission.gate[k]?.result ?? gateFromSummary(k)?.result)} detail={mission.gate[k]?.detail || gateFromSummary(k)?.detail || undefined} />)}
        <Step label="Final audit" why={WHY.audit} mark={markOf(audit?.result)} detail={audit?.detail} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- loops

export function Loops({ mission, summary, lit }: { mission: Mission; summary: RunSummary | null; lit: Record<string, boolean> }) {
  const c = mission.counters;
  const cal = summary?.loops?.calibration ?? null;
  const items = [
    { id: "repair", title: "Repair loop", value: `${c.repairs} repair${c.repairs === 1 ? "" : "s"}`, sub: `attempts up to ${Math.max(1, ...Object.values(mission.stages).map((s) => s.attempts))}`, why: "A failed check sends the stage back to its agent with the failure, a limited number of times." },
    { id: "change_request", title: "Contract changes", value: `${c.cr.accepted} accepted · ${c.cr.rejected} rejected`, sub: `${c.cr.open} open`, why: "A stage cannot edit the contract; it asks, and the engine accepts or rejects with a recorded reason." },
    { id: "proposal", title: "Requirement proposals", value: `${c.proposals} draft`, sub: "a person approves", why: "When a limit cannot be met, a new limit is proposed from the measurements; only a person can approve it." },
    { id: "calibration", title: "Measure → calibrate", value: cal ? `${cal.measurements} measured` : NOT_RECORDED, sub: cal ? `${cal.applied} applied` : "", why: "Real measurements from a printed unit adjust the simulation parameters, so the model learns from the world." },
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label="Feedback loops">
      {items.map((i) => (
        <Tip key={i.id} text={i.why} align="left">
          <div data-loop={i.id} data-lit={!!lit[i.id]} className={`w-full rounded-lg border bg-card p-3 text-left ${lit[i.id] ? "border-warning/60" : ""}`}>
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{i.title}</p>
            <p className="mt-1 text-sm font-semibold">{i.value}</p>
            {i.sub && <p className="text-xs text-muted-foreground">{i.sub}</p>}
          </div>
        </Tip>))}
    </div>
  );
}

// ---------------------------------------------------------------- agents

const KIND_BADGE = { llm: "LLM", deterministic: "Deterministic", script: "Script" } as const;
const KIND_CLASS = { llm: "bg-primary/10 text-primary", deterministic: "bg-info/10 text-info", script: "bg-muted text-muted-foreground" } as const;
const STATUS_DOT: Record<string, string> = { active: "bg-info animate-pulse", done: "bg-success", failed: "bg-destructive", idle: "bg-muted-foreground/40" };

function AgentCard({ a }: { a: Agent }) {
  return (
    <li data-agent={a.id} data-status={a.status} className="rounded-lg border bg-card p-3 text-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><p className="truncate font-semibold">{a.name}</p><p className="text-xs text-muted-foreground">{a.role}</p></div>
        <span className="flex shrink-0 items-center gap-1.5 text-xs"><span className={`h-2 w-2 rounded-full ${STATUS_DOT[a.status] ?? STATUS_DOT.idle}`} />{a.status}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className={`rounded px-1.5 py-0.5 font-mono font-medium ${KIND_CLASS[a.kind]}`}>{KIND_BADGE[a.kind]}</span>
        {a.model && <span className="rounded border px-1.5 py-0.5 font-mono">{a.model}</span>}
        {a.family && <span className={`rounded px-1.5 py-0.5 font-mono ${a.side === "verify" ? "bg-success/10 text-success" : "bg-primary/10 text-primary"}`}>{a.family}{a.side ? ` · ${a.side}` : ""}</span>}
      </div>
      <p className="mt-2 font-mono text-[11px] text-muted-foreground">{a.stage ? `${a.stage}${a.phase ? ` · ${a.phase}` : ""}` : "not working now"}</p>
      <p className="mt-1 flex gap-3 font-mono text-[11px]"><span>{a.calls} calls</span><span>{a.tool_calls} tool calls</span><span>{a.repairs} repairs</span></p>
    </li>
  );
}

export function Agents({ data, activeIds }: { data: AgentsResponse; activeIds?: Set<string> }) {
  const agents = data.agents.map((a) => (activeIds ? { ...a, status: activeIds.has(a.id) ? ("active" as const) : a.status === "active" ? ("done" as const) : a.status } : a));
  const active = agents.filter((a) => a.status === "active").length;
  const f = data.families;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <p className="flex items-center gap-2 text-sm font-semibold" data-testid="active-agents"><Bot className="h-4 w-4 text-primary" />{active} agent{active === 1 ? "" : "s"} active now</p>
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-1.5 text-xs" data-testid="families">
          <span><b className="text-primary">Design</b> {f.design.length ? f.design.join(", ") : NOT_RECORDED}</span>
          <span className="text-muted-foreground">vs</span>
          <span><b className="text-success">Verify</b> {f.verify.length ? f.verify.join(", ") : NOT_RECORDED}</span>
          <Tip text="The model that designs is never the family that checks, so no model grades its own work.">
            <span className={`rounded px-1.5 py-0.5 font-medium ${f.independent ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive"}`}>{f.independent ? "different families: no self-grading" : "families overlap"}</span></Tip>
        </div>
      </div>
      {agents.length === 0 ? <Empty title="No agents listed">The server returned no agents for this run.</Empty>
        : <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{agents.map((a) => <AgentCard key={a.id} a={a} />)}</ul>}
    </div>
  );
}

// ---------------------------------------------------------------- ticker

const TONE_TEXT = { ok: "text-success", bad: "text-destructive", warn: "text-warning", info: "text-foreground" } as const;

export function Ticker({ lines, present }: { lines: TickerLine[]; present: boolean }) {
  const box = useRef<HTMLOListElement>(null);
  const last = lines.length;
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; }, [last]);
  return (
    <section aria-label="Event ticker" className="rounded-lg border bg-card">
      <div className="flex items-center gap-2 border-b px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-muted-foreground"><ScrollText className="h-3.5 w-3.5" /> Event ticker <span className="ml-auto normal-case">{lines.length} events</span></div>
      {lines.length === 0 ? <p className="p-3 text-sm text-muted-foreground">No events recorded yet.</p> : (
        <ol ref={box} className={`${present ? "h-44 text-sm" : "h-36 text-xs"} space-y-0.5 overflow-y-auto p-2 font-mono`}>
          {lines.slice(-300).map((l) => (
            <li key={l.index} className={`flex gap-2 ${TONE_TEXT[l.tone]}`}>
              <span className="shrink-0 text-muted-foreground">{new Date(l.ts).toISOString().slice(11, 19)}</span><span className="min-w-0 break-words">{l.text}</span>
            </li>))}
        </ol>)}
    </section>
  );
}

// ---------------------------------------------------------------- Break it

export function BreakIt({ steps, onBreak, busy, unavailable, error, sample }: {
  steps: BreakStep[]; onBreak: () => void; busy: boolean; unavailable: string | null; error: string | null; sample: boolean;
}) {
  const started = steps[0].done;
  return (
    <section aria-label="Break it" className="rounded-lg border bg-card p-3">
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <button onClick={onBreak} disabled={busy || !!unavailable} className="flex items-center gap-2 rounded-md bg-destructive px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
          <Zap className="h-4 w-4" /> Break it
        </button>
        <p className="text-xs text-muted-foreground">{unavailable ?? (sample ? "Sample data: replays a recorded fault, nothing is injected." : "Injects a real fault into a copy of the design, then watch the checks catch and repair it.")}</p>
      </div>
      {error && <p role="alert" className="mb-2 rounded border border-destructive/40 bg-destructive/5 px-2 py-1 text-xs text-destructive">{error}</p>}
      <ol className="grid gap-2 sm:grid-cols-5" aria-label="Fault steps">
        {steps.map((s, i) => (
          <li key={s.key} data-step={s.key} data-done={s.done} className={`rounded-md border p-2 text-xs ${s.done ? (s.tone === "ok" ? "border-success/50 bg-success/10" : s.tone === "bad" ? "border-destructive/50 bg-destructive/10" : "border-warning/50 bg-warning/10") : "border-dashed text-muted-foreground"}`}>
            <p className="font-semibold">{i + 1}. {s.title}</p><p className="mt-0.5 break-words font-mono text-[10px]">{s.done ? s.detail : started ? "waiting…" : "—"}</p>
          </li>))}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------- legend

const RULES: [string, string, string][] = [
  ["The spec is the truth", "One written spec per project is the only source of requirements.", "Agents quietly building to different versions of the requirements."],
  ["Scripts decide", "Pass or fail comes from scripts that read files; models only write code.", "A model grading its own work and calling it good."],
  ["Independent verifier", "A different model family writes the checks than the one that designs.", "A shared blind spot letting the same mistake into both the design and its test."],
  ["Human approval", "A person approves the requirements and checks by hash before building.", "An unreviewed requirement or check becoming the standard."],
  ["Reproducible builds", "A rebuild from committed code gives byte-identical files.", "A result nobody can recreate, or one that changed after it was checked."],
  ["Canaries", "Every verifier must fail a known-bad input before its pass is trusted.", "A checker that always says pass."],
  ["Contract board", "Stages share budgets and interfaces only through the contract, changed by recorded requests.", "Parallel agents breaking each other's mass, space, power or pins."],
  ["Evidence levels", "Every verdict says calculated, simulated or measured.", "A calculation being presented as a real measurement."],
];

export function Legend() {
  return (
    <section aria-label="The eight rules">
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {RULES.map(([title, what, prevents], i) => (
          <li key={title} className="rounded-lg border bg-card p-3 text-sm">
            <p className="flex items-center gap-2 font-semibold"><span className="grid h-5 w-5 place-items-center rounded-full bg-primary/10 font-mono text-[11px] text-primary">{i + 1}</span>{title}</p>
            <p className="mt-1 text-xs text-muted-foreground">{what}</p>
            <p className="mt-1.5 text-xs"><b>Prevents:</b> {prevents}</p>
          </li>))}
      </ul>
    </section>
  );
}

