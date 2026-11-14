import { describe, expect, it } from "vitest";
import history from "../../public/sample/events/history.json";
import faultLog from "../../public/sample/events/fault.json";
import { parseEvent, type LiveEvent } from "./live";
import { breakSteps, deriveMission, describe as say, latestRun, mergeEvents } from "./mission";

const parse = (list: unknown[]) => list.map(parseEvent).filter((e): e is LiveEvent => e !== null);
const run = parse(history);
const fault = parse(faultLog);

describe("fixtures follow docs/mission-api.md", () => {
  it("every event parses, including the new kinds", () => {
    expect(run).toHaveLength(history.length);
    expect(fault).toHaveLength(faultLog.length);
    const kinds = new Set(run.map((e) => e.kind));
    for (const k of ["phase_start", "phase_end", "cache", "gate_step", "approval", "change_request", "proposal"]) expect(kinds.has(k as never)).toBe(true);
  });
});

describe("deriveMission", () => {
  it("shows nothing for no events: no invented data", () => {
    const m = deriveMission([]);
    expect(m.stages).toEqual({});
    expect(m.gate).toEqual({});
    expect(m.counters).toMatchObject({ repairs: 0, cacheHits: 0, proposals: 0, cr: { open: 0, accepted: 0, rejected: 0 } });
    expect(m.ticker).toEqual([]);
  });
  const m = deriveMission(run);
  it("keeps every phase with attempt, result and duration", () => {
    const mech = m.stages.mechanical;
    expect(mech.attempts).toBe(2);
    expect(mech.phases.filter((p) => p.phase === "generate")).toHaveLength(2);
    expect(mech.phases.find((p) => p.phase === "build" && p.attempt === 1)).toMatchObject({ durationMs: 4200, result: "ok" });
    expect(mech.active).toBeNull();
  });
  it("marks the live phase while it is open", () => {
    const i = run.findIndex((e) => e.kind === "phase_start" && e.stage === "mechanical" && e.data.phase === "build");
    const mid = deriveMission(run.slice(0, i + 1));
    expect(mid.stages.mechanical.active).toBe("build");
    expect(mid.stages.mechanical.phases.at(-1)?.end).toBeNull();
  });
  it("shows cache hits and misses", () => {
    expect(m.stages.firmware.cacheHit).toBe(true);
    expect(m.stages.mechanical.cacheHit).toBeNull();
    expect(m.stages.architect.cacheHit).toBe(false);
    expect(m.counters.cacheHits).toBe(1);
  });
  it("counts the four feedback loops from events", () => {
    expect(m.counters.repairs).toBe(1);
    expect(m.stages.mechanical.repairs).toBe(1);
    expect(m.counters.cr).toEqual({ open: 0, accepted: 1, rejected: 1 });
    expect(m.changeRequests.find((c) => c.id === "CR-002")).toMatchObject({ status: "rejected", stage: "firmware" });
    expect(m.proposals).toHaveLength(1);
    expect(m.firing.repair).toBeGreaterThan(0);
    expect(m.firing.calibration).toBe(0); // no event kind for it: left to the summary
  });
  it("a change request moves from open to its decision", () => {
    const open = run.findIndex((e) => e.kind === "change_request" && e.data.status === "open");
    expect(deriveMission(run.slice(0, open + 1)).counters.cr.open).toBe(1);
  });
  it("collects gate steps, approvals and the latest check results", () => {
    expect(Object.keys(m.gate).sort()).toEqual(["G1", "G2", "G3", "G4", "G5", "audit"]);
    expect(m.gate.G3).toMatchObject({ result: "pass", detail: "rebuilt electronics: byte-identical" });
    expect(m.approvals.spec.hash).toContain("sha256:");
    expect(m.stages.mechanical.checks.find((c) => c.check === "mass_budget")?.result).toBe("pass");
    expect(m.counters.checksFailed).toBe(0);
  });
  it("the same first N events always give the same picture", () => {
    for (const n of [0, 7, 40, run.length]) expect(deriveMission(run.slice(0, n))).toEqual(deriveMission(run.slice(0, n)));
  });
});

describe("ticker wording", () => {
  const lines = deriveMission(run).ticker.map((l) => l.text);
  it("reads like a sentence", () => {
    expect(lines).toContain("mechanical · design agent chose wall 3.2 mm because R-004 needs at least 3 mm");
    expect(lines).toContain("G3 rebuilt electronics: byte-identical ✓");
    expect(lines.some((l) => l.startsWith("mechanical · repair loop, attempt 2"))).toBe(true);
    expect(lines).toContain("firmware · change request CR-002 rejected: would exceed the part's 512 kB flash");
    expect(lines).toContain("mechanical · mass_budget fail (R-003): 318.4 vs ≤ 300 ✗");
  });
  it("marks failures red and passes green", () => {
    const tones = Object.fromEntries(deriveMission(run).ticker.map((l) => [l.text, l.tone]));
    expect(tones["mechanical · mass_budget fail (R-003): 318.4 vs ≤ 300 ✗"]).toBe("bad");
    expect(tones["G3 rebuilt electronics: byte-identical ✓"]).toBe("ok");
  });
  it("describes an event of any kind", () => {
    expect(say(run[0]).text).toContain("verify_plan");
  });
});

describe("break it steps", () => {
  it("none are done before a fault", () => {
    expect(breakSteps(run, deriveMission(run)).every((s) => !s.done)).toBe(true);
  });
  it("light up one by one as real events arrive", () => {
    const all = [...run, ...fault];
    const at = (pred: (e: LiveEvent) => boolean) => all.findIndex(pred) + 1;
    const done = (n: number) => { const e = all.slice(0, n); return breakSteps(e, deriveMission(e)).map((s) => s.done); };
    expect(done(at((e) => e.kind === "fault"))).toEqual([true, false, false, false, false]);
    expect(done(at((e) => e.stage === "mechanical" && e.attempt === 3 && e.kind === "verdict"))).toEqual([true, true, false, false, false]);
    expect(done(at((e) => e.kind === "repair" && e.attempt === 4))).toEqual([true, true, true, false, false]);
    expect(done(at((e) => e.kind === "verdict" && e.attempt === 4))).toEqual([true, true, true, true, false]);
    expect(done(all.length)).toEqual([true, true, true, true, true]);
  });
});

describe("merging history and live events", () => {
  it("drops duplicates and keeps time order", () => {
    const merged = mergeEvents(run.slice(0, 10), [...run.slice(5, 15)]);
    expect(merged).toHaveLength(15);
    const t = merged.map((e) => Date.parse(e.ts));
    expect(t).toEqual([...t].sort((a, b) => a - b));
  });
  it("shows only the newest run", () => {
    const older = { ...run[0], run_id: 3 };
    expect(latestRun([older, ...run]).every((e) => e.run_id === 7)).toBe(true);
  });
});
