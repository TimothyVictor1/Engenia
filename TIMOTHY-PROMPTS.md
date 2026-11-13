# Engenia: Timothy's Prompts

**How to use this file**

1. Do the tasks in the order they appear. One task = one Superset workspace = one branch.
2. In Superset press Cmd+N and name the workspace exactly as the branch shown under the task title.
3. Wait for "Workspace ready", start the agent named for that task, and paste the whole grey block.
4. When the agent says it is finished, tick the acceptance list yourself.
5. Post the pull request link in the team chat and ask for review: `Review PR #<number>, task <task ID>`.
6. If the review says "changes needed", use the fix prompt at the bottom of this file.

Every prompt assumes the agent has read `AGENTS.md`, which Step 0 creates.

---

# Step 0 (17:45 to 18:30)

## Step 0: contract freeze

**Seat T1 (Claude A). Branch `t1-s0-contracts`. Timothy supervises, P1 and P2 review. 17:45 to 18:30.**

```
You are setting up a new Python repository called engenia. Read PROJECT.md fully
before writing anything. PROJECT.md sections 4, 5 and 6 are the specification
for this task. Follow names, fields and paths exactly.

Create:

1. pyproject.toml for Python 3.12 managed by uv. Dependencies: pydantic>=2,
   typer, pyyaml, pytest, ruff, httpx. Console script: engenia = engenia.core.cli:app.

2. engenia/contracts/models.py with Pydantic v2 models: Requirement, VerifySpec,
   Limit, Contract, ChangeRequest, Interface, Verdict, CheckPlanItem,
   StageContext, CheckContext. Fields and enums exactly as PROJECT.md section 6.
   Reject unknown fields.

3. engenia/stages/base.py with the Stage Protocol from section 6, and
   engenia/stages/registry.py that maps stage name to a Stage instance.

4. A stub Stage for every stage name in PROJECT.md section 2: verify_plan,
   architect, mechanical, electronics, firmware, redteam, testplan, simprep,
   integration, simulation, final, report. Each stub:
   - has the correct needs list from the section 2 diagram,
   - generate() writes one placeholder file in projects/<p>/src/<stage>/,
   - build() copies it to projects/<p>/build/<stage>/,
   - interface() returns {"stub": true},
   - checks is an empty list.
   Put each stub in the folder its future owner owns (see section 3).

5. engenia/core/cli.py with Typer commands lint, plan, run, gate, matrix, status.
   For now: lint validates spec files against Requirement; run executes all
   stubs in dependency order, serially; the others print "not implemented"
   and exit 0.

6. projects/harbour-listener/spec/ with three valid requirements:
   R-001 total mass max 400 g (affects mechanical, electronics),
   R-003 endurance min 7 days (affects electronics, mechanical),
   R-005 buoyancy margin min 0.20 (affects mechanical).

7. .superset/config.json, .superset/setup.sh, .superset/teardown.sh exactly as
   PROJECT.md section 7.3. Make the scripts executable.

8. .github/workflows/ci.yml running on ubuntu-latest for now:
   uv sync, ruff check, pytest -q, then `uv run engenia run harbour-listener`.

9. AGENTS.md containing: the five rules (section 1), folder ownership table
   (section 3), frozen contracts (section 6), finish steps (section 8), and
   this sentence: "Never edit files outside your seat's folders. Never edit
   engenia/contracts/. If you need a contract change, stop and say so."
   Copy AGENTS.md to CLAUDE.md.

10. .gitignore: .env, .venv, .engenia/, projects/*/build/, __pycache__.
    .env.example with the variables from section 4.

11. tests/smoke/test_smoke.py: models import, three specs validate, registry
    has all twelve stages, `engenia run harbour-listener` exits 0.

Acceptance:
- uv sync works from a clean clone.
- uv run ruff check . passes.
- uv run pytest -q passes.
- uv run engenia run harbour-listener exits 0 and creates build/<stage>/ for all
  twelve stages.

Do not implement real stage logic. Do not add dependencies beyond the list.
Finish: commit, push, open a pull request to main.
```

---

# Sprint 1 (18:30 to 21:00): start all four, one workspace each

## T1-S1: core spec, graph, hashing, cache

**Seat T1 (Claude A). Branch `t1-s1-core`.**

```
Read AGENTS.md. You own engenia/core/ and tests/core/. Touch nothing else.

Build the core of the pipeline.

1. engenia/core/spec.py: load all projects/<p>/spec/R-*.yaml into Requirement
   objects. Error clearly on duplicate ids or id not matching file name.

2. engenia/core/lint.py: lint rules, each with a rule id and message:
   L1 statement contains exactly one "shall";
   L2 no vague words: robust, fast, sufficient, adequate, easy, user-friendly,
      appropriate, as needed;
   L3 value and unit present unless method is inspection;
   L4 limit has at least one of min, max, equals;
   L5 affects names only real stage names;
   L6 verify.check names an existing file in verifiers/ (warning, not error,
      until sprint 2).
   `engenia lint <p>` prints findings and exits 1 on any error.

3. engenia/core/graph.py: build the stage DAG from the registry. Functions:
   topo_order(), downstream(stage), requirements_for(stage).
   Detect cycles and unknown needs.

4. engenia/core/hashing.py: stage_key(stage, ctx) = sha256 over, in fixed order:
   sorted bytes of the requirement files affecting the stage; upstream
   interface.json files; contract.json if reads_contract; all files in
   src/<stage>/; the stage class source; container digest string from
   containers/<family>/DIGEST if present. Use canonical JSON (sorted keys,
   no whitespace). Also file_hash(path) and tree_hash(dir).

5. engenia/core/cache.py: content-addressed store under $ENGENIA_STORE.
   put(key, build_dir) and get(key, build_dir). A hit restores outputs,
   interface.json and verdict.json exactly.

6. Update `engenia run` to: compute keys, use the cache, run generate() only when
   src/<stage>/ is empty or its inputs changed, always run build() on a miss,
   write interface.json, and stop propagation when a rerun stage produces an
   identical interface.json. Still serial in this task.

Acceptance (write these as tests in tests/core/):
- Running twice in a row: second run executes zero stages.
- Editing R-003 reruns electronics and mechanical and their downstream, and
  does NOT rerun firmware unless an upstream interface changed.
- Editing a comment-free whitespace difference in a spec file still changes
  the key (we hash bytes) - document this in a docstring.
- A stage rerun with unchanged interface.json does not rerun its downstream.
- stage_key is identical across two fresh clones for the same inputs.
- Lint: one failing example per rule L1 to L5.

Do not add parallelism yet. Do not call any model.
Finish steps from AGENTS.md.
```

---

## T2-S1: agent harness and intake agent

**Seat T2 (Claude B). Branch `t2-s1-harness`.**

```
Read AGENTS.md. You own engenia/harness/, engenia/agents/intake/, tests/harness/.

1. engenia/harness/llm.py: a small OpenAI-compatible chat client using httpx.
   Reads ENGENIA_LLM_BASE_URL and ENGENIA_LLM_API_KEY. Function
   chat(model, messages, tools=None, temperature=0) -> response.
   Retries on 429 and 5xx with backoff, max 4 tries. A FakeLLM class with
   scripted responses for tests.

2. engenia/harness/tools.py: exactly three tools, each sandboxed to an allowed
   directory passed in at construction:
   write_file(path, content), run_build() -> stdout/stderr/exit code of the
   stage build, read_verdicts() -> list of Verdict dicts.
   Any path outside the allowed directory raises an error returned to the
   model as a tool result.

3. engenia/harness/limits.py: per-run limits: max attempts (ENGENIA_MAX_ATTEMPTS),
   max tool calls per attempt (40), max wall time per attempt (600 s).

4. engenia/harness/loop.py: run_agent(system_prompt, task, tools, model, limits)
   -> AgentResult(status, attempts, transcript_path). Loop: model call, execute
   tool calls, feed results back, stop when the model returns a final message
   or a limit is hit. Save the full transcript as JSON under
   projects/<p>/build/<stage>/transcripts/.

5. engenia/harness/design_loop.py: generate_until_pass(stage, ctx): calls
   run_agent, then build, then the stage's verifiers; on fail, sends the
   failed Verdict objects (check, measured, limit) back as the next user
   message; stops at pass or max attempts. Returns status pass | exhausted.
   The pass decision comes only from Verdict.result values.

6. engenia/agents/intake/agent.py: intake(text, project) writes draft
   Requirement YAML files (status: draft) and returns the list. It must run
   engenia lint on its own output and fix lint errors, max 3 rounds. It never
   sets status approved.

Acceptance (tests in tests/harness/, all using FakeLLM, no network):
- A write_file outside the allowed directory is refused.
- Attempt limit and tool-call limit both stop the loop.
- generate_until_pass returns pass when verdicts pass on attempt 2, and
  exhausted after 3 failing attempts; the failing numbers appear in the
  message sent to the model.
- Transcript file is written for every attempt.
- Intake output validates against Requirement and has status draft.

Do not put any pass or fail logic in prompts or model output parsing.
Finish steps from AGENTS.md.
```

---

## T3-S1: mechanical stage

**Seat T3 (Codex). Branch `t3-s1-mechanical`.**

```
Read AGENTS.md. You own engenia/agents/mechanical/, packs/buoy/templates/,
tests/mechanical/. Add build123d to pyproject.toml dependencies (this one
shared-file edit is allowed).

Replace the mechanical stub with a real stage.

1. packs/buoy/templates/hull.py: a parametric build123d model of a small
   cylindrical buoy: outer diameter, height, wall thickness, lid with an
   O-ring groove, internal battery bay (x, y, z), four board standoffs at
   given hole positions. One function build_hull(params: HullParams) returning
   the hull and lid solids. All dimensions in mm.

2. engenia/agents/mechanical/stage.py:
   - generate(): writes projects/<p>/src/mechanical/params.yaml and model.py.
     model.py imports the template and calls it with params. For this sprint,
     generate() derives params directly from contract.json budgets (bay size,
     envelope, mount holes) without a model call, behind a function
     propose_params(contract, requirements) that T2's harness can replace
     later.
   - build(): runs model.py, exports hull.step, lid.step, hull.stl, lid.stl
     into build/mechanical/. Must be deterministic: fixed tolerance, no
     timestamps in files where avoidable.
   - interface(): facts: dry_mass_g (volume x material density from
     packs/buoy/pack.yaml, PETG 1.27 g/cm3, with an infill factor),
     displaced_volume_cm3 (outer closed volume), internal_volume_cm3,
     battery_bay_mm, wall_min_mm, bbox_mm, centre_of_mass_mm.
   - checks: ["mass_budget", "buoyancy_margin", "print_bed_fit",
     "wall_thickness_min"] (A2 writes these verifiers; until merged, skip a
     missing verifier with a warning).

3. If contract.json does not exist yet, read a default from
   packs/buoy/default_contract.json (create it, matching PROJECT.md section 6).

Acceptance (tests in tests/mechanical/):
- build() produces four files and a valid interface.json.
- Two builds from the same src give identical STEP and STL hashes. If the
  kernel writes a timestamp header, normalise it and document how.
- Changing bay_mm height from 22 to 26 increases internal volume and changes
  the interface.
- bbox fits a 220 x 220 x 250 mm print bed with default params.
- wall_min_mm in the interface equals the params value.

Do not write verifier scripts. Do not edit engenia/core/.
Finish steps from AGENTS.md.
```

---

## T4-S1: example spec and red-team agent

**Seat T4 (OpenCode). Branch `t4-s1-spec-redteam`.**

```
Read AGENTS.md. You own projects/*/spec/, engenia/agents/redteam/, tests/redteam/.

1. Expand projects/harbour-listener/spec/ to twelve approved requirements.
   Keep R-001, R-003, R-005. Add, one obligation each, all passing
   `engenia lint`:
   R-002 fits print bed 220 x 220 x 250 mm (mechanical)
   R-004 minimum wall thickness 3 mm (mechanical)
   R-006 floats upright: metacentric height min 5 mm (mechanical)
   R-007 average power max 25 mW (electronics)
   R-008 battery capacity min as required by R-003 and R-007 (electronics)
   R-009 detects a 200 Hz tone at stated level in recorded audio, detection
         rate min 0.9 (firmware)
   R-010 false alarms max 2 per hour on quiet recording (firmware)
   R-011 max tilt 30 degrees in 0.5 m significant wave height (simulation)
   R-012 hull stress safety factor min 2 at 3 m depth (simulation)
   Use verify.check names: print_bed_fit, wall_thickness_min,
   metacentric_height, power_budget, battery_capacity, detection_rate,
   false_alarm_rate, wave_tilt, stress_safety_factor.

2. engenia/agents/redteam/stage.py replacing the stub: generate() produces
   scenario files in projects/<p>/src/redteam/scenarios/*.yaml. Schema per
   file: name, wave_height_m, wave_period_s, water_temp_c, water_density,
   depth_m, extra (free dict), rationale, targets (list of requirement ids).
   For this sprint produce four fixed scenarios without a model call
   (calm_harbour, baltic_winter, storm, flooded_bay) behind a function
   propose_scenarios() the harness can replace later. build() validates and
   copies them. interface() lists scenario names.

Acceptance (tests in tests/redteam/):
- engenia lint harbour-listener exits 0 with twelve requirements.
- Every scenario file validates and names at least one real requirement id.
- A scenario targeting an unknown requirement id fails build().

Do not write verifiers. Finish steps from AGENTS.md.
```

---

# Sprint 2 (21:30 to 23:30)

## T1-S2: parallel scheduler and contract board

**Seat T1 (Claude A). Branch `t1-s2-parallel`.**

```
Read AGENTS.md. You own engenia/core/ and tests/core/.

1. engenia/core/scheduler.py: run the DAG in parallel with a worker pool
   (concurrent.futures, default 4 workers). A stage starts as soon as all its
   needs are done. Rules:
   - A stage that fails or exhausts attempts is marked failed. Stages that do
     not depend on it keep running and are cached when they finish.
   - Stages downstream of a failed stage are marked blocked, not run.
   - If lint fails or the contract totals cannot be satisfied, stop everything
     (status halted). This is the only global stop.
   - Each stage writes only inside its own src/ and build/ folders, so no
     locks are needed there. Guard the cache and run state with a lock.

2. engenia/core/contract_board.py:
   - load/save contract.json with a version number.
   - list open ChangeRequest files.
   - resolve(cr): apply the requested value to a copy, then check totals by
     script: sum of mass budgets <= totals.mass_g_max; electronics bay fits
     inside the mechanical envelope with wall allowance. If totals hold,
     accept: bump version, write contract.json, mark the CR accepted. If not,
     mark it needs_architect and leave the contract unchanged.
   - affected_stages(path): map a contract path to the stages that read it
     (budgets.<stage>.* affects that stage; interfaces.pin_map affects
     electronics and firmware; interfaces.mounts affects mechanical and
     electronics; budgets.electronics.bay_mm also affects mechanical).

3. Wire into `engenia run`: after the parallel design stages finish, resolve
   open change requests; if any were accepted, rerun only affected_stages()
   and their downstream; repeat at most 3 rounds, then halt for a person.

4. engenia/core/runstate.py: SQLite at .engenia/run.db with tables run and
   stage_run (run id, stage, key, status, attempts, started, finished).
   `engenia status <p>` prints the latest run as a table plus open CRs.

Acceptance (tests in tests/core/ with fake stages):
- Three independent stages with a 1 s sleep finish in under 2 s total.
- A forced failure in one of three parallel stages: the other two are
  status done and present in the cache; downstream is blocked.
- An accepted CR on budgets.electronics.bay_mm reruns electronics and
  mechanical, not firmware.
- A CR that breaks the mass total is not accepted and the contract file is
  byte-identical afterwards.
- More than 3 rounds halts with a clear message.

Finish steps from AGENTS.md.
```

---

## T2-S2: architect agent, verification agent, gate

**Seat T2 (Claude B). Branch `t2-s2-verify-gate`.**

```
Read AGENTS.md. You own engenia/agents/architect/, engenia/agents/verification/,
engenia/gate/, tests/gate/, tests/agents/.

1. engenia/agents/architect/stage.py replacing the stub. generate() uses the
   harness with ENGENIA_DESIGN_MODEL to propose contract.json from the
   requirements and packs/buoy/pack.yaml. build() validates it against the
   Contract model and checks by script that budgets add up within totals;
   on failure the measured sums go back to the agent (max 3 attempts). If
   the model is unavailable, fall back to packs/buoy/default_contract.json
   and record that in the interface. The stage also handles CRs marked
   needs_architect by proposing a new split.

2. engenia/agents/verification/stage.py replacing the verify_plan stub.
   generate() uses the harness with ENGENIA_VERIFY_MODEL. Its input is ONLY the
   requirement files and the list of available verifier names with their
   docstrings. It must never be given src/ or build/ content: enforce this by
   constructing its tool sandbox on the checks/ folder only, and add a test.
   Output: projects/<p>/checks/checks.yaml (CheckPlanItem list).
   build() validates by script:
   - every item cites an existing requirement id,
   - every item names an existing verifier,
   - every approved requirement has at least one item,
   - limits equal the requirement's limit (the agent may add params, not
     change limits).
   Then write checks/checks.sha256. Any later change to checks.yaml without
   updating an approval file checks/APPROVED (containing the hash) fails the
   gate.

3. engenia/gate/gate.py, `engenia gate <p>`:
   G1 run every item in checks.yaml against the right stage and collect
      Verdicts;
   G2 tool checks reported by stages (erc_clean and similar) are present;
   G3 reproduction: rebuild every stage from src/ into a temp dir and compare
      output hashes with build/;
   G4 coverage: every approved requirement has at least one Verdict with
      result pass or fail (not error, not missing);
   G5 canary: run each verifier once on a fixture known to fail and confirm
      it returns fail.
   Exit 0 only if G1 all pass and G2 to G5 hold. Write build/gate/report.json.

Acceptance (tests, FakeLLM only):
- The verification agent's sandbox cannot read src/ or build/.
- A check citing R-999 is rejected by build().
- A check with a limit different from its requirement is rejected.
- Editing checks.yaml after approval fails the gate with a clear message.
- G3 fails when a build output is altered by hand.
- G4 fails when one requirement has no verdict.
- G5 fails when a verifier is replaced by one that always returns pass.

No pass or fail decision may come from a model. Finish steps from AGENTS.md.
```

---

## T3-S2: integration stage

**Seat T3 (Codex). Branch `t3-s2-integration`.**

```
Read AGENTS.md. You own engenia/stages/integration/ and tests/integration/.

Replace the integration stub. This stage has no agent. generate() does
nothing. build() reads the mechanical, electronics and firmware interface
files plus contract.json and writes build/integration/assembly.json and
Verdicts for these cross-discipline checks, implemented here as plain
functions and exposed as verifiers through verifiers' registry interface
(ask A2 for the registration call; do not edit verifiers/ yourself, register
from your own package):

- int_board_fits_bay: electronics board_mm and battery fit battery_bay_mm
  with 1 mm clearance per side.
- int_mounts_match: board hole positions equal contract interfaces.mounts.
- int_mass_total: dry_mass_g + electronics mass_g <= totals.mass_g_max.
- int_pins_match: firmware pins_used equals electronics pins_used equals
  contract pin_map.
- int_power_consistent: firmware duty cycle equals the duty cycle electronics
  used for avg_power_mw (tolerance 1 percent).
- int_contract_version: all three interfaces were built against the same
  contract version.

On any fail, write a ChangeRequest or a mismatch record naming the stages
involved, so the scheduler reruns only those.

interface(): total_mass_g, centre_of_mass_mm of the assembly (hull plus a
point mass for electronics at the bay centre), displaced_volume_cm3.

Acceptance (tests with fixture interface files):
- One pass and one fail case per check.
- A contract version mismatch names exactly the stale stage.
- Mismatch records list only the stages involved.

Finish steps from AGENTS.md.
```

---

## T4-S2: test-plan agent

**Seat T4 (OpenCode). Branch `t4-s2-testplan`.**

```
Read AGENTS.md. You own engenia/agents/testplan/ and tests/testplan/.

Replace the testplan stub. Input: requirement files only. Output:
projects/<p>/src/testplan/test_plan.md and test_plan.yaml.

For each requirement write one physical test: id (T-xxx), requirement id,
equipment, numbered steps, what to record with units, pass limit copied from
the requirement, and the evidence it would produce. This sprint: generate
from templates keyed by verify.check (mass on a scale, float test for
buoyancy and waterline, bucket leak test, battery run-down, tone playback
for detection) behind a function propose_tests() the harness can replace.

build() validates: every approved requirement has a test, limits match the
requirement exactly, no test claims a result. Also write
build/testplan/measurements_template.csv with columns requirement, test,
quantity, unit, measured, date, operator - empty rows for people to fill in.

interface(): number of tests, list of requirement ids covered.

Acceptance:
- Twelve requirements give twelve tests.
- A test whose limit differs from its requirement fails build().
- The template CSV has one row per test and no measured values.

Finish steps from AGENTS.md.
```

---

# Sprint 3 (00:30 to 03:00)

## T1-S3: resumable runs, plan and status

**Seat T1 (Claude A). Branch `t1-s3-resume`.**

```
Read AGENTS.md. You own engenia/core/ and tests/core/.

1. Resumable runs: every stage transition is written to .engenia/run.db before
   and after it happens. `engenia run <p> --resume` continues the latest
   unfinished run: stages marked done with a valid cache entry are not
   repeated; a stage marked running is treated as not started. Stage runs
   are idempotent by (run id, stage, key).

2. `engenia plan <p>`: prints, without running anything, each stage as
   reuse | run | blocked, with the reason (which requirement, interface or
   contract path changed).

3. `engenia run <p> --only <stage>` and `--force <stage>` for debugging.

4. A manifest: after a run, write build/manifest.json listing every output
   file with its hash, plus stage keys, contract version, checks hash and
   model names used. CI's reproduce job compares this file.

Acceptance:
- Kill the process during a 3-stage parallel run (test with a subprocess and
  SIGKILL); --resume finishes with no stage executed twice beyond the one
  that was in flight.
- engenia plan after editing R-003 shows electronics and mechanical as run with
  "R-003 changed" and firmware as reuse.
- Two full runs from clean clones give byte-identical manifest.json apart
  from a run id field, which is excluded from comparison.

Finish steps from AGENTS.md.
```

---

## T2-S3: final audit and model-family guard

**Seat T2 (Claude B). Branch `t2-s3-audit`.**

```
Read AGENTS.md. You own engenia/gate/, engenia/agents/verification/, engenia/harness/.

1. Replace the `final` stub (engenia/gate/audit.py as a Stage): runs the full
   gate on the integrated design, then calls the verification agent in audit
   mode. Audit mode input: requirement files, checks.yaml and the Verdict
   list only. Output: build/final/audit.md listing requirements with no
   check, checks with result error, requirements verified only at
   "calculated" level whose verify.method asked for simulation or test, and
   open assumptions. The audit text is advisory. The pass decision of the
   final stage is the gate exit code plus two scripted conditions: zero
   requirements without a verdict, zero verdicts with result error.

2. Model-family guard in engenia/harness/llm.py: at start-up compare
   ENGENIA_DESIGN_MODEL and ENGENIA_VERIFY_MODEL. If they are equal, or share a
   family prefix from a small table (for example both start with the same
   vendor name), refuse to run unless ENGENIA_ALLOW_SAME_FAMILY=1, and record
   the override in the manifest.

3. Replace the deterministic propose_* functions with harness-driven
   versions where the owning seat has exposed them: call the model to
   propose, keep the deterministic function as the fallback when the model is
   unavailable or attempts are exhausted, and record which path was used in
   the stage interface. Do this for mechanical params first, then electronics
   selection. Do not change those lanes' files: wrap them from engenia/harness/
   through a registered proposer hook, and ask Timothy if a hook is missing.

Acceptance:
- The final stage fails when one requirement has no verdict even if every
  existing verdict passes.
- The audit never changes a Verdict.
- Same model for design and verification stops the run with a clear message.
- With FakeLLM proposing a bad hull, the design loop receives the failed
  numbers and passes on a later attempt; with FakeLLM always bad, the
  deterministic fallback is used and recorded.

Finish steps from AGENTS.md.
```

---

## T3-S3: second pack variant

**Seat T3 (Codex). Branch `t3-s3-variant`.**

```
Read AGENTS.md. You own engenia/agents/mechanical/, packs/buoy/templates/.

Prove the mechanical stage is not tied to one shape.

1. Add packs/buoy/templates/mast_housing.py: a weatherproof box-shaped
   sensor housing with a gasketed lid, four mounting ears and a cable gland
   boss. Same HullParams-style interface and the same interface facts where
   they make sense (dry_mass_g, bbox_mm, wall_min_mm, internal_volume_cm3,
   battery_bay_mm). Facts that do not apply (displaced volume) are omitted,
   and the stage must not list buoyancy checks for this template.

2. packs/buoy/pack.yaml gains a `templates` map; the mechanical stage picks
   the template from a `template` field in contract.json (default hull) and
   chooses its checks list from pack.yaml per template.

Acceptance:
- Building with template mast_housing produces STEP and STL and an interface
  without displaced_volume_cm3.
- The checks list for mast_housing excludes buoyancy_margin and
  metacentric_height.
- Existing hull tests still pass unchanged.

Finish steps from AGENTS.md.
```

---

## T4-S3: second project spec

**Seat T4 (OpenCode). Branch `t4-s3-second-project`.**

```
Read AGENTS.md. You own projects/*/spec/.

Create projects/deck-sensor/spec/ with eight approved requirements for a
deck-mounted weather sensor housing that uses the mast_housing template:
total mass, print bed fit, minimum wall, average power, endurance, firmware
unit tests pass, board fits bay, pins match. Use only verifier names that
exist in verifiers/. No buoyancy or wave requirements.

Add projects/deck-sensor/contract/contract.json by hand with
"template": "mast_housing" and sensible budgets.

Acceptance:
- engenia lint deck-sensor exits 0.
- engenia run deck-sensor and engenia gate deck-sensor exit 0 on the cloud runner.
- No file outside projects/deck-sensor/ is changed.

If a run fails because of another lane's code, do not patch that code. Write
the failure and the exact command in the pull request and tell Timothy.
Finish steps from AGENTS.md.
```

---

# Fix prompt (use after any review that says "changes needed")

```
Read AGENTS.md. You are on branch <branch> for task <task ID>.
A review found the problems below. Fix each one, add or change a test that
proves the fix, and change nothing else. Stay inside your folders.

<paste the numbered review list here>

Finish steps from AGENTS.md. In the pull request, reply to each numbered
item with the commit that fixes it.
```
