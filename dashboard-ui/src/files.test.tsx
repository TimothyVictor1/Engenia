// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { App } from "./App";
import { fmtSize, parseCsv } from "./views/Files";

const sample = (name: string) => readFileSync(`public/sample/${name}`, "utf8");
const json = (body: unknown, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status });

class FakeEventSource {
  static CLOSED = 2; readyState = 0;
  onopen: (() => void) | null = null; onmessage: (() => void) | null = null; onerror: (() => void) | null = null;
  addEventListener() {} close() {}
}
function install(handler: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => handler(String(url))));
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} unobserve() {} });
}
const entry = (path: string, type: string, size = 10) => ({ path, name: path.split("/").pop(), size, type, modified: "2026-11-14T10:00:00+00:00" });
const LIST = { stage: "mechanical", files: [
  entry("cad/hull.stl", "stl", 25), entry("hull.step", "step", 13), entry("data.csv", "csv"), entry("interface.json", "json"),
  entry("notes.txt", "text"), entry("blob.bin", "binary"), entry("body.step", "step") ] };
const FILES: Record<string, string> = {
  "api/files/mechanical/interface.json": '{"stage":"mechanical","facts":{"dry_mass_g":291.2}}',
  "api/files/mechanical/data.csv": 'a,b\n1,"two, three"\n',
  "api/files/mechanical/notes.txt": "hello world",
};
function live(over: { release?: unknown; list?: unknown; listStatus?: number } = {}) {
  return (url: string) => {
    if (url in FILES) return json(FILES[url]);
    if (url === "api/files/mechanical") return json(over.list ?? LIST, over.listStatus ?? 200);
    if (/^api\/files\/[a-z_]+$/.test(url)) return json({ stage: url.split("/").pop(), files: [] });
    if (url === "api/release") return json(over.release ?? { available: false });
    if (url.startsWith("api/pipeline")) return json(sample("pipeline.json"));
    if (url.startsWith("api/files/")) return new Response("", { status: 404 });
    return new Response("", { status: 404 });
  };
}
const open = (hash: string, query = "") => { history.replaceState(null, "", `/${query}#/files${hash}`); return render(<App />); };
beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("helpers", () => {
  it("formats sizes", () => { expect(fmtSize(12)).toBe("12 B"); expect(fmtSize(2048)).toBe("2.0 kB"); expect(fmtSize(3 * 1024 * 1024)).toBe("3.0 MB"); });
  it("parses CSV with quoted commas", () => expect(parseCsv('a,b\n1,"two, three"\n')).toEqual([["a", "b"], ["1", "two, three"]]));
  it("caps the rows it parses", () => expect(parseCsv(Array.from({ length: 500 }, (_, i) => `${i},x`).join("\n"), 10)).toHaveLength(11));
});

describe("listing", () => {
  it("shows name, type, size and modified time, with download links", async () => {
    install(live());
    open("/mechanical");
    const row = (await screen.findByText("cad/hull.stl")).closest("tr") as HTMLElement;
    expect(row.textContent).toContain("stl");
    expect(row.textContent).toContain("25 B");
    expect(row.textContent).toContain("2026-11-14 10:00:00 UTC");
    expect(within(row).getByRole("link", { name: /download cad\/hull\.stl/i }).getAttribute("href")).toBe("api/files/mechanical/cad/hull.stl");
    expect(screen.getByRole("link", { name: /download mechanical\.zip/i }).getAttribute("href")).toBe("api/download/mechanical.zip");
    expect(screen.getByRole("link", { name: /download all/i }).getAttribute("href")).toBe("api/download/all.zip");
  });
  it("offers the release only when one exists", async () => {
    install(live());
    open("/mechanical");
    await screen.findByText("cad/hull.stl");
    expect(screen.queryByRole("link", { name: /download release/i })).toBeNull();
    cleanup();
    install(live({ release: { available: true, name: "harbour-listener-abcdef012345.zip", size: 4096 } }));
    open("/mechanical");
    expect((await screen.findByRole("link", { name: /download release/i })).getAttribute("href")).toBe("api/download/release");
  });
  it("says so when a stage has built nothing", async () => {
    install(live());
    open("/firmware");
    expect(await screen.findByText(/Nothing built yet/)).toBeTruthy();
    expect(screen.getByText(/download firmware\.zip/i).closest("a")?.getAttribute("aria-disabled")).toBe("true");
  });
  it("shows an error with retry when the listing fails", async () => {
    install(live({ listStatus: 500 }));
    open("/mechanical");
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
  });
});

describe("preview", () => {
  const preview = async (name: RegExp) => {
    install(live());
    open("/mechanical");
    await screen.findByText("cad/hull.stl");
    fireEvent.click(screen.getByRole("button", { name }));
  };
  it("pretty-prints JSON", async () => {
    await preview(/preview interface\.json/i);
    const pre = await screen.findByLabelText(/contents of interface\.json/i);
    expect(pre.textContent).toContain('"dry_mass_g": 291.2');
  });
  it("shows CSV as a table", async () => {
    await preview(/preview data\.csv/i);
    expect(await screen.findByRole("columnheader", { name: "a" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "two, three" })).toBeTruthy();
  });
  it("shows plain text", async () => {
    await preview(/preview notes\.txt/i);
    expect((await screen.findByLabelText(/contents of notes\.txt/i)).textContent).toBe("hello world");
  });
  it("shows a STEP file as its sibling STL, and says so", async () => {
    await preview(/preview hull\.step/i);
    // hull.step has no hull.stl at the top level (it is under cad/), so there is no mesh to show
    expect(await screen.findByText(/No 3D preview for this STEP file/)).toBeTruthy();
  });
  it("uses the matching STL when there is one", async () => {
    install(live({ list: { stage: "mechanical", files: [entry("hull.stl", "stl", 25), entry("hull.step", "step", 13)] } }));
    open("/mechanical");
    fireEvent.click(await screen.findByRole("button", { name: /preview hull\.step/i }));
    expect(await screen.findByText(/shown as the mesh the build exported/)).toBeTruthy();
  });
  it("offers no preview for binary files", async () => {
    await preview(/preview blob\.bin/i);
    expect(await screen.findByText(/No preview for this file type/)).toBeTruthy();
  });
  it("reports a file that vanished", async () => {
    install((url) => (url === "api/files/mechanical/notes.txt" ? new Response("", { status: 404 }) : live()(url)));
    open("/mechanical");
    await screen.findByText("cad/hull.stl");
    fireEvent.click(screen.getByRole("button", { name: /preview notes\.txt/i }));
    expect(await screen.findByText(/no longer there/)).toBeTruthy();
  });
  it("tells the person to download a very large text file", async () => {
    install(live({ list: { stage: "mechanical", files: [entry("big.log", "text", 50 * 1024 * 1024)] } }));
    open("/mechanical");
    fireEvent.click(await screen.findByRole("button", { name: /preview big\.log/i }));
    expect((await screen.findByRole("alert")).textContent).toContain("download it to read it");
  });
});

describe("sample mode", () => {
  it("lists the sample files and has no downloads to offer", async () => {
    install((url) => json(sample(url.replace(/^sample\//, "").replace(/^files\/.*/, "files.json"))));
    open("/mechanical", "?sample=1");
    await screen.findByText("hull.stl");
    await waitFor(() => expect(screen.getByText(/download all/i).closest("a")?.getAttribute("aria-disabled")).toBe("true"));
    expect(screen.getAllByText(/Sample data/).length).toBeGreaterThan(0);
  });
});
