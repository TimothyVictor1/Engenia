import { describe, expect, it } from "vitest";
import events from "../../public/sample/live-events.json";
import { deriveStudio, lineDiff, parseEvent, replayDelay, type LiveEvent } from "./live";
import { resolve } from "./api";
import { parseHash } from "./route";

const all = events.map(parseEvent).filter((e): e is LiveEvent => e !== null);

describe("parseEvent", () => {
  it("accepts the whole sample log", () => expect(all).toHaveLength(events.length));
  it("rejects malformed input", () => {
    for (const bad of [null, 3, {}, { ts: "nope", stage: "a", kind: "message" }, { ts: "2026-01-01T00:00:00Z", stage: "a", kind: "unknown" }]) {
      expect(parseEvent(bad)).toBeNull();
    }
  });
  it("defaults missing data and attempt", () => {
    const e = parseEvent({ ts: "2026-01-01T00:00:00Z", stage: "a", kind: "message" });
    expect(e).toMatchObject({ attempt: 1, data: {} });
  });
});

describe("deriveStudio", () => {
  it("is empty for no events", () => {
    const s = deriveStudio([]);
    expect(s).toMatchObject({ spans: [], decisions: [], code: null, preview: null });
  });
  it("a failing verdict makes the attempt fail, a later repair marks it repaired", () => {
    const failed = deriveStudio(all.slice(0, 10));
    expect(failed.spans.find((s) => s.stage === "mechanical")?.outcome).toBe("fail");
    const repaired = deriveStudio(all.slice(0, 11));
    expect(repaired.spans.find((s) => s.stage === "mechanical" && s.attempt === 1)?.outcome).toBe("repair");
  });
  it("replaying the first N events equals deriving from them again", () => {
    for (const n of [0, 5, 10, all.length]) expect(deriveStudio(all.slice(0, n))).toEqual(deriveStudio([...all].slice(0, n)));
  });
  it("collects decisions with rejected alternatives and requirement ids", () => {
    const d = deriveStudio(all).decisions;
    expect(d).toHaveLength(2);
    expect(d[0]).toMatchObject({ title: "Cylindrical hull", requirements: ["R-001", "R-005"] });
    expect(d[0].rejected[0]).toEqual({ option: "Spherical hull", why: "Does not fit the print bed" });
  });
  it("shows the latest code with a diff against the previous version", () => {
    const code = deriveStudio(all).code!;
    expect(code.code).toContain("WALL_MM = 2.0");
    expect(code.diff).toContain("-WALL_MM = 2.4");
    expect(code.diff).toContain("+WALL_MM = 2.0");
  });
  it("keeps the newest preview", () => expect(deriveStudio(all).preview?.files).toEqual(["hull.stl", "lid.stl"]));
});

describe("lineDiff", () => {
  it("marks added, removed and unchanged lines", () => expect(lineDiff("a\nb", "a\nc")).toEqual([" a", "-b", "+c"]));
  it("treats no previous text as all added", () => expect(lineDiff("", "x")).toEqual(["+x"]));
});

describe("replayDelay", () => {
  it("follows the real gap, scaled by speed, within sane bounds", () => {
    expect(replayDelay(all, 0, 1)).toBe(1200); // 3 s gap is capped
    expect(replayDelay(all, 0, 8)).toBe(375);
    expect(replayDelay(all, all.length - 1, 1)).toBe(0);
  });
});

describe("api paths", () => {
  it("maps api paths to sample files in sample mode only", () => {
    expect(resolve("/api/pipeline", true)).toBe("sample/pipeline.json");
    expect(resolve("/api/files/mechanical/hull.stl", true)).toBe("sample/files/mechanical/hull.stl");
    expect(resolve("/api/pipeline", false)).toBe("api/pipeline");
  });
  it("parses routes and falls back to the pipeline", () => {
    expect(parseHash("#/requirements/R-005")).toEqual({ view: "requirements", param: "R-005" });
    expect(parseHash("#/nope")).toEqual({ view: "mission", param: "" });
    expect(parseHash("#/pipeline/%E0%A4%A")).toMatchObject({ view: "pipeline" });
  });
});
