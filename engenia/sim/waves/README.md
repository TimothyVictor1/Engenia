# Waves solver

`run_case(case, ctx)` answers requirement R-011 (tilt in waves). It solves the pitch response of
the buoy hull with [Capytaine](https://capytaine.org) for each red-team scenario.

## Method (linear theory)

- Linear potential flow: small waves, small motions, no viscosity, no wind, no mooring. Real
  tilt is larger or smaller where these fail; nothing here models drag, slamming or green water.
- One regular wave per scenario, at `wave_period_s`, travelling along x. Wave amplitude is half
  of `wave_height_m` (the significant wave height).
- The hull is an upright closed cylinder. Diameter and height come from the mechanical
  `bbox_mm`; mass from integration `total_mass_g`.
- Draft = mass / (water density x waterplane area). Water density is the scenario's
  `water_density` in kg/m3; water depth is its `depth_m`.
- Surge, heave and pitch are solved together; pitch is about the centre of mass. Inertia is that
  of a uniform cylinder of the same size and mass. Hydrostatic stiffness comes from the mesh.
- `max_tilt_deg` = |pitch RAO at the scenario period| x wave amplitude, in degrees. Because the
  theory is linear, doubling the wave height doubles the tilt.
- The only damping is radiation damping, so response near the pitch natural period is
  overstated. Scenario periods are far above it for this hull.
- The centre of mass is the integration value; if integration has none (no battery bay
  position), the hull's own is used and a note is written to `results.json`.
- Flooding (`extra.hull_flood_percent`) and the other `extra` fields are not modelled.

## Sanity checks

A scenario fails, and with it the whole case, if: the mesh is not closed; the mesh's displaced
volume differs from mass / density by more than 5 percent; the hull does not float; pitch
stiffness is not positive; or the tilt is not finite and between 0 and 90 degrees. A case
that lists scenarios which were not built, or leaves out built ones, also fails.

## Settings

`packs/buoy/sim/waves/params.yaml`: gravity, mesh resolution, volume tolerance.

## Cache

The simulation stage key includes `SimulationStage.solver_digest`, a hash of `engenia/sim/**/*.py`
and every `packs/buoy/sim/*/params.yaml`. After changing either, run
`uv run python -m engenia.sim.solver_digest --write`; a test fails until you do.
