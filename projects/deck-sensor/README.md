# deck-sensor

A deck-mounted sensor box: temperature, humidity and vibration sensing on a weather deck,
battery powered with a LoRa uplink, in a 3D-printed PETG enclosure. Second example project
(task T4-S3, extended in T4-S4): it runs the same pipeline as `harbour-listener` with no
changes to `engenia/` core, and builds from its own product pack.

## Pack

`project.yaml` names the pack: `pack: enclosure`. The architect loads
`packs/enclosure/default_contract.json` and shows that pack to the design model, the
electronics stage selects parts from `packs/enclosure/parts/facts.toml`, and the firmware
stage generates the pack's `threshold` detector (a block-RMS level sampler) instead of the
buoy's tone detector. A project without `project.yaml` (harbour-listener) keeps the buoy
pack.

## Spec

Eight approved requirements in `spec/`, all verified by existing verifier scripts:

| id | obligation | check |
|---|---|---|
| R-001 | total mass at most 400 g | `mass_budget` |
| R-002 | enclosure fits the 220 x 220 x 250 mm print bed | `print_bed_fit` |
| R-003 | 14 days on one battery charge | `battery_capacity` |
| R-004 | minimum wall thickness 3 mm | `wall_thickness_min` |
| R-005 | average power at most 25 mW | `power_budget` |
| R-006 | zero electrical rule errors | `erc_clean` |
| R-007 | electronics assembly fits the battery bay | `bay_fit` |
| R-008 | firmware detects the reference vibration tone at >= 0.9 | `detection_rate` |

No requirement uses `method: simulation`, so the pipeline needs no wave or structure
solver. The flow case `simprep` always writes for red-team scenarios is skipped per
scenario because deck scenarios carry no water conditions; the skip is recorded in the
flow solver's sanity messages.

## Red-team scenarios

`scenarios/` holds four deck-relevant stress cases (heat soak, cold snap, washdown,
transport vibration). The red-team stage prefers `projects/<p>/scenarios/` over its fixed
buoy scenarios. The wave and water fields of the `Scenario` model are optional: these files
carry only the ambient temperature and deck-specific `extra` facts, because a deck-mounted
box does not float. The wave and flow solvers skip such scenarios and record why in their
sanity messages.

## Check plan

`checks/checks.yaml` is the deterministic plan the verify_plan stage writes for this spec
(byte-identical), `checks/checks.sha256` is its hash as written by build, and
`checks/APPROVED` holds that hash as the human sign-off, so the gate's approval check
passes without a model call.

## How to run

```bash
uv run engenia lint deck-sensor
uv run engenia run deck-sensor
uv run engenia gate deck-sensor
uv run engenia matrix deck-sensor
```

`run` generates `contract/` (from the architect stage) and `src/` (generated stage code,
left untracked like `harbour-listener`). The gate needs a completed run first.

## Remaining buoy-specific coupling

- The verification stage reads bed sizes from `packs/buoy/pack.yaml`; both packs declare
  the same 220 x 220 x 250 bed, so R-002 is unaffected.
