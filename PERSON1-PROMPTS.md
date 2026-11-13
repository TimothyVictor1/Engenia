# Engenia: Person 1's Prompts

**How to use this file**

1. Do the tasks in the order they appear. One task = one Superset workspace = one branch.
2. In Superset press Cmd+N and name the workspace exactly as the branch shown under the task title.
3. Wait for "Workspace ready", start the agent named for that task, and paste the whole grey block.
4. When the agent says it is finished, tick the acceptance list yourself.
5. Post the pull request link in the team chat and ask for review: `Review PR #<number>, task <task ID>`.
6. If the review says "changes needed", use the fix prompt at the bottom of this file.

Every prompt assumes the agent has read `AGENTS.md`, which Step 0 creates.

---

# Sprint 1 (18:30 to 21:00): start both, one workspace each

## A1-S1: electronics stage

**Seat A1 (Codex, P1). Branch `a1-s1-electronics`.**

```
Read AGENTS.md. You own engenia/agents/electronics/, packs/buoy/parts/,
tests/electronics/. Add skidl and tomli-w to pyproject.toml (allowed).

Replace the electronics stub with a real stage.

1. packs/buoy/parts/facts.toml: an approved parts list. For each part: id,
   function, mass_g, size_mm, supply voltage range, active and sleep current
   in mA, and a `source` string naming the datasheet and page. Include at
   least: one microcontroller module, one LoRa radio module, one audio
   preamp/ADC, three battery options of different capacity and size, one
   regulator. Every numeric value must have a source. A part without a source
   is invalid.

2. engenia/agents/electronics/stage.py:
   - generate(): writes src/electronics/selection.yaml (chosen part ids and a
     duty cycle: fraction of time in each mode) and circuit.py (a SKiDL
     script wiring the chosen parts using the pin_map from contract.json).
     This sprint: choose parts by a deterministic rule behind
     propose_selection(contract, requirements, facts): pick the smallest
     battery that meets endurance at the computed average power and fits
     bay_mm; if none fits, write a ChangeRequest file asking for the smallest
     bay that works, and still finish with the best-fitting battery.
   - build(): runs circuit.py to produce circuit.net, runs SKiDL ERC and
     writes erc.txt, writes bom.csv.
   - interface(): mass_g total, board_mm, avg_power_mw, battery_wh,
     endurance_days, pins_used (name to pin), erc_errors count.
   - checks: ["power_budget", "battery_capacity", "erc_clean",
     "mass_budget", "bay_fit"].

Acceptance (tests in tests/electronics/):
- facts.toml loader rejects a part with a missing source.
- With endurance 7 days the smallest battery is chosen; with 30 days a larger
  one is chosen.
- When no battery fits the bay, exactly one ChangeRequest file is written
  with status open, and build() still completes.
- pins_used matches contract pin_map exactly.
- Two builds give an identical circuit.net hash.

Do not edit contract.json yourself. Do not write verifiers.
Finish steps from AGENTS.md.
```

---

## A2-S1: level 1 verifier scripts

**Seat A2 (OpenCode, P1). Branch `a2-s1-verifiers`.**

```
Read AGENTS.md. You own verifiers/ and tests/verifiers/.

Write verifier scripts. Each is verifiers/<name>.py with
run(ctx: CheckContext) -> Verdict (see PROJECT.md section 6). A verifier
reads build/<stage>/interface.json and output files only. No model calls, no
network, no clock, no randomness. evidence_level is "calculated".
On missing input return result "error" with a clear message, never raise.

Write these, with the formula in the docstring:

- mass_budget: sum of dry_mass_g (mechanical) and mass_g (electronics)
  against limit max. When run for one stage, compare that stage's mass with
  its budget in contract.json.
- buoyancy_margin: (displaced_volume_cm3 * water_density - total_mass_g)
  / total_mass_g, limit min. water_density from params, default 1.005 g/cm3.
- print_bed_fit: bbox_mm fits params bed_mm in some axis-aligned orientation.
- wall_thickness_min: wall_min_mm against limit min.
- metacentric_height: simple upright cylinder estimate GM = KB + BM - KG,
  using interface facts; state assumptions in the docstring.
- power_budget: avg_power_mw against limit max.
- battery_capacity: endurance_days = battery_wh * 1000 / avg_power_mw / 24,
  with a derating factor from params (default 0.8), limit min.
- erc_clean: erc_errors equals 0.
- bay_fit: electronics board_mm and battery size fit battery_bay_mm.

Also verifiers/__init__.py with load(name) and a registry, and
verifiers/_common.py with read_interface(), compare(measured, limit).

Acceptance (tests in tests/verifiers/, using small fixture interface files):
- For every verifier: one pass case, one fail case, one missing-input case
  returning "error".
- compare handles min, max, equals, and combinations.
- No verifier imports httpx, random, time or datetime (add a test that scans
  the source files).

Finish steps from AGENTS.md.
```

---

# Sprint 2 (21:30 to 23:30)

## A1-S2: firmware stage

**Seat A1 (Codex, P1). Branch `a1-s2-firmware`.**

```
Read AGENTS.md. You own engenia/agents/firmware/ and tests/firmware/.
Add platformio and numpy to pyproject.toml (allowed).

Replace the firmware stub.

1. generate() writes a PlatformIO project in projects/<p>/src/firmware/:
   - platformio.ini with a `native` environment.
   - include/pins.h generated from contract.json pin_map.
   - src/detector.c/.h: a tone detector (Goertzel at a configurable frequency)
     with a threshold and a hold time; pure C, no hardware calls.
   - src/duty.h: duty cycle constants, the same values electronics used
     (read from electronics selection if present, else contract defaults).
   - test/test_detector.c: Unity tests.
   - tools/replay.py: feeds a WAV file through the detector compiled as a
     native program and prints detections as JSON.
   - data/: two small synthetic WAV files generated by a deterministic
     script: tone_in_noise.wav (ten 200 Hz bursts at a fixed signal-to-noise
     ratio, fixed random seed) and quiet.wav (noise only, 10 minutes).
   This sprint, write the files from templates behind propose_firmware().

2. build(): `pio run -e native`, `pio test -e native`, then replay both WAV
   files. Write build/firmware/detections.json and metrics.json with
   detection_rate and false_alarms_per_hour.

3. interface(): pins_used, duty_cycle, flash_kb estimate, detection_rate,
   false_alarms_per_hour, tests_passed, tests_total.

4. checks: ["detection_rate", "false_alarm_rate", "fw_tests_pass"].
   Tell A2 these three verifier names; A2 writes them in A2-S2.

Acceptance:
- pio test passes in the firmware container.
- Detection rate on tone_in_noise.wav is at least 0.9 and false alarms on
  quiet.wav are at most 2 per hour with default settings.
- Regenerating the WAV files gives identical hashes.
- pins.h matches contract pin_map exactly; changing the pin map changes
  pins.h and the stage key.

Finish steps from AGENTS.md.
```

---

## A2-S2: fault suite and trace matrix

**Seat A2 (OpenCode, P1). Branch `a2-s2-faults-matrix`.**

```
Read AGENTS.md. You own verifiers/, evals/faults/, engenia/evidence/,
tests/verifiers/, tests/evidence/.

1. New verifiers with pass, fail and error tests: detection_rate,
   false_alarm_rate, fw_tests_pass (from firmware interface); wave_tilt and
   stress_safety_factor (from simulation interface, evidence_level
   "simulated").

2. evals/faults/: planted defects. Each is a folder with a README line and
   fixture interface/output files that represent one broken design:
   F01 wall 1 mm; F02 battery too small for endurance; F03 hull too large for
   the bed; F04 mass over total; F05 negative buoyancy margin; F06 ERC error;
   F07 board larger than bay; F08 pin mismatch between firmware and
   electronics; F09 detection rate 0.5; F10 a verdict file edited by hand to
   say pass with a measured value that fails its limit.
   evals/faults/run_faults.py runs the relevant verifier or gate check on
   each and writes evals/faults/report.json: fault id, expected check,
   caught true/false. Exit 1 unless all are caught.

3. engenia/evidence/matrix.py, `engenia matrix <p>`: writes
   build/report/trace_matrix.md and .json. One row per requirement: id,
   statement, stage(s), check, measured, limit, result, evidence level,
   artifact hash. Add a summary: counts by result and by evidence level, and
   a list of requirements with no verdict.

Acceptance:
- run_faults.py catches 10 of 10.
- F10 is caught: the gate recomputes result from measured and limit and
  rejects a mismatch (if the gate does not do this yet, add the comparison
  in verifiers/_common.py as verify_consistency() and tell T2).
- The matrix lists all twelve requirements; a requirement with no verdict
  shows as "unverified", never as pass.

Finish steps from AGENTS.md.
```

---

# Sprint 3 (00:30 to 03:00)

## A1-S3: firmware emulator slot and parts growth

**Seat A1 (Codex, P1). Branch `a1-s3-emulator-slot`.**

```
Read AGENTS.md. You own engenia/agents/firmware/, engenia/agents/electronics/,
packs/buoy/parts/.

1. Emulator slot: add a second PlatformIO environment for the chosen
   microcontroller that compiles the same detector for the real target
   (compile only). Report flash and RAM use from the build output in the
   interface, and add them to checks as fw_flash_budget and fw_ram_budget
   against contract budgets.firmware (tell A2 the two verifier names).
   Add engenia/agents/firmware/emulator.py with a function run_in_emulator()
   that raises NotImplementedError with a message describing the planned
   Renode run, and a skipped test that documents the intended assertions.

2. Parts library: add two more microcontroller options and two more battery
   options to facts.toml, every value with a source. Add a second-source
   field per part; the electronics stage reports parts_without_second_source
   in its interface.

Acceptance:
- Target compile succeeds in the firmware container and sizes are reported.
- A firmware budget set below the measured size fails the check.
- parts_without_second_source is a list, empty for the default selection.

Finish steps from AGENTS.md.
```

---

## A2-S3: assumption ledger and benchmark runner

**Seat A2 (OpenCode, P1). Branch `a2-s3-ledger-bench`.**

```
Read AGENTS.md. You own verifiers/, evals/, engenia/evidence/.

1. Verifiers fw_flash_budget and fw_ram_budget with pass, fail, error tests.

2. engenia/evidence/ledger.py: an assumption ledger at
   projects/<p>/assumptions.yaml. Entry: id, text, source (which stage or
   person), related requirement ids, status (open | simulated | measured),
   evidence (path or note). Stages can append through add_assumption().
   Seed it by scanning verifier docstrings for lines starting "ASSUMES:" and
   adding them for every verifier used in checks.yaml. `engenia matrix` appends
   the ledger to the report with counts by status.

3. evals/bench/run_bench.py: runs lint, run, gate on every project under
   projects/ N times (default 3) from clean build folders and writes
   evals/bench/report.json: per project pass/fail, manifest hash per run,
   whether all hashes match, stages executed on run 2 (should be zero),
   attempts per stage, wall time. Exit 1 if any hashes differ.

Acceptance:
- Every verifier with an "ASSUMES:" line appears in the ledger.
- run_bench.py on harbour-listener and deck-sensor reports matching hashes
  across three runs and zero stages executed on a repeat run.

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
