# deck-sensor

A deck-mounted sensor box: temperature, humidity and vibration sensing on a weather deck,
battery powered with a LoRa uplink, in a 3D-printed PETG enclosure. Second example project
(task T4-S3): it runs the same pipeline as `harbour-listener` with no changes to
`engenia/` core.

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
solver; the flow case `simprep` always writes for red-team scenarios is informational only.

## Red-team scenarios

`scenarios/` holds four deck-relevant stress cases (heat soak, cold snap, washdown,
transport vibration). The red-team stage prefers `projects/<p>/scenarios/` over its fixed
buoy scenarios. The `Scenario` model is still buoy-specific (wave fields are required), so
each file carries neutral zero-immersion values, noted in its rationale.

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

## Known buoy-specific coupling (not patched here)

- The mechanical stage resolves `packs/buoy/` by hard-coded path: pack, default contract,
  parts facts, and the `hull`/`mast_housing` templates. deck-sensor therefore builds the
  cylindrical buoy hull from the buoy pack; `packs/enclosure/` ships the ready-to-wire
  deck enclosure template.
- The architect's model fallback copies `packs/buoy/default_contract.json` into
  `contract/contract.json`, so deck-sensor inherits the buoy budgets (which happen to
  satisfy every requirement above).
- The electronics stage always selects a `lora` and an `audio` part from
  `packs/buoy/parts/facts.toml`, and requires an approved `battery_capacity` requirement
  (hence R-003).
- The firmware stage is a 200 Hz tone detector; R-008 is phrased around what it measures.
- The red-team `Scenario` model requires wave fields (see above).
