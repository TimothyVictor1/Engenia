// @vitest-environment jsdom
// Mission Control view: real-shaped data in, honest empty states when the server lacks a route.

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { App } from "./App";

const file = (name: string) => readFileSync(`public/sample/${name}`, "utf8");
const json = (body: unknown, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status });

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CLOSED = 2;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((e: { data: string }) => void)[]>();
  constructor() { FakeEventSource.instances.push(this); }
  addEventListener(n: string, fn: (e: { data: string }) => void) { this.listeners.set(n, [...(this.listeners.get(n) ?? []), fn]); }
  close() { this.readyState = 2; }
  emit(n: string, data: unknown) { this.listeners.get(n)?.forEach((fn) => fn({ data: JSON.stringify(data) })); }
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;
const calls: { url: string; method: string; body?: string }[] = [];
function install(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method ?? "GET", body: init?.body as string | undefined });
    return handler(String(url), init);
  }));
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} unobserve() {} });
}
const sampleFiles: Handler = (url) => json(file(url.replace(/^sample\//, "").replace(/^files\/.*/, "files.json")));
/** A live server that has the route data. */
const live = (over: Record<string, Response | (() => Response)> = {}): Handler => (url) => {
  const key = Object.keys(over).find((k) => url.includes(k));
  if (key) { const r = over[key]; return typeof r === "function" ? r() : r; }
  if (url.includes("api/pipeline")) return json(file("pipeline.json"));
  if (url.includes("api/events/history")) return json({ run_id: 7, events: JSON.parse(file("events/history.json")) });
  if (url.includes("api/agents")) return json(file("agents.json"));
  if (url.includes("api/run/summary")) return json(file("run/summary.json"));
  if (url.includes("api/demo-fault")) return json(file("demo-fault.json"));
  return new Response("", { status: 404 });
};

const open = (query = "") => { history.replaceState(null, "", `/${query}#/mission`); return render(<App />); };
beforeEach(() => { FakeEventSource.instances = []; vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("with sample data", () => {
  it("shows the graph, the three layers, the agents and the eight rules", async () => {
    install(sampleFiles);
    open("?sample=1");
    expect(await screen.findByRole("region", { name: /pipeline graph/i })).toBeTruthy();
    for (const stage of ["verify_plan", "mechanical", "firmware", "report"]) expect(document.querySelector(`[data-stage="${stage}"]`)).toBeTruthy();
    for (const band of ["before", "during", "after"]) expect(document.querySelector(`[data-band="${band}"]`)).toBeTruthy();
    for (const layer of ["before", "during", "after"]) expect(document.querySelector(`[data-layer="${layer}"]`)).toBeTruthy();
    for (const g of ["G1", "G2", "G3", "G4", "G5"]) expect(document.querySelector(`[data-step="${g}"]`)?.getAttribute("data-mark")).toBe("pass");
    expect(await screen.findByText(/0 agents active now/)).toBeTruthy();
    expect(screen.getByTestId("families").textContent).toContain("different families: no self-grading");
    expect(screen.getAllByRole("tooltip").length).toBeGreaterThan(5);
    expect(screen.getByRole("region", { name: /the eight rules/i }).querySelectorAll("li")).toHaveLength(8);
    expect(screen.getAllByText(/Sample data/).length).toBeGreaterThan(0);
  });
  it("draws cache hits, the repair counter and the lit contract arcs", async () => {
    install(sampleFiles);
    open("?sample=1");
    await screen.findByRole("region", { name: /pipeline graph/i });
    expect(document.querySelector('[data-stage="firmware"]')?.textContent).toContain("cache hit");
    expect(document.querySelector('[data-stage="mechanical"]')?.textContent).toContain("attempt 2");
    await waitFor(() => expect(document.querySelector('[data-arc="repair"][data-lit="true"]')).toBeTruthy());
    expect(document.querySelector('[data-arc="change_request"][data-lit="true"]')).toBeTruthy();
    expect(screen.getByTestId("lanes").textContent).toContain("parallel lanes");
    expect(document.querySelector('[data-loop="repair"]')?.textContent).toContain("1 repair");
    expect(document.querySelector('[data-loop="change_request"]')?.textContent).toContain("1 accepted · 1 rejected");
  });
  it("expands a stage to its phases", async () => {
    install(sampleFiles);
    open("?sample=1");
    await screen.findByRole("region", { name: /pipeline graph/i });
    fireEvent.click(within(document.querySelector('[data-stage="mechanical"]') as HTMLElement).getByRole("button"));
    const detail = await screen.findByLabelText(/Stage mechanical detail/);
    expect(detail.textContent).toContain("generate");
    expect(detail.textContent).toContain("repair");
  });
  it("replays a finished run step by step", async () => {
    install(sampleFiles);
    open("?sample=1");
    await screen.findByRole("region", { name: /pipeline graph/i });
    fireEvent.click(screen.getByRole("button", { name: /replay this run/i }));
    const slider = await screen.findByRole("slider", { name: /position in the run/i });
    expect(screen.getByText(/^0 \/ 119$/)).toBeTruthy();
    expect(document.querySelector('[data-step="G1"]')?.getAttribute("data-mark")).toBe("none");
    fireEvent.change(slider, { target: { value: "119" } });
    await waitFor(() => expect(document.querySelector('[data-step="G1"]')?.getAttribute("data-mark")).toBe("pass"));
  });
  it("replays the recorded fault in sample mode and lights every break-it step", async () => {
    install(sampleFiles);
    open("?sample=1");
    await screen.findByRole("region", { name: /pipeline graph/i });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByRole("button", { name: /break it/i }));
    await act(async () => { await vi.advanceTimersByTimeAsync(700 * 25); });
    vi.useRealTimers();
    for (const step of ["fault", "fail", "repair", "fixed", "gate"]) expect(document.querySelector(`[data-step="${step}"]`)?.getAttribute("data-done")).toBe("true");
  });
});

describe("with a live server", () => {
  it("says so when the server has no agents, summary or fault routes yet", async () => {
    install(live({ "api/agents": json({}, 404), "api/run/summary": json({}, 404), "api/demo-fault": json({}, 501) }));
    open();
    expect(await screen.findByText(/Agents: not available yet/)).toBeTruthy();
    const button = await screen.findByRole("button", { name: /break it/i });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByText(/Fault injection is not available on this server yet/)).toBeTruthy();
    expect(document.querySelector('[data-loop="calibration"]')?.textContent).toContain("not recorded yet");
    expect(document.querySelector('[data-step="G1"]')?.getAttribute("data-mark")).toBe("pass"); // still from events
  });
  it("shows a run with no events as empty, not as a run", async () => {
    install(live({ "api/events/history": json({ run_id: null, events: [] }), "api/agents": json({}, 404), "api/run/summary": json({}, 404), "api/demo-fault": json({}, 404) }));
    open();
    expect((await screen.findAllByText(/No events recorded yet/)).length).toBeGreaterThan(0);
    // Stage status still comes from the API's run state; no phase or check is invented from it.
    expect(document.querySelector('[data-phase][data-state="ok"]')).toBeNull();
    expect(document.querySelector('[data-stage] [aria-label="Scripted checks"] span')).toBeNull();
    expect(document.querySelector('[data-step="G1"]')?.getAttribute("data-mark")).toBe("none");
  });
  it("lights a stage from named design events on the live stream", async () => {
    install(live({ "api/events/history": json({ run_id: null, events: [] }) }));
    open();
    await screen.findByRole("region", { name: /pipeline graph/i });
    const source = FakeEventSource.instances.at(-1)!;
    act(() => source.emit("design", { ts: "2026-11-14T10:00:00.000Z", run_id: 9, project: "harbour-listener", stage: "mechanical", attempt: 1, kind: "phase_start", data: { phase: "generate" } }));
    await waitFor(() => expect(document.querySelector('[data-stage="mechanical"]')?.getAttribute("data-status")).toBe("running"));
    expect(document.querySelector('[data-stage="mechanical"] [data-phase="generate"]')?.getAttribute("data-state")).toBe("active");
    act(() => source.emit("design", { ts: "2026-11-14T10:00:03.000Z", run_id: 9, project: "harbour-listener", stage: "mechanical", attempt: 1, kind: "phase_end", data: { phase: "generate", duration_ms: 3000, result: "ok" } }));
    await waitFor(() => expect(document.querySelector('[data-stage="mechanical"] [data-phase="generate"]')?.getAttribute("data-state")).toBe("ok"));
    expect(screen.getAllByText(/mechanical · generate finished in 3\.0 s/).length).toBeGreaterThan(0);
  });
  it("Break it posts the fault and lights the steps from real events", async () => {
    install(live({ "api/events/history": json({ run_id: null, events: [] }), "api/demo-fault": (() => { let n = 0; return () => (n++ === 0 ? json(file("demo-fault.json")) : json({ run_id: 8, fault: { id: "F01-wall-1mm" } }, 202)); })() as never }));
    open();
    const button = await screen.findByRole("button", { name: /break it/i });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.includes("api/demo-fault"))).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "POST")!.body!)).toEqual({ fault_id: "F01-wall-1mm" });
    const source = FakeEventSource.instances.at(-1)!;
    act(() => source.emit("design", { ts: "2026-11-14T11:00:00.000Z", run_id: 8, project: "harbour-listener", stage: "mechanical", attempt: 1, kind: "fault", data: { id: "F01-wall-1mm", description: "Hull wall set to 1 mm", injected_into: "src/mechanical/hull.py" } }));
    act(() => source.emit("design", { ts: "2026-11-14T11:00:02.000Z", run_id: 8, project: "harbour-listener", stage: "mechanical", attempt: 1, kind: "verdict", data: { check: "wall_thickness_min", requirement: "R-004", result: "fail", measured: 1, limit: { min: 3 } } }));
    await waitFor(() => expect(document.querySelector('[data-step="fail"]')?.getAttribute("data-done")).toBe("true"));
    expect(document.querySelector('[data-step="fixed"]')?.getAttribute("data-done")).toBe("false");
    expect(document.querySelector('[data-step="fail"]')?.textContent).toContain("wall_thickness_min fail");
  });
  it("reports a refused fault (a run is already in progress)", async () => {
    install(live({ "api/events/history": json({ run_id: null, events: [] }), "api/demo-fault": (() => { let n = 0; return () => (n++ === 0 ? json(file("demo-fault.json")) : json({ detail: "a run is in progress" }, 409)); })() as never }));
    open();
    const button = await screen.findByRole("button", { name: /break it/i });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    expect((await screen.findByRole("alert")).textContent).toContain("already in progress");
  });
  it("shows an error, not a blank page, when everything fails", async () => {
    install(() => new Response("boom", { status: 500, statusText: "Server Error" }));
    open();
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: /Mission Control/ })).toBeTruthy();
  });
});

describe("presentation mode", () => {
  it("hides the navigation and offers a way out", async () => {
    install(sampleFiles);
    open("?sample=1&present=1");
    expect(await screen.findByRole("button", { name: /exit presentation/i })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: /views/i })).toBeNull();
    expect(screen.queryByRole("region", { name: /the eight rules/i })).toBeNull();
    expect(screen.getByRole("region", { name: /pipeline graph/i })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /exit presentation/i }));
    expect(await screen.findByRole("navigation", { name: /views/i })).toBeTruthy();
  });
});
