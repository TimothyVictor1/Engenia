# thruster-duct

A thruster duct: it fits a 70 mm propeller with 1 to 2 mm tip clearance, has a 4 x M4 mounting
flange and weighs under 150 g. Built from the `thruster_duct` pack (`project.yaml`: `pack:
thruster_duct`) with no model: `engenia run thruster-duct` and `engenia gate thruster-duct` pass
on the deterministic fallback. Created with `engenia new` from the request "A thruster duct that
fits a 70 mm propeller with 1-2 mm tip clearance, 4 x M4 flange carrying 15 N of thrust at safety
factor 3, under 150 g, walls at least 1.5 mm, fits the print bed".

## Spec

| id | obligation | check |
|---|---|---|
| R-001 | fit a 70 mm propeller with a tip clearance of 1 to 2 mm | `prop_clearance` |
| R-002 | the 4 x M4 flange carries 15 N of thrust, bending safety factor at least 3 | `bending_stress` |
| R-003 | total mass at most 150 g | `mass_budget` |
| R-004 | minimum wall thickness at least 1.5 mm | `wall_thickness_min` |
| R-005 | the duct fits the print bed | `print_bed_fit` |

## What the pipeline built

The pack skips electronics and firmware, so those stages publish `not_applicable`. No scenarios
live here: the red-team stage uses the pack's `thrust_surge` and `debris_and_swell`, resolved to
R-001, R-002 and R-004.

| part | mass |
|---|---|
| `duct` (nozzle ring and flange) | 33.62 g printed |

Dry mass 33.62 g, which is also the total. Throat 73.1 mm (propeller 70 mm, tip clearance 1.55
mm, 1.45 mm after the 0.1 mm swelling shift), wall 1.6 mm, 36.55 mm long with the propeller plane
16.4 mm from the inlet, flange 106.6 mm across and 3.2 mm thick with four 4.5 mm holes on a 93.0
mm circle. Bending safety factor 48.2 under twice the thrust. The STL is
`build/mechanical/duct.stl`.
