# Current drag

`run_case(case, ctx)` estimates current drag for every built red-team scenario. It uses the
scenario's `extra.current_speed_ms` when supplied, then the documented assumptions in
`packs/buoy/sim/flow/params.yaml`. The present fixed scenarios do not include current speeds.

The wetted projected area is cylinder diameter times draft. Draft comes from assembly mass,
with trapped floodwater added when `extra.hull_flood_percent` is present. The fallback is
`F = 0.5 * water_density * Cd * area * speed**2`; output evidence is `calculated`.
The Cd source and speed assumptions are in `params.yaml`.

`shutil.which("simpleFoam")` detects whether OpenFOAM is installed. This package has no
OpenFOAM mesh and force-integration case yet, so it uses the calculated method even where
OpenFOAM is installed. It never invokes an absent system solver.
