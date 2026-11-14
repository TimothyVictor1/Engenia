# antenna-bracket

A mast-mounted antenna bracket: it holds a 0.5 kg antenna 150 mm from a 40 mm mast with a
bending safety factor of at least 3 and weighs under 120 g. Built from the `bracket` pack
(`project.yaml`: `pack: bracket`) with no model: `engenia run antenna-bracket` and `engenia gate
antenna-bracket` pass on the deterministic fallback. Created with `engenia new` from the
request "A bracket that holds a 0.5 kg antenna 150 mm from a 40 mm mast, safety factor >= 3,
under 120 g, walls at least 3 mm, fits the print bed".

## Spec

| id | obligation | check |
|---|---|---|
| R-001 | hold a 0.5 kg load 150 mm from the axis of a 40 mm mast, bending safety factor at least 3 | `bending_stress` |
| R-002 | total mass at most 120 g | `mass_budget` |
| R-003 | minimum wall thickness at least 3 mm | `wall_thickness_min` |
| R-004 | the bracket fits the print bed | `print_bed_fit` |

The distance is measured from the mast axis, so the arm's bending moment is taken at the clamp's
outer surface (23.4 mm from the axis).

## What the pipeline built

The pack skips electronics and firmware, so those stages publish `not_applicable`. No scenarios
live here: the red-team stage uses the pack's `gust_load` and `ice_loading`, resolved to R-001
and R-003. The bending verdict is the safety factor under the worst of them (2 x load).

| part | mass |
|---|---|
| `bracket` (clamp ring, arm, four-hole plate) | 36.68 g printed |
| M4 clamp bolt and nut | 4.00 g (bought, allowance) |

Dry mass 40.68 g, which is also the total. Bore 40.4 mm, ring 3.2 mm thick and 20 mm tall,
arm 5.2 x 20 mm, plate 43.6 x 43.6 x 3.2 mm with four 4.5 mm holes on a 30 mm square. Overall
185.4 x 46.8 x 43.6 mm. Bending safety factor 5.86 under the gust (11.7 at the stated load).
The STL is `build/mechanical/bracket.stl`.
