// Mission Control: shapes from docs/mission-api.md and the pure logic that turns real events into
// what the screen shows. Nothing here invents data: a value the events or the API do not carry
// stays null and the UI says "not recorded yet".

import { fmtDuration, fmtNumber } from "./format";
import type { LiveEvent } from "./live";

export const PHASES = ["plan", "propose", "generate", "build", "check", "repair", "publish", "cache"] as const;
export type Phase = (typeof PHASES)[number];

// ---------------------------------------------------------------- API shapes

export interface Agent {
  id: string; name: string; role: string; kind: "llm" | "deterministic" | "script";
  model: string | null; family: string | null; side: "design" | "verify" | null;
  status: "idle" | "active" | "done" | "failed"; stage: string | null; phase: string | null;
  calls: number; tool_calls: number; repairs: number;
}
export interface AgentsResponse {
  run_id: number | null; agents: Agent[];
  families: { design: string[]; verify: string[]; independent: boolean };
}
export interface RunSummary {
  run_id: number; project: string; status: string; started: string | null; finished: string | null;
  stages: { name: string; status: string; attempts: number; cache_hit: boolean; duration_ms: number | null;
    checks: { check: string; requirement: string | null; result: string }[] }[];
  before: {
    verify_plan: { model: string | null; family: string | null; result: string | null; requirements: number | null; checks: number | null } | null;
    approval: { hash: string | null; approved: boolean; approved_by: string | null; approved_at: string | null } | null;
  };
  during: { checks_total: number; passed: number; failed: number } | null;
  after: { gate: { step: string; title: string; result: string; detail: string }[]; audit: { result: string; detail: string } | null };
  loops: {
    repair: { attempts: number; fixed: number }; change_requests: { open: number; accepted: number; rejected: number };
    proposals: { draft: number }; calibration: { measurements: number; applied: number };
  };
}

// ---------------------------------------------------------------- derived state

export interface PhaseRun {
  phase: Phase; attempt: number; start: number; end: number | null;
  durationMs: number | null; result: "ok" | "fail" | "skipped" | null;
}
export interface StageState {
  name: string; phases: PhaseRun[]; active: Phase | null; attempts: number; cacheHit: boolean | null;
  first: number; last: number; repairs: number;
  checks: { check: string; requirement: string | null; result: string }[];
}
export interface GateStep { step: string; title: string; result: string; detail: string; durationMs: number | null }
export interface ChangeRequestState { id: string; stage: string; path: string; requested: unknown; status: string; reason: string }
export interface ProposalState { stage: string; requirement: string; oldLimit: unknown; newLimit: unknown; reason: string; status: string }
export type Tone = "ok" | "bad" | "warn" | "info";
export interface TickerLine { index: number; ts: number; stage: string; text: string; tone: Tone }
export type LoopId = "repair" | "change_request" | "proposal" | "calibration";

export interface Mission {
  stages: Record<string, StageState>;
  gate: Record<string, GateStep>;
  approvals: Record<string, { what: string; hash: string; approved: boolean; approvedBy: string | null }>;
  changeRequests: ChangeRequestState[];
  proposals: ProposalState[];
  fault: { id: string; description: string; injectedInto: string; ts: number; index: number } | null;
  counters: { repairs: number; cacheHits: number; checksPassed: number; checksFailed: number;
    cr: { open: number; accepted: number; rejected: number }; proposals: number };
  /** event index (1-based) of the last event that fired each loop; 0 = never */
  firing: Record<LoopId, number>;
  ticker: TickerLine[];
  count: number;
  start: number; end: number;
}

const str = (v: unknown): string => (v == null ? "" : typeof v === "string" ? v : typeof v === "object" ? JSON.stringify(v) : String(v));
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const isPhase = (v: unknown): v is Phase => (PHASES as readonly string[]).includes(v as string);

function limitText(limit: unknown): string {
  if (!limit || typeof limit !== "object") return "";
  const l = limit as Record<string, unknown>;
  const parts: string[] = [];
  if (l.min != null) parts.push(`≥ ${fmtNumber(l.min)}`);
  if (l.max != null) parts.push(`≤ ${fmtNumber(l.max)}`);
  if (l.equals != null) parts.push(`= ${fmtNumber(l.equals)}`);
  return parts.join(", ");
}

/** One human-readable line per event, for the ticker. */
export function describe(e: LiveEvent): { text: string; tone: Tone } {
  const d = e.data;
  const at = e.stage;
  switch (e.kind) {
    case "phase_start": return { text: `${at} · ${str(d.phase)} started${e.attempt > 1 ? ` (attempt ${e.attempt})` : ""}`, tone: "info" };
    case "phase_end": {
      const result = str(d.result);
      const ms = num(d.duration_ms);
      return { text: `${at} · ${str(d.phase)} ${result === "ok" ? "finished" : result === "skipped" ? "skipped" : "failed"}${ms != null ? ` in ${fmtDuration(ms / 1000)}` : ""}`, tone: result === "fail" ? "bad" : "info" };
    }
    case "decision": return { text: `${at} · design agent chose ${str(d.choice) || str(d.title)}${d.reason ? ` because ${str(d.reason)}` : ""}`, tone: "info" };
    case "verdict": {
      const result = str(d.result);
      const limit = limitText(d.limit);
      const measured = num(d.measured);
      return { text: `${at} · ${str(d.check)} ${result}${d.requirement ? ` (${str(d.requirement)})` : ""}${measured != null ? `: ${fmtNumber(measured)}${limit ? ` vs ${limit}` : ""}` : ""}${result === "pass" ? " ✓" : " ✗"}`, tone: result === "pass" ? "ok" : "bad" };
    }
    case "repair": return { text: `${at} · repair loop, attempt ${e.attempt}: ${str(d.summary ?? d.reason ?? d.message)}`, tone: "warn" };
    case "gate_step": {
      const result = str(d.result);
      if (result === "running") return { text: `${str(d.step)} ${str(d.title)} running…`, tone: "info" };
      return { text: `${str(d.step)} ${str(d.detail) || str(d.title)} ${result === "pass" ? "✓" : "✗"}`, tone: result === "pass" ? "ok" : "bad" };
    }
    case "approval": return { text: `${at} · ${str(d.what)} ${d.approved ? "approved" : "not approved"} (hash ${str(d.hash).replace("sha256:", "").slice(0, 8)})${d.approved_by ? ` by ${str(d.approved_by)}` : ""}`, tone: d.approved ? "ok" : "warn" };
    case "change_request": {
      const status = str(d.status);
      return { text: `${at} · change request ${str(d.id)} ${status}${d.reason ? `: ${str(d.reason)}` : ""}`, tone: status === "accepted" ? "ok" : status === "rejected" ? "bad" : "warn" };
    }
    case "proposal": return { text: `${at} · proposes ${str(d.requirement)} limit ${limitText(d.old_limit)} → ${limitText(d.new_limit)}${d.reason ? `: ${str(d.reason)}` : ""}`, tone: "warn" };
    case "cache": return { text: `${at} · cache ${d.hit ? "hit: restored, nothing ran" : "miss: building"}`, tone: d.hit ? "ok" : "info" };
    case "fault": return { text: `fault injected into ${at}: ${str(d.description) || str(d.id)}`, tone: "bad" };
    case "code": return { text: `${at} · wrote ${str(d.path)}`, tone: "info" };
    case "preview": return { text: `${at} · 3D preview updated`, tone: "info" };
    case "tool_call": return { text: `${at} · called ${str(d.name ?? d.tool)}`, tone: "info" };
    case "tool_result": return { text: `${at} · ${str(d.summary ?? d.result ?? d.output) || "tool finished"}`, tone: "info" };
    default: return { text: `${at} · ${str(d.text ?? d.message)}`, tone: "info" };
  }
}

const emptyStage = (name: string, t: number): StageState => ({
  name, phases: [], active: null, attempts: 1, cacheHit: null, first: t, last: t, repairs: 0, checks: [],
});

export function deriveMission(events: LiveEvent[]): Mission {
  const stages: Record<string, StageState> = {};
  const gate: Record<string, GateStep> = {};
  const approvals: Mission["approvals"] = {};
  const crs = new Map<string, ChangeRequestState>();
  const proposals: ProposalState[] = [];
  const ticker: TickerLine[] = [];
  const firing: Record<LoopId, number> = { repair: 0, change_request: 0, proposal: 0, calibration: 0 };
  let fault: Mission["fault"] = null;
  let repairs = 0;
  const times = events.map((e) => Date.parse(e.ts));

  events.forEach((e, i) => {
    const t = times[i];
    const s = (stages[e.stage] ??= emptyStage(e.stage, t));
    s.first = Math.min(s.first, t);
    s.last = Math.max(s.last, t);
    s.attempts = Math.max(s.attempts, e.attempt);
    const d = e.data;
    if (e.kind === "phase_start" && isPhase(d.phase)) {
      s.phases.push({ phase: d.phase, attempt: e.attempt, start: t, end: null, durationMs: null, result: null });
    } else if (e.kind === "phase_end" && isPhase(d.phase)) {
      const open = [...s.phases].reverse().find((p) => p.phase === d.phase && p.attempt === e.attempt && p.end === null);
      const ms = num(d.duration_ms);
      const result = d.result === "fail" || d.result === "skipped" ? d.result : "ok";
      if (open) Object.assign(open, { end: t, durationMs: ms ?? t - open.start, result });
      else s.phases.push({ phase: d.phase, attempt: e.attempt, start: ms != null ? t - ms : t, end: t, durationMs: ms, result });
    } else if (e.kind === "cache") {
      s.cacheHit = d.hit === true;
    } else if (e.kind === "verdict") {
      const row = { check: str(d.check), requirement: d.requirement == null ? null : str(d.requirement), result: str(d.result) };
      const at = s.checks.findIndex((c) => c.check === row.check);
      if (at >= 0) s.checks[at] = row; else s.checks.push(row);
    } else if (e.kind === "repair") {
      s.repairs += 1; repairs += 1; firing.repair = i + 1;
    } else if (e.kind === "gate_step") {
      gate[str(d.step)] = { step: str(d.step), title: str(d.title), result: str(d.result), detail: str(d.detail), durationMs: num(d.duration_ms) };
    } else if (e.kind === "approval") {
      approvals[str(d.what)] = { what: str(d.what), hash: str(d.hash), approved: d.approved === true, approvedBy: d.approved_by == null ? null : str(d.approved_by) };
    } else if (e.kind === "change_request") {
      crs.set(str(d.id), { id: str(d.id), stage: e.stage, path: str(d.path), requested: d.requested, status: str(d.status), reason: str(d.reason) });
      firing.change_request = i + 1;
    } else if (e.kind === "proposal") {
      const row = { stage: e.stage, requirement: str(d.requirement), oldLimit: d.old_limit, newLimit: d.new_limit, reason: str(d.reason), status: str(d.status) };
      const at = proposals.findIndex((p) => p.requirement === row.requirement);
      if (at >= 0) proposals[at] = row; else proposals.push(row);
      firing.proposal = i + 1;
    } else if (e.kind === "fault") {
      fault = { id: str(d.id), description: str(d.description), injectedInto: str(d.injected_into), ts: t, index: i + 1 };
    }
    const line = describe(e);
    ticker.push({ index: i + 1, ts: t, stage: e.stage, ...line });
  });

  for (const s of Object.values(stages)) {
    const open = [...s.phases].reverse().find((p) => p.end === null);
    s.active = open ? open.phase : null;
  }
  const checks = Object.values(stages).flatMap((s) => s.checks);
  const crList = [...crs.values()];
  const count = (status: string) => crList.filter((c) => c.status === status).length;
  return {
    stages, gate, approvals, changeRequests: crList, proposals, fault,
    counters: {
      repairs, cacheHits: Object.values(stages).filter((s) => s.cacheHit === true).length,
      checksPassed: checks.filter((c) => c.result === "pass").length,
      checksFailed: checks.filter((c) => c.result !== "pass").length,
      cr: { open: count("open"), accepted: count("accepted"), rejected: count("rejected") },
      proposals: proposals.length,
    },
    firing, ticker, count: events.length,
    start: times.length ? Math.min(...times) : 0, end: times.length ? Math.max(...times) : 0,
  };
}

// ---------------------------------------------------------------- "Break it" steps

export interface BreakStep { key: string; title: string; detail: string; done: boolean; tone: Tone }

/** The five steps of a fault, each lit only by the events that prove it. */
export function breakSteps(events: LiveEvent[], m: Mission): BreakStep[] {
  const f = m.fault;
  const after = f ? events.slice(f.index - 1) : [];
  const failing = after.find((e) => e.kind === "verdict" && e.data.result !== "pass");
  const repair = after.find((e) => e.kind === "repair" || (e.kind === "phase_start" && e.data.phase === "repair"));
  const fixed = failing && after.find((e) => e.kind === "verdict" && e.data.result === "pass" && e.data.check === failing.data.check && Date.parse(e.ts) >= Date.parse(failing.ts));
  const gateSteps = ["G1", "G2", "G3", "G4", "G5"].map((k) => m.gate[k]);
  const green = !!f && gateSteps.every((g) => g && g.result === "pass") && !!fixed
    && Date.parse(events.filter((e) => e.kind === "gate_step").slice(-1)[0]?.ts ?? "") >= (f?.ts ?? 0);
  return [
    { key: "fault", title: "Fault injected", detail: f ? `${f.description || f.id} → ${f.injectedInto}` : "waiting", done: !!f, tone: "bad" },
    { key: "fail", title: "Check fails", detail: failing ? describe(failing).text : "waiting", done: !!failing, tone: "bad" },
    { key: "repair", title: "Repair loop runs", detail: repair ? describe(repair).text : "waiting", done: !!repair, tone: "warn" },
    { key: "fixed", title: "Check passes again", detail: fixed ? describe(fixed).text : "waiting", done: !!fixed, tone: "ok" },
    { key: "gate", title: "Gate goes green", detail: green ? "G1–G5 pass" : "waiting", done: green, tone: "ok" },
  ];
}

// ---------------------------------------------------------------- merging history and live

export const eventKey = (e: LiveEvent) => `${e.run_id}|${e.ts}|${e.stage}|${e.attempt}|${e.kind}|${JSON.stringify(e.data)}`;

/** History first, then live events that history does not already hold, in time order. */
export function mergeEvents(history: LiveEvent[], live: LiveEvent[]): LiveEvent[] {
  const seen = new Set<string>();
  const out: LiveEvent[] = [];
  for (const e of [...history, ...live]) {
    const k = eventKey(e);
    if (!seen.has(k)) { seen.add(k); out.push(e); }
  }
  return out.sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
}

/** Only the newest run's events: Mission Control shows one run. */
export function latestRun(events: LiveEvent[]): LiveEvent[] {
  let best: number | null = null;
  for (const e of events) {
    const id = Number(e.run_id);
    if (Number.isFinite(id) && (best === null || id > best)) best = id;
  }
  return best === null ? events : events.filter((e) => Number(e.run_id) === best);
}
