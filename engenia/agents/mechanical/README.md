Mechanical stage (T3-S1)
=======================

`MechanicalStage` implements the frozen Stage protocol. `generate()` validates
project requirements and reads the project contract, falling back to the selected
pack's `default_contract.json` (PROJECT.md section 6). The replaceable
`propose_params(contract, requirements)` function sizes the hull around the bay
and mount holes, rejecting dimensions outside the contract envelope. Approved
requirements whose check is `wall_thickness_min` supply their `verify.limit.min`
as the wall thickness; if several apply, the strongest minimum wins. Without
such a requirement the wall defaults to 3.0 mm. This sprint uses no model calls.

Generated `src/mechanical/model.py` imports the selected pack's template and reads
its adjacent `params.yaml`. The buoy pack uses the hull; the enclosure pack uses
the deck enclosure body. `build()` executes that source in a fresh Python
worker, exports four CAD artifacts, and publishes a schema-valid
`build/mechanical/interface.json`. `interface()` returns its facts for the core
scheduler. Run from a repository checkout so the pack templates are available.

Pack-owned templates
--------------------

A template entry in `pack.yaml` with `module: packs.<pack>.templates.<name>` is sized and built
by that module (the pressure_housing `sensor_pod` is the first). The module exposes `Params`,
`propose(contract, requirements, pack, pack_dir) -> (params, decisions)`, `build(params) ->
{name: Shape}`, `analysis_solids(params)`, `hardware(params)` and `extra_facts(params)`. The
stage logs each decision, writes `params.yaml` and a generated `model.py`; the CAD worker exports
every part as STEP and STL (analysis solids as STEP only) and publishes `dry_mass_g` as printed
volume x density x infill plus the bought hardware, with a per-part mass table. An envelope that
is too small raises the same change request as the hull. No model is asked: the module's
`propose` is the proposer.

Geometry and facts
------------------

All geometry is in mm. XY is centred on the cylinder, Z starts at the hull
bottom, and the lid is exported in its assembled position. Board hole positions
are relative to the lower-left corner of the battery bay. An open rectangular
battery well provides the full requested clear XYZ space; supporting tabs and
four blind board standoffs start above it. A wider upper rim meets the underside
O-ring groove in the lid. Seal hardware and lid fasteners are not modelled.

`bbox_mm` is the assembled bounding box. `displaced_volume_cm3` is the outer
closed cylinder volume, assuming a sealed assembly. `internal_volume_cm3` is
that closed volume minus both actual solid volumes (including internal fixtures).
`wall_min_mm` is the template's nominal structural minimum: cylindrical wall,
floor, bay wall, blind-hole floor and material above the groove. This does not
replace A2's independent wall-thickness verification. The centre of mass comes
from the CAD solids at uniform effective density.

Dry mass = combined solid volume in cm3 × density × infill factor.
`packs/buoy/pack.yaml` supplies:

```yaml
materials:
  PETG:
    density_g_cm3: 1.27
printing:
  infill_factor: 1.0
  bed_mm: [220, 220, 250]
```

Infill 1.0 means solid walls; the factor is written to source parameters during
generation. It represents an effective material fraction, not a slicer prediction.
Density is read during each build. The committed pack provides PETG 1.27 g/cm3
and a 220 × 220 × 250 mm print bed. If no pack file is available, the stage
retains the PETG density fallback with a warning.

The four check names are declared for the scheduler. Missing verifier files
produce explicit warnings and are skipped; the stage neither implements nor
runs verifiers. No passing verdict is invented for an absent check.

Reproducibility
---------------

build123d is pinned to 0.12.0. STEP uses a BytesIO stream, which avoids output
paths in its header, and the exporter's `timestamp` argument fixes the date to
`2000-01-01T00:00:00`. STL uses fixed linear deflection 0.01 mm and angular
deflection 0.1 radians; the binary STL's 80-byte comment is replaced by a fixed
string. The kernel treats linear STL deflection as relative. Triangle payloads
are not rewritten. Each build uses a fresh interpreter to isolate CAD state.

Tests compare SHA256 for every STEP, STL and interface file across independent
workers, different directories, and repeated builds without regenerating source.
Byte identity is tested within one kernel/platform version; cross-version or
cross-architecture kernel identity is not claimed.

Validation
----------

```sh
uv sync --locked
uv run ruff check .
uv run pytest -q
```
