# AGENTS.md: rules every coding agent reads first

Engenia takes a written product request and produces a verified design: requirements, CAD, electronics, firmware, simulation results and a trace report. When a requirement changes, only the affected parts rebuild. Full plan: `PROJECT.md`. Deep design: `engenia-technical-implementation-plan.md`.

**Never edit files outside your seat's folders. Never edit engenia/contracts/. If you need a contract change, stop and say so.**

---

## 1. The five rules

1. One spec per project is the only source of truth.
2. Agents write code. Scripts decide pass or fail.
3. Design agents run in parallel and coordinate only through the contract board.
4. Verification happens before design, after each stage, at integration and at the end.
5. A rebuild from committed code gives identical files.

---

## 2. Folder ownership

A seat may only create or edit files inside its own folders. This is what makes parallel work safe.

| Folder | Owner seat |
|---|---|
| `engenia/core/` | T1 |
| `engenia/contracts/` (schemas) | T1, changes only with Timothy's approval |
| `engenia/harness/`, `engenia/agents/intake/`, `engenia/agents/architect/`, `engenia/agents/verification/`, `engenia/gate/` | T2 |
| `engenia/agents/mechanical/`, `packs/buoy/templates/`, `engenia/stages/integration/` | T3 |
| `engenia/agents/redteam/`, `engenia/agents/testplan/`, `projects/*/spec/` | T4 |
| `engenia/agents/electronics/`, `engenia/agents/firmware/`, `packs/buoy/parts/` | A1 |
| `verifiers/`, `evals/faults/`, `engenia/evidence/` | A2 |
| `containers/`, `infra/`, `.github/`, `engenia/sim/`, `packs/buoy/sim/` | B1 |
| `tests/<lane>/` | Same seat as the lane |

Seats: T1 Claude A (core), T2 Claude B (harness, agents, gate), T3 Codex (mechanical and integration), T4 OpenCode (independent agents and example specs), A1 Codex (electronics and firmware), A2 OpenCode (checks and evidence), B1 OpenCode (infrastructure, then simulation).

---

## 3. Frozen contracts

Written in `engenia/contracts/models.py`. They change only through Timothy, on `main`.

### Stage names

`verify_plan`, `architect`, `mechanical`, `electronics`, `firmware`, `redteam`, `testplan`, `simprep`, `integration`, `simulation`, `final`, `report`.

### Requirement (`projects/<p>/spec/R-xxx.yaml`)

| Field | Type | Rule |
|---|---|---|
| `id` | string | `R-` plus three digits, matches file name |
| `statement` | string | One obligation, contains "shall" |
| `value` | number or null | Required unless method is `inspection` |
| `unit` | string or null | Required when `value` is set |
| `verify.method` | enum | `inspection`, `analysis`, `simulation`, `test` |
| `verify.check` | string | Name of a file in `verifiers/` |
| `verify.limit` | object | Any of `min`, `max`, `equals` |
| `affects` | list of stage names | At least one |
| `status` | enum | `draft`, `approved` |

### Contract (`projects/<p>/contract/contract.json`)

```json
{
  "version": 1,
  "budgets": {
    "mechanical":  {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
    "electronics": {"mass_g": 60, "bay_mm": [70, 40, 22], "energy_mwh_per_day": 180},
    "firmware":    {"flash_kb": 512, "ram_kb": 64}
  },
  "interfaces": {
    "pin_map": {"HYDROPHONE_ADC": "GPIO34", "LORA_CS": "GPIO5"},
    "mounts":  {"board_holes_mm": [[3, 3], [67, 3], [3, 37], [67, 37]]}
  },
  "totals": {"mass_g_max": 400}
}
```

### Change request (`projects/<p>/contract/change_requests/CR-xxx.json`)

```json
{"id": "CR-001", "from_stage": "electronics", "path": "budgets.electronics.bay_mm",
 "requested": [70, 40, 26], "reason": "battery height 24 mm", "status": "open"}
```

Status values: `open`, `accepted`, `rejected`.

### Interface (`build/<stage>/interface.json`)

```json
{"stage": "mechanical", "key": "sha256:...", "facts": {"dry_mass_g": 318.4}}
```

### Verdict (`build/<stage>/verdict.json`, a list)

```json
{"check": "buoyancy_margin", "requirement": "R-005", "measured": 0.27,
 "limit": {"min": 0.20}, "result": "pass", "evidence_level": "calculated",
 "artifact": "sha256:..."}
```

`result` is `pass`, `fail` or `error`. `evidence_level` is `calculated`, `simulated` or `measured`.

### Stage contract (`engenia/stages/base.py`)

```python
class Stage(Protocol):
    name: str                 # exact stage name from the list above
    needs: list[str]          # upstream stage names
    reads_contract: bool
    checks: list[str]         # verifier names run after build

    def generate(self, ctx: StageContext) -> None: ...   # agent writes src/<stage>/
    def build(self, ctx: StageContext) -> None: ...      # deterministic, no model calls
    def interface(self, ctx: StageContext) -> dict: ...  # facts for later stages
```

### Verifier contract (`verifiers/<name>.py`)

```python
def run(ctx: CheckContext) -> Verdict: ...
```

A verifier reads files only. It never calls a model and never writes outside `build/`.

### Check plan (`projects/<p>/checks/checks.yaml`)

```yaml
- id: C-005
  requirement: R-005
  check: buoyancy_margin
  params: {water_density_kg_m3: 1005}
  limit: {min: 0.20}
```

### CLI commands (exact)

| Command | Does |
|---|---|
| `engenia lint <project>` | Validates and lints the spec |
| `engenia plan <project>` | Shows which stages will run and why |
| `engenia run <project>` | Runs the pipeline with parallel scheduling and cache |
| `engenia gate <project>` | Runs all checks, reproduction and coverage |
| `engenia matrix <project>` | Writes the trace matrix |
| `engenia status <project>` | Shows run state and open change requests |

---

## 4. Finish steps for every task

Branch names: `<seat>-<sprint>-<short-name>`, lower case. Examples: `t1-s1-core`, `a1-s2-firmware`, `b1-s1-infra`.

The agent does steps 1 to 5. The person does step 6.

1. `uv run ruff check .` passes.
2. `uv run pytest tests/<lane> -q` passes.
3. `git status` shows changes only inside the seat's folders.
4. Commit with message `<seat>: <what changed>`.
5. `git push -u origin <branch>` then `gh pr create --fill --base main`.
6. Post the pull request link in the team chat and send it for review.

Pull request description (use `--fill`, then edit):

```
Seat:
Task ID:
What it adds:
How to test: (exact commands)
Acceptance criteria met: (tick list from the task prompt)
Anything not done:
```

Timothy merges a pull request only when CI is green, the review says "approve", the files changed are inside the seat's folders, and all acceptance criteria are ticked. Squash merge. After every merge, each open workspace runs `git fetch origin && git rebase origin/main`.
