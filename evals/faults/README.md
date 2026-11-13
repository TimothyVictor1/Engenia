# Planted-defect suite

Ten faults a reviewer can plant in a project, and the check that must catch each one.
This suite is the regression net for the check layer: if a change makes any of these
checks miss a real defect, `run_faults.py` fails.

## Layout

`F01-wall-1mm/` ... `F10-edited-verdict/` each hold the smallest broken piece of a
project that one check must reject — only the `build/<stage>/interface.json` files the
check reads, plus `contract/contract.json` for F08 and a `build/mechanical/verdict.json`
for F10. Each folder has a one-line README saying what was planted and what must catch it.

`run_faults.py` copies each folder into a scratch project under a temporary root (with a
copy of `verifiers/`, exactly as a real project root is laid out), runs the one check that
must catch the fault, and writes `report.json` next to this README.

## Faults

| Fault | Planted defect | Must be caught by |
|---|---|---|
| F01 | wall 1 mm thick (min 3 mm) | `wall_thickness_min` verdict fail |
| F02 | 1 Wh battery, 100 mW average load (min 7 days) | `battery_capacity` verdict fail |
| F03 | 260 mm hull on a 160 mm print bed | `print_bed_fit` verdict fail |
| F04 | 350 g hull + 100 g electronics (total max 400 g) | `mass_budget` verdict fail |
| F05 | 400 g displacing 100 cm3 (margin min 0.20) | `buoyancy_margin` verdict fail |
| F06 | 2 electrical rule check errors | `erc_clean` verdict fail |
| F07 | 100 mm board in a 70 mm battery bay | `bay_fit` verdict fail |
| F08 | firmware pin LORA_CS on GPIO6, contract says GPIO5 | integration check `int_pins_match` verdict fail |
| F09 | detection rate 0.5 (min 0.9) | `detection_rate` verdict fail |
| F10 | hand-edited verdict: pass with 450 g (max 400 g) | gate recomputation rejects the verdict |

A fault counts as caught only when the check returns `fail` (for F10: the gate's
`verdict_problem` finds the verdict disagrees with its own numbers). `error` never counts
as caught — a missing input must not be scored as a catch.

## Run

    uv run python evals/faults/run_faults.py

Prints one line per fault, then `10/10 faults caught`. Exits 0 only when every fault is
caught; otherwise it exits 1 and says which faults were missed and why. Pass
`--report PATH` to write the report somewhere else.

`report.json` is committed and deterministic (no timestamps, no scratch paths), so a CI
diff shows exactly which catch changed.
