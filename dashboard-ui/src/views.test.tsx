// @vitest-environment jsdom
// Every view must show something useful, never a blank page, whatever the API does.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { App } from "./App";

const sampleFile = (name: string) => readFileSync(`public/sample/${name}`, "utf8");
const ok = (body: unknown) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status: 200 });

class FakeEventSource {
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((e: { data: string }) => void)[]>();
  constructor() { FakeEventSource.instances.push(this); }
  addEventListener(name: string, fn: (e: { data: string }) => void) { this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]); }
  close() { this.readyState = 2; }
  /** Deliver a named SSE message, as the server does for the design log. */
  emit(name: string, data: unknown) { this.listeners.get(name)?.forEach((fn) => fn({ data: JSON.stringify(data) })); }
}

function install(handler: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => handler(String(url))));
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} unobserve() {} });
}

const VIEWS = ["pipeline", "requirements", "design", "contract", "scenarios", "studio"];
const open = (view: string, query = "") => { history.replaceState(null, "", `/${query}#/${view}`); return render(<App />); };

beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("sample data", () => {
  const files = (url: string) => ok(sampleFile(url.replace(/^sample\//, "").replace(/^files\/.*/, "files.json")));
  it("pipeline shows the sample stages", async () => {
    install(files);
    open("pipeline", "?sample=1");
    expect(await screen.findByRole("group", { name: /pipeline stages/i })).toBeTruthy();
    expect(screen.getAllByText(/Sample data/i).length).toBeGreaterThan(0);
  });
  it("requirements, contract and scenarios render their content", async () => {
    install(files);
    open("requirements", "?sample=1");
    expect((await screen.findAllByText(/^R-\d{3}$/)).length).toBeGreaterThan(0);
    cleanup(); open("contract", "?sample=1");
    expect(await screen.findByText(/Budgets and reported facts/i)).toBeTruthy();
    cleanup(); open("scenarios", "?sample=1");
    expect((await screen.findAllByText(/calm_harbour|baltic_winter|storm/i)).length).toBeGreaterThan(0);
  });
  it("live studio replays the sample run with replay controls", async () => {
    install(files);
    open("studio", "?sample=1");
    expect(await screen.findByRole("group", { name: /replay controls/i })).toBeTruthy();
    expect(await screen.findByText("Cylindrical hull")).toBeTruthy();
    expect(screen.getByText(/CAD code/i)).toBeTruthy();
    expect(screen.getAllByText(/mass_budget/).length).toBeGreaterThan(0);
  });
});

describe("API errors", () => {
  for (const view of VIEWS.filter((v) => v !== "studio")) {
    it(`${view} shows an error with a retry, not a blank page`, async () => {
      install(() => new Response("boom", { status: 500, statusText: "Server Error" }));
      open(view);
      if (view === "design") {
        // The design view has its own panels: the facts fail, the viewer says it is not built.
        expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
      } else {
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect(screen.getAllByRole("button", { name: /try again/i }).length).toBeGreaterThan(0);
      }
    }, 15000);
  }
  it("falls back to sample data when there is no server at all", async () => {
    install((url) => (url.startsWith("sample/") ? ok(sampleFile(url.replace(/^sample\//, ""))) : Promise.reject(new TypeError("offline"))));
    open("pipeline");
    expect(await screen.findByRole("group", { name: /pipeline stages/i })).toBeTruthy();
  });
});

describe("empty data", () => {
  const empty = (url: string) => {
    if (url.includes("pipeline")) return ok({ project: "p", updated: "2026-01-01T00:00:00Z", stages: [] });
    if (url.includes("requirements")) return ok({ requirements: [] });
    if (url.includes("contract")) return ok({ source: "none", contract: null, facts: {}, change_requests: [] });
    if (url.includes("scenarios")) return ok({ scenarios: [] });
    return new Response("", { status: 404 });
  };
  it.each([
    ["pipeline", /No stages/],
    ["requirements", /No requirements/],
    ["contract", /No contract/],
    ["scenarios", /No scenarios/],
  ])("%s says what is missing", async (view, text) => {
    install(empty);
    open(view);
    expect((await screen.findAllByText(text)).length).toBeGreaterThan(0);
  });
  it("live studio explains there are no events and offers a sample replay", async () => {
    install(empty);
    open("studio");
    expect(await screen.findByText(/No live events yet/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /replay a sample run/i })).toBeTruthy();
  });
  it("design reports that the model is not built", async () => {
    install((url) => (url.includes("/api/stages") ? ok({ name: "mechanical", status: "not_run", facts: {}, verdicts: [] }) : empty(url)));
    open("design");
    await waitFor(() => expect(screen.getAllByText(/No facts reported|Not built yet|could not start WebGL/i).length).toBeGreaterThan(0));
  });
});

describe("projector theme", () => {
  it("toggles a high-contrast light theme from the header and keeps the choice", async () => {
    install((url) => ok(sampleFile(url.replace(/^sample\//, "").replace(/^files\/.*/, "files.json"))));
    open("pipeline", "?sample=1&theme=light");
    const button = await screen.findByRole("button", { name: /projector theme/i });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    button.click();
    await waitFor(() => expect(document.documentElement.classList.contains("projector")).toBe(true));
    expect(screen.getByRole("button", { name: /projector theme/i }).getAttribute("aria-pressed")).toBe("true");
    expect(localStorage.getItem("engenia-theme")).toBe("projector");
    screen.getByRole("button", { name: /projector theme/i }).click();
    await waitFor(() => expect(document.documentElement.classList.contains("projector")).toBe(false));
    localStorage.removeItem("engenia-theme");
  });
});
