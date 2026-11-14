# Mission Control API: what the UI needs

Written by B1 (dashboard-ui) for t1-mission-events. Every field below is read by the Mission
Control view. Nothing is simulated: if the backend has no value, send `null` or an empty list and
the UI shows "not recorded yet". Existing routes (`/api/events`, `/api/events/history`,
`/api/pipeline`, ...) keep their shapes.

All routes accept `?project=` (default: the dashboard's project). Run-scoped routes accept
`?run_id=` (default: the project's newest run). Timestamps are UTC ISO 8601. Durations are
milliseconds.

## 1. Events (`/api/events` live, `/api/events/history` replay)

Same envelope as today: `{ts, run_id, project, stage, attempt, kind, data}`. The existing eight
kinds (`message|tool_call|tool_result|code|decision|preview|verdict|repair`) are unchanged. New
kinds:

| kind | stage | data |
|---|---|---|
| `phase_start` | the stage | `{phase}` |
| `phase_end` | the stage | `{phase, duration_ms, result}` |
| `cache` | the stage | `{hit: bool, key: "sha256:..."}` |
| `gate_step` | `final` | `{step, title, result, detail, duration_ms}` |
| `approval` | `verify_plan` | `{what, hash, approved: bool, approved_by: str\|null}` |
| `change_request` | requesting stage | `{id, path, requested, status, reason}` |
| `proposal` | requesting stage | `{requirement, old_limit, new_limit, reason, status}` |
| `fault` | the stage hit | `{id, description, injected_into}` |

- `phase` is one of `plan, propose, generate, build, check, repair, publish, cache`.
  `phase_end.result` is `ok | fail | skipped`. `phase_start` and `phase_end` come in pairs; a
  stage restarted by the repair loop emits a new pair with a higher `attempt`.
- `cache`: `hit: true` means the stage was restored and no phase ran.
- `gate_step.step` is `G1` to `G5` or `audit`; `result` is `running | pass | fail`; `detail` is one
  human-readable line (for example `"rebuilt electronics: byte-identical"`).
- `approval.what` is `spec | checks | release`; `hash` is the hash that was approved.
- `change_request.status` is `open | accepted | rejected`; one event per status change; `reason`
  explains an accept or reject (it may be the request's own reason while `open`).
- `proposal.status` is `draft | approved | rejected`.
- `fault`: emitted once when `demo-fault` injects a fault; the failing `verdict`, the `repair`
  events and the gate then follow as ordinary events.
- `verdict` data is the existing verdict plus nothing new (`check, requirement, measured, limit,
  result, evidence_level, message`). `repair.data` carries `summary` and, if known, `check`.

## 2. `GET /api/agents`

```json
{
  "run_id": 7,
  "agents": [
    {
      "id": "mechanical",
      "name": "Mechanical agent",
      "role": "Writes the CAD code from the contract budgets",
      "kind": "llm",
      "model": "claude-sonnet-5-5",
      "family": "anthropic",
      "side": "design",
      "status": "active",
      "stage": "mechanical",
      "phase": "generate",
      "calls": 4,
      "tool_calls": 11,
      "repairs": 1
    }
  ],
  "families": { "design": ["anthropic"], "verify": ["openai"], "independent": true }
}
```

- `kind`: `llm | deterministic | script`. For non-LLM agents `model` and `family` are `null`.
- `side`: `design | verify | null`. `families.independent` is true when no family appears in both
  lists (the family guard's verdict). Send the guard's own answer, not one the UI could recompute.
- `status`: `idle | active | done | failed`. `stage` and `phase` are the current ones, else `null`.
- `calls`, `tool_calls`, `repairs` are counts for the run.
- Every agent in the pipeline appears, including idle ones, so the panel can show "N active now".

## 3. `GET /api/run/summary`

```json
{
  "run_id": 7, "project": "harbour-listener", "status": "running",
  "started": "2026-11-14T10:00:00Z", "finished": null,
  "stages": [
    {
      "name": "mechanical", "status": "done", "attempts": 2,
      "started": "...", "finished": "...", "duration_ms": 41200, "cache_hit": false,
      "phases": [
        {"phase": "generate", "attempt": 1, "start": "...", "end": "...",
         "duration_ms": 9100, "result": "ok"}
      ],
      "checks": [{"check": "mass_budget", "requirement": "R-003", "result": "pass"}]
    }
  ],
  "before": {
    "verify_plan": {"model": "gpt-x", "family": "openai", "result": "pass",
                    "requirements": 12, "checks": 14},
    "approval": {"hash": "sha256:...", "approved": true, "approved_by": "timothy",
                 "approved_at": "..."}
  },
  "during": {"checks_total": 24, "passed": 22, "failed": 2},
  "after": {
    "gate": [{"step": "G1", "title": "All checks rerun", "result": "pass", "detail": "..."}],
    "audit": {"result": "pass", "detail": "every requirement has a result"}
  },
  "loops": {
    "repair": {"attempts": 3, "fixed": 2},
    "change_requests": {"open": 0, "accepted": 1, "rejected": 1},
    "proposals": {"draft": 1},
    "calibration": {"measurements": 0, "applied": 0}
  }
}
```

- `stages[].phases` and `checks` may be empty (and `cache_hit` false) for stages that have not
  started. `stages[].status` uses the run-state values (`pending|running|done|cached|failed|blocked`).
- Any of `before.verify_plan`, `before.approval`, `after.gate`, `after.audit` may be `null` / `[]`
  when not recorded yet.
- `during.checks_total/passed/failed` count scripted verdicts for the run (latest per check).
- `loops` are the four feedback loops: counts only; the events carry the detail.

## 4. Demo fault

- `GET /api/demo-fault` -> `{"faults": [{"id": "F01-wall-1mm", "description": "...",
  "stage": "mechanical"}]}` (the faults available to inject; `[]` if the server cannot inject).
- `POST /api/demo-fault` with `{"fault_id": "F01-wall-1mm"}` (optional; default: the first one)
  -> `202 {"run_id": 8, "fault": {...as listed}}`. The fault is injected into a copy, never into
  the user's project files. Then the run proceeds and emits events: `fault`, failing `verdict`,
  `repair`, passing `verdict`, `gate_step`.
- `409 {"detail": "..."}` if a run is already in progress; `404` if the fault id is unknown;
  `501` if the server cannot inject faults.
- This is the only write route the dashboard has. The UI calls it only when the person presses
  "Break it".
