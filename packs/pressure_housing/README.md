# packs/pressure_housing

Product pack for a watertight cylindrical sensor pod: a printed PETG tube closed by two plug
end caps that seal on O-rings, with an optional cable-gland boss on the rear cap. Same file
shape as `packs/enclosure/`: `pack.yaml`, `default_contract.json`, `intake.yaml`, a parts
catalogue under `parts/`, a build123d template under `templates/`, plus red-team scenarios
under `scenarios/`.

## Files

- `pack.yaml` declares the `sensor_pod` template (`module:` names the Python module; its
  checks are `mass_budget`, `buoyancy_margin`, `print_bed_fit`, `wall_thickness_min`), the
  structure-solver block (PETG constants, mesh size, `scenarios_govern`) and the `pod:` rules
  the proposer sizes from. Every number in `pod:` is a rule, not a dimension.
- `templates/sensor_pod.py` is the whole product. `propose(contract, requirements, pack,
  pack_dir)` returns the parameters and one decision record per choice; `build(params)` returns
  the printed parts (`tube`, `cap_rear`, `cap_front`); `analysis_solids(params)` returns the
  bonded `vessel` the structure solver meshes; `hardware` and `extra_facts` say what the
  mechanical interface publishes. The mechanical stage loads the module named in `pack.yaml`
  and adds nothing pack-specific.
- `parts/o_rings.toml` is the standard O-ring table (metric ID x cross-section, NBR 70). The
  proposer picks from it and never invents a ring size.
- `parts/facts.toml` is the electronics catalogue: ESP32-WROVER-B, MAX4466EUK hydrophone
  preamp, BME280 (watches the cavity for leaks), S7V8F3 regulator and the EEMB cells. No radio:
  a sealed pod logs to flash and is read out on recovery.
- `default_contract.json` pins the 64 x 36 x 22 mm electronics bay (a 60 mm sensor board plus
  clearance), the mechanical envelope and the `HYDROPHONE_ADC` signal.
- `scenarios/` holds `max_depth_plus_20pct` and `cold_water`. They target checks, not
  requirement ids; the red-team stage resolves them to the project's own requirement ids.
- `intake.yaml` turns "survives 50 m, fits a 60 mm sensor, under 250 g" into requirements.

## How the pod is sized (all in `propose`)

1. **Bore** = bay cross-section corner diagonal + 2 x clearance, on a 0.5 mm grid.
2. **O-ring**: the smallest standard cross-section whose ID stretches 0.5 to 5 % over the groove
   root, with a 20 % squeeze in the bore. The bore widens in 0.5 mm steps if no ring fits.
3. **Design pressure** = water density x g x depth x the pack's depth factor. The depth comes
   from the stress requirement's statement ("at 50 m depth"); the factor (1.2) and the density
   (1028 kg/m3) come from the pack's own scenarios.
4. **Wall** = the largest of the requirement minimum, the printing minimum, the thin-wall hoop
   estimate (with the safety factor and a margin) and the long-tube elastic collapse
   requirement (`2E/(1-v^2) (t/D)^3 >= 2 x P`), rounded up to 0.4 mm. For a 50 m PETG pod
   collapse, not hoop stress, governs.
5. **Groove, plug, flange, tube length** follow from the ring, the wall and the bay length.
6. **Cable gland**: a boss for a PG7 gland (12.5 mm hole) when the cap is wide enough.

Parameters that do not fit the contract's envelope raise a change request on
`budgets.mechanical.outer_envelope_mm`; a mass over budget raises one on
`budgets.mechanical.mass_g` (the existing mechanical-stage behaviour).

## Structure analysis

The mechanical stage also exports `vessel.step`: tube and caps bonded into one solid, the gland
hole filled, the O-ring grooves left as dry voids. The structure solver loads every wet face
with rho g d (the cavity and grooves are listed in `structure_dry_regions_mm`), which balances
by itself, then reports the safety factor against the PETG allowable with the printed
knock-down. With `scenarios_govern: true` the published safety factor is the worst of the
design depth and each red-team scenario. The problem is linear, so a scenario scales the stress
by its pressure ratio.

## Status (honest)

- Collapse is checked only in the proposer, with the long-tube formula, which is conservative
  for a tube with stiff end caps. The finite element check is linear stress, not buckling.
- The maximum von Mises stress moves with mesh size because the groove corners are sharp:
  on the sensor-pod example 4 mm, 3 mm and 2 mm meshes gave safety factors 5.1, 4.8 and 4.3.
  The margin over the requirement is large; the number is not mesh-converged.
- The bonded vessel assumes the plug and tube carry load together and ignores the O-ring. A real
  pod needs a hydrostatic pressure test of a printed prototype.
- The cable gland is a leak path that this pack sizes a boss for and does not analyse. The
  gland mass (4 g) is an allowance, not a vendor figure.
- PETG strength at 2 C is not modelled; the `cold_water` scenario raises density only.
- O-ring sizes are nominal catalogue sizes (ISO 3601 style metric series). Check a size against
  the supplier before buying.
