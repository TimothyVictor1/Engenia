// Live studio events and the pure logic that turns a list of them into what the screen shows.
// Format: {ts, run_id, stage, attempt, kind, data}. Everything here is deterministic, so a replay
// of the first N events always draws exactly what the live view drew after N events.

export const KINDS = [
  "message", "tool_call", "tool_result", "code", "decision", "preview", "verdict", "repair",
  // Mission Control (docs/mission-api.md)
  "phase_start", "phase_end", "cache", "gate_step", "approval", "change_request", "proposal", "fault",
] as const;
export type Kind = (typeof KINDS)[number];

export interface LiveEvent {
  ts: string;
  run_id: string | number;
  project?: string;
  stage: string;
  attempt: number;
  kind: Kind;
  data: Record<string, unknown>;
}

/** Accept only well-formed events; anything else on the stream is ignored. */
export function parseEvent(value: unknown): LiveEvent | null {
  if (!value || typeof value !== "object") return null;
  const e = value as Record<string, unknown>;
  if (typeof e.ts !== "string" || Number.isNaN(Date.parse(e.ts))) return null;
  if (typeof e.stage !== "string" || !KINDS.includes(e.kind as Kind)) return null;
  const data = e.data && typeof e.data === "object" && !Array.isArray(e.data) ? e.data : {};
  return {
    ts: e.ts,
    run_id: typeof e.run_id === "number" || typeof e.run_id === "string" ? e.run_id : "",
    project: typeof e.project === "string" ? e.project : undefined,
    stage: e.stage,
    attempt: typeof e.attempt === "number" ? e.attempt : 1,
    kind: e.kind as Kind,
    data: data as Record<string, unknown>,
  };
}

const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map(str) : []);

// ---------------------------------------------------------------- derived state

export interface Span {
  stage: string; attempt: number; start: number; end: number;
  /** pass/fail from the last verdict in the span; "repair" if it ended in a repair. */
  outcome: "pass" | "fail" | "error" | "repair" | "running";
}
export interface DecisionCard {
  stage: string; title: string; choice: string; reason: string;
  rejected: { option: string; why: string }[]; requirements: string[]; ts: string;
}
export interface CodeFile { path: string; language: string; code: string; diff: string[]; ts: string }
export interface Studio {
  spans: Span[];
  decisions: DecisionCard[];
  code: CodeFile | null;
  preview: { stage: string; files: string[]; url: string | null; ts: string } | null;
  feed: LiveEvent[];
  start: number;
  end: number;
}

type Outcome = Span["outcome"];

export function lineDiff(before: string, after: string): string[] {
  const a = before === "" ? [] : before.split("\n");
  const b = after.split("\n");
  // Longest common subsequence over lines; generated CAD files are a few hundred lines.
  const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out: string[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push(` ${a[i]}`); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) { out.push(`-${a[i]}`); i += 1; }
    else { out.push(`+${b[j]}`); j += 1; }
  }
  while (i < a.length) out.push(`-${a[i++]}`);
  while (j < b.length) out.push(`+${b[j++]}`);
  return out;
}

export function deriveStudio(events: LiveEvent[]): Studio {
  const times = events.map((e) => Date.parse(e.ts));
  const start = times.length ? Math.min(...times) : 0;
  const end = times.length ? Math.max(...times) : 0;
  const spans = new Map<string, Span>();
  const decisions: DecisionCard[] = [];
  const files = new Map<string, string>();
  let code: CodeFile | null = null;
  let preview: Studio["preview"] = null;

  for (const e of events) {
    const t = Date.parse(e.ts);
    const key = `${e.stage}#${e.attempt}`;
    const span = spans.get(key) ?? { stage: e.stage, attempt: e.attempt, start: t, end: t, outcome: "running" as Outcome };
    span.end = Math.max(span.end, t);
    if (e.kind === "verdict") {
      const result = str(e.data.result);
      // One failing verdict makes the attempt fail; a later passing one cannot undo it.
      if (result === "fail" || result === "error") span.outcome = result;
      else if (result === "pass" && span.outcome !== "fail" && span.outcome !== "error") span.outcome = "pass";
    } else if (e.kind === "repair") {
      span.outcome = "repair";
    }
    spans.set(key, span);

    if (e.kind === "decision") {
      const rejected = Array.isArray(e.data.rejected) ? e.data.rejected : [];
      decisions.push({
        stage: e.stage, ts: e.ts,
        title: str(e.data.title), choice: str(e.data.choice), reason: str(e.data.reason),
        rejected: rejected.map((r) =>
          r && typeof r === "object"
            ? { option: str((r as Record<string, unknown>).option), why: str((r as Record<string, unknown>).why) }
            : { option: str(r), why: "" }),
        requirements: strings(e.data.requirements),
      });
    } else if (e.kind === "code") {
      const path = str(e.data.path) || "model.py";
      const text = str(e.data.code ?? e.data.content);
      const diff = Array.isArray(e.data.diff) ? strings(e.data.diff)
        : typeof e.data.diff === "string" ? e.data.diff.split("\n")
        : lineDiff(files.get(path) ?? "", text);
      files.set(path, text);
      code = { path, language: str(e.data.language) || "python", code: text, diff, ts: e.ts };
    } else if (e.kind === "preview") {
      const list = strings(e.data.files).concat(e.data.file ? [str(e.data.file)] : []);
      preview = { stage: str(e.data.stage) || e.stage, files: list.filter((f) => f.endsWith(".stl")), url: typeof e.data.stl === "string" && e.data.stl.startsWith("/api/") ? e.data.stl : null, ts: e.ts };
    }
  }
  return {
    spans: [...spans.values()].sort((x, y) => x.start - y.start || x.attempt - y.attempt),
    decisions, code, preview, feed: events, start, end,
  };
}

/** How long to wait before showing event i+1, in ms: real gap / speed, kept watchable. */
export function replayDelay(events: LiveEvent[], i: number, speed: number): number {
  if (i + 1 >= events.length) return 0;
  const gap = Date.parse(events[i + 1].ts) - Date.parse(events[i].ts);
  return Math.min(1200, Math.max(120, (Number.isFinite(gap) ? gap : 0) / Math.max(speed, 0.25)));
}
