// The only module that talks to the network. In sample mode every /api/* path is answered from
// sample/*.json (served next to the page), with the same shapes.

import type {
  ContractData, Pipeline, Requirement, ScenariosData, StageDetail,
} from "./types";
import type { AgentsResponse, RunSummary } from "./mission";

export class ApiError extends Error {
  status: number | null;
  network: boolean;
  constructor(message: string, opts: { status?: number | null; network?: boolean } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status ?? null;
    this.network = opts.network ?? false;
  }
}

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));
const enc = encodeURIComponent;

/** Map an API path to where the data lives: the live server or the bundled sample. */
export function resolve(path: string, sample: boolean): string {
  if (!sample) return path.replace(/^\//, "");
  const rest = path.replace(/^\/api\//, "").replace(/\?.*$/, "");
  return rest.startsWith("files/") ? `sample/${rest}` : `sample/${rest}.json`;
}

async function get(path: string, sample: boolean): Promise<Response> {
  let res: Response | null = null;
  // The backend answers 503 while a build is writing its files; that clears in moments.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      res = await fetch(resolve(path, sample), { cache: "no-store" });
    } catch {
      throw new ApiError("Cannot reach the server.", { network: true });
    }
    if (res.status !== 503) break;
    await wait(400 * (attempt + 1));
  }
  if (!res || !res.ok) {
    const status = res?.status ?? null;
    throw new ApiError(`Server answered ${status} ${res?.statusText ?? ""}`.trim(), { status });
  }
  return res;
}

async function json<T>(path: string, sample: boolean): Promise<T> {
  const res = await get(path, sample);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("The server did not return valid JSON.", { status: res.status });
  }
}

/** Sample stages.json holds one stage or a list; other stages answer with just their status. */
async function sampleStage(name: string): Promise<StageDetail> {
  const sample = await json<StageDetail | StageDetail[]>("/api/stages", true);
  const hit = (Array.isArray(sample) ? sample : [sample]).find((s) => s && s.name === name);
  if (hit) return hit;
  const pipeline = await json<Pipeline>("/api/pipeline", true);
  const stage = pipeline.stages.find((s) => s.name === name);
  if (!stage) throw new ApiError("Unknown stage", { status: 404 });
  return { name, status: stage.status, facts: {}, verdicts: [] };
}

export interface AvailableFault { id: string; description: string; stage: string }

/** POST /api/demo-fault. Resolves to the run id and fault; ApiError carries 409/404/501. */
async function postFault(faultId?: string): Promise<{ run_id: number; fault: AvailableFault }> {
  let res: Response;
  try {
    res = await fetch("api/demo-fault", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(faultId ? { fault_id: faultId } : {}),
    });
  } catch {
    throw new ApiError("Cannot reach the server.", { network: true });
  }
  if (!res.ok) {
    let detail = `Server answered ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep the status text */ }
    throw new ApiError(String(detail), { status: res.status });
  }
  return res.json();
}

export const makeApi = (sample: boolean) => ({
  pipeline: () => json<Pipeline>("/api/pipeline", sample),
  requirements: () => json<{ requirements: Requirement[] }>("/api/requirements", sample),
  stage: (name: string) =>
    sample ? sampleStage(name) : json<StageDetail>(`/api/stages/${enc(name)}`, false),
  contract: () => json<ContractData>("/api/contract", sample),
  scenarios: () => json<ScenariosData>("/api/scenarios", sample),
  agents: () => json<AgentsResponse>("/api/agents", sample),
  runSummary: (runId?: number | string | null) =>
    json<RunSummary>(`/api/run/summary${runId != null && !sample ? `?run_id=${enc(String(runId))}` : ""}`, sample),
  eventsHistory: () => json<{ run_id: number | null; events: unknown[] }>("/api/events/history", sample)
    .then((r) => (Array.isArray(r) ? { run_id: null, events: r } : r)),
  demoFaults: () => json<{ faults: AvailableFault[] }>("/api/demo-fault", sample),
  breakIt: postFault,
  /** URL of a raw build file. `version` busts the cache after the design changes. */
  fileUrl: (stage: string, file: string, version = 0) => {
    const base = resolve(`/api/files/${enc(stage)}/${enc(file)}`, sample);
    return version && !sample ? `${base}?v=${version}` : base;
  },
});
export type Api = ReturnType<typeof makeApi>;

/** Fetch a raw file: the bytes, or "missing" for a 404. Other failures throw. */
export async function fetchFile(url: string): Promise<ArrayBuffer | "missing"> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch {
    throw new ApiError("Cannot reach the server.", { network: true });
  }
  if (res.status === 404) return "missing";
  if (!res.ok) throw new ApiError(`Server answered ${res.status} ${res.statusText}`.trim(), { status: res.status });
  return res.arrayBuffer();
}

/** Is the live API reachable? Used once at start to choose live or sample mode. */
export async function apiReachable(): Promise<boolean> {
  try {
    await get("/api/pipeline", false);
    return true;
  } catch (error) {
    // A server that answers with an error is still a server; only silence means "no API".
    return error instanceof ApiError && !error.network;
  }
}
