// Shapes of what the FastAPI backend returns. The UI only displays these; it never decides
// pass or fail. Fields added by newer backends are optional so older servers still work.

export type StageStatus =
  | "done" | "failed" | "not_run" | "stub" | "running" | "blocked" | "pending" | "cached";
export type Result = "pass" | "fail" | "error";
export type Evidence = "calculated" | "simulated" | "measured";

export interface PipelineStage {
  name: string;
  needs: string[];
  status: StageStatus;
  key: string | null;
  attempts?: number | null;
  duration_s?: number | null;
  message?: string | null;
}
export interface Pipeline {
  project: string;
  updated: string;
  run?: { id: number | string; status: string | null; started: string | null } | null;
  stages: PipelineStage[];
}

export interface Limit { min: number | null; max: number | null; equals: number | null }
export interface RequirementVerdict {
  check: string; measured: number | null; result: Result;
  evidence_level: Evidence; message: string | null; stage: string;
}
export interface Requirement {
  id: string; statement: string; unit: string | null; limit: Limit; affects: string[];
  method: string; status: Result | "unverified"; verdict: RequirementVerdict | null;
}

export interface Verdict {
  check: string; requirement: string | null; measured: number | null;
  limit: Partial<Limit>; result: Result; evidence_level: Evidence;
  artifact?: string | null; message: string | null;
}
export interface StageDetail {
  name: string; status: StageStatus; facts: Record<string, unknown>; verdicts: Verdict[];
}

export interface ChangeRequest {
  id: string; from_stage: string; path: string; requested: unknown; reason: string;
  status: "open" | "accepted" | "rejected";
}
export interface ContractData {
  source: "project" | "default" | "none";
  contract: {
    version?: number;
    budgets?: Record<string, Record<string, unknown>>;
    interfaces?: Record<string, Record<string, unknown>>;
    totals?: Record<string, unknown>;
  } | null;
  facts: Record<string, Record<string, unknown>>;
  change_requests: ChangeRequest[];
  decisions?: { id: string; status: string; reason: string; contract_version: number }[];
  mismatches?: { check: string; stages: string[]; message: string; source: string }[];
}

export interface Scenario {
  name: string; wave_height_m?: number | null; wave_period_s?: number | null;
  depth_m?: number | null; water_density?: number | null; water_temp_c?: number | null;
  extra?: Record<string, unknown>; rationale?: string; targets?: string[];
}
export interface ScenariosData { scenarios: Scenario[] }

export type Area = "pipeline" | "requirements" | "contract" | "scenarios" | "design";

export interface FileEntry {
  path: string; name: string; size: number; modified: string;
  type: "text" | "json" | "csv" | "stl" | "step" | "image" | "archive" | "pdf" | "binary";
}
export interface FilesList { stage: string; files: FileEntry[] }
