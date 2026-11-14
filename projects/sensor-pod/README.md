# sensor-pod

A watertight sensor pod: it survives 50 m, fits a 60 mm sensor board and stays under 250 g.
Built from the `pressure_housing` pack (`project.yaml`: `pack: pressure_housing`), with no
model: `engenia run sensor-pod` and `engenia gate sensor-pod` pass on the deterministic
fallback. Created with `engenia new` from the request "A sensor pod that survives 50 m, fits a
60 mm sensor, under 250 g, near-neutral buoyancy, wall at least 3 mm, and it fits the 3D
printer bed".

## Spec

| id | obligation | check |
|---|---|---|
| R-001 | total mass at most 250 g | `mass_budget` |
| R-002 | stress safety factor at least 1.5 at 50 m depth | `stress_safety_factor` |
| R-003 | minimum wall thickness at least 3 mm | `wall_thickness_min` |
| R-004 | near-neutral buoyancy: margin at least -0.05 | `buoyancy_margin` |
| R-005 | electronics (sensor board and battery) fit the bay | `bay_fit` |
| R-006 | every printed part fits the print bed | `print_bed_fit` |

"Fits a 60 mm sensor" is carried by the pack: the catalogue's board is 60 mm long and the
default contract's bay is 64 mm, which `bay_fit` checks. The number in the request is recorded
in R-005's statement; it does not change the bay.

## What the pipeline built

No scenarios live here: the red-team stage uses the pack's `max_depth_plus_20pct` and
`cold_water`, resolved to R-002 and R-004. The stress safety factor is the worst of 50 m and
the two scenarios (60 m, 1025 kg/m3 and 50 m, 1028 kg/m3).

| part | printed mass |
|---|---|
| `tube` | 59.24 g |
| `cap_rear` (with cable-gland boss) | 30.55 g |
| `cap_front` | 31.05 g |
| O-rings 42 x 2 mm (2), PG7 gland | 5.09 g (bought) |

Dry mass 125.93 g, electronics 14.95 g, total 140.88 g. Tube 52.7 mm outer diameter, 3.6 mm
wall, 102.2 mm long overall. Stress safety factor 4.76; buoyancy margin +0.50 (it floats; the
requirement only allows it to be near neutral). STL files are in
`build/mechanical/` (`tube.stl`, `cap_rear.stl`, `cap_front.stl`).
