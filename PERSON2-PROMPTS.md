# Engenia: Person 2's Prompts

**How to use this file**

1. Do the tasks in the order they appear. One task = one Superset workspace = one branch.
2. In Superset press Cmd+N and name the workspace exactly as the branch shown under the task title.
3. Wait for "Workspace ready", start the agent named for that task, and paste the whole grey block.
4. When the agent says it is finished, tick the acceptance list yourself.
5. Post the pull request link in the team chat and ask for review: `Review PR #<number>, task <task ID>`.
6. If the review says "changes needed", use the fix prompt at the bottom of this file.

Every prompt assumes the agent has read `AGENTS.md`, which Step 0 creates.

---

# Sprint 1 (18:30 to 21:00)

## B1-S1: cloud machine, containers, CI

**Seat B1 (OpenCode, P2). Branch `b1-s1-infra`.**

```
Read AGENTS.md. You own containers/, infra/, .github/, tests/infra/.

1. infra/vm_bootstrap.sh for Ubuntu 24.04: installs Docker, git, uv; creates
   user `engenia`; prints the commands to register a GitHub Actions self-hosted
   runner with label engenia-build. Idempotent: safe to run twice.

2. infra/runner_setup.md: exact numbered steps for P2 to create the machine,
   run the script, register the runner, add the repository secrets
   ENGENIA_LLM_BASE_URL, ENGENIA_LLM_API_KEY, ENGENIA_DESIGN_MODEL, ENGENIA_VERIFY_MODEL,
   and set a spend alert and auto-shutdown.

3. containers/cad/Dockerfile: python:3.12-slim plus build123d and its system
   libraries, plus uv. containers/eda/Dockerfile: python 3.12 plus skidl and
   kicad-cli. containers/firmware/Dockerfile: python 3.12 plus platformio
   with the native platform pre-installed. containers/sim/Dockerfile:
   python 3.12 plus capytaine, gmsh, calculix-ccx.
   Pin base images by digest. Pin every pip package to an exact version.

4. containers/build.sh: builds all four, pushes to GitHub Container Registry,
   and writes the resulting digest to containers/<family>/DIGEST.

5. .github/workflows/ci.yml, runs on [self-hosted, engenia-build]:
   job lint-test: uv sync, ruff check, pytest -q.
   job pipeline: uv run engenia lint harbour-listener; uv run engenia run
   harbour-listener; uv run engenia gate harbour-listener.
   job reproduce: run the pipeline a second time in a clean checkout and
   compare a manifest of output hashes; fail on any difference.
   Upload projects/*/build/**/verdict.json and the manifest as artifacts.
   Keep a fallback matrix entry on ubuntu-latest for lint-test only, so CI
   still gives a signal if the runner is offline.

6. tests/infra/: a test that every Dockerfile pins its base image by digest
   and that every DIGEST file, when present, matches sha256:[0-9a-f]{64}.

Acceptance:
- vm_bootstrap.sh passes shellcheck.
- CI is green on this branch on the self-hosted runner.
- The reproduce job fails when a test deliberately writes a timestamp into
  an output (show this once in the pull request description, then remove it).

P2 does by hand, not the agent: create the machine, register the runner, add
secrets. Finish steps from AGENTS.md.
```

---

# Sprint 2 (21:30 to 23:30)

## B1-S2: simprep and wave simulation

**Seat B1 (OpenCode, P2). Branch `b1-s2-sim-waves`.**

```
Read AGENTS.md. You own engenia/sim/, packs/buoy/sim/, tests/sim/.

1. engenia/sim/simprep.py replacing the simprep stub: from requirements with
   verify.method simulation, write projects/<p>/src/simprep/cases.yaml
   listing each case: id, solver (capytaine | calculix | openfoam), template
   path under packs/buoy/sim/, requirement id, scenario names it needs.
   Needs only the spec, so it runs in parallel with design.

2. packs/buoy/sim/waves/: a Capytaine template for a floating axisymmetric
   body. Inputs: outer diameter, draft (computed from total mass and water
   density), centre of mass, mass, pitch inertia estimate, wave height and
   period from the scenario. Output: pitch response amplitude; report
   max_tilt_deg = pitch RAO at the scenario period x wave amplitude,
   converted to degrees. State the linear-theory assumption in the README.

3. engenia/sim/simulation.py replacing the simulation stub: for each case and
   each scenario, fill the template from integration interface facts, run it
   in the sim container, collect results into build/simulation/results.json.

4. engenia/sim/sanity.py, run before results are accepted:
   - mesh is closed and panel count within a range;
   - solver finished without warnings on the hydrostatics;
   - displaced volume from the mesh agrees with the level 1 value within
     5 percent;
   - result is finite and within a plausible range (0 to 90 degrees).
   A failed sanity check gives a Verdict with result "error", never pass.

5. interface(): per scenario max_tilt_deg, plus sanity results.

Acceptance (tests in tests/sim/):
- The wave case runs in the sim container in under 5 minutes.
- Doubling wave height doubles max_tilt_deg within 1 percent (linear check).
- A deliberately open mesh is rejected by sanity.
- A 20 percent wrong displaced volume is rejected by sanity.
- Two runs give identical results.json.

Finish steps from AGENTS.md.
```

---

# Sprint 3 (00:30 to 03:00)

## B1-S3: structure simulation and flow template

**Seat B1 (OpenCode, P2). Branch `b1-s3-sim-structure`.**

```
Read AGENTS.md. You own engenia/sim/, packs/buoy/sim/, tests/sim/.

1. packs/buoy/sim/structure/: a CalculiX case for the hull under external
   water pressure at the scenario depth. Mesh hull.step with gmsh
   (tetrahedra, fixed mesh size, fixed seed), apply pressure
   rho x g x depth on outer faces, constrain rigid body motion, linear
   elastic PETG from pack.yaml with a knock-down factor for printed parts
   (state it as ASSUMES: in the README and expose it as a parameter).
   Output max von Mises stress; safety_factor = allowable / max stress.

2. Sanity checks for this solver in engenia/sim/sanity.py: solver converged;
   mesh has no inverted elements; reaction force balances applied load
   within 2 percent; stress scales linearly when pressure doubles (run this
   as a test, not on every run).

3. packs/buoy/sim/flow/: an OpenFOAM template folder for steady flow past the
   hull with a README describing inputs and the drag output, and a case
   entry in simprep marked "enabled: false". engenia/sim/simulation.py skips
   disabled cases and lists them in the interface as not_run. Do not spend
   time making it run tonight.

Acceptance:
- The structure case runs in the sim container in under 10 minutes.
- Doubling depth doubles max stress within 2 percent.
- An unbalanced-load fixture is rejected by sanity.
- The flow case appears in the interface as not_run and produces no verdict,
  so its requirement, if any, shows as unverified in the matrix.

Finish steps from AGENTS.md.
```

---

# Proof block (03:30 to 06:00)

No new features. You run these by hand and paste the output in the team chat.

```bash
git checkout main && git pull
uv sync

# 1. Full pipeline, both projects
uv run engenia lint harbour-listener && uv run engenia run harbour-listener && uv run engenia gate harbour-listener
uv run engenia lint deck-sensor      && uv run engenia run deck-sensor      && uv run engenia gate deck-sensor

# 2. Nothing reruns
uv run engenia plan harbour-listener        # expect every stage: reuse

# 3. Change ripple
#    edit projects/harbour-listener/spec/R-003.yaml: value 7 -> 30
uv run engenia plan harbour-listener        # expect electronics and mechanical: run; firmware: reuse
uv run engenia run harbour-listener && uv run engenia gate harbour-listener

# 4. Fault suite
uv run python evals/faults/run_faults.py    # expect 10 of 10 caught

# 5. Repeatability
uv run python evals/bench/run_bench.py      # expect matching hashes, zero stages on repeat

# 6. Evidence
uv run engenia matrix harbour-listener
```

Then tick the list in `PROJECT.md` section 12 and fill in section 13 honestly.

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
