// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { App } from "./App";

const sample = (name: string) => readFileSync(`public/sample/${name}`, "utf8");
const json = (body: unknown, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status });

class FakeEventSource {
  static CLOSED = 2; readyState = 0;
  onopen: (() => void) | null = null; onmessage: (() => void) | null = null; onerror: (() => void) | null = null;
  addEventListener() {} close() {}
}
const PROJECTS = [
  { name: "antenna-bracket", pack: "bracket", requirements: 4 },
  { name: "harbour-listener", pack: "buoy", requirements: 12 },
  { name: "sensor-pod", pack: "pressure_housing", requirements: 6 },
];
function install(projects: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = String(url);
    if (path.startsWith("api/projects")) return json(projects, status);
    if (path.startsWith("api/pipeline")) return json(JSON.parse(sample("pipeline.json")));
    return new Response("", { status: 404 });
  }));
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} unobserve() {} });
}
const open = () => { history.replaceState(null, "", "/#/pipeline"); return render(<App />); };
beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("project switcher", () => {
  it("offers every project with its pack when the server is switchable", async () => {
    install({ current: "harbour-listener", switchable: true, projects: PROJECTS });
    open();
    const select = (await screen.findByRole("combobox", { name: /project/i })) as HTMLSelectElement;
    expect(select.value).toBe("harbour-listener");
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "antenna-bracket (bracket)", "harbour-listener (buoy)", "sensor-pod (pressure_housing)",
    ]);
  });
  it("shows the plain project name on a single-project server", async () => {
    install({ current: "harbour-listener", switchable: false, projects: PROJECTS });
    open();
    expect(await screen.findByText("harbour-listener")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: /project/i })).toBeNull();
  });
  it("shows the plain name when there is only one project or the list fails", async () => {
    install({ current: "harbour-listener", switchable: true, projects: PROJECTS.slice(1, 2) });
    open();
    expect(await screen.findByText("harbour-listener")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: /project/i })).toBeNull();
    cleanup();
    install({}, 500);
    open();
    expect(await screen.findByText("harbour-listener")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: /project/i })).toBeNull();
  });
});
