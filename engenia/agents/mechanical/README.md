Mechanical stage (T3-S1)
=======================

`MechanicalStage` implements the frozen Stage protocol. `generate()` validates
project requirements and reads the project contract, falling back to
`packs/buoy/default_contract.json` (PROJECT.md section 6). The replaceable
`propose_params(contract, requirements)` function sizes the hull around the bay
and mount holes, rejecting dimensions outside the contract envelope. This sprint
uses no model calls and does not infer new obligations from requirements.

Generated `src/mechanical/model.py` imports `packs.buoy.templates.hull` and reads
its adjacent `params.yaml`. `build()` executes that source in a fresh Python
worker, exports four CAD artifacts, and publishes a schema-valid
`build/mechanical/interface.json`. `interface()` returns its facts for the core
scheduler. Run from a repository checkout so the pack templates are available.

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

Dry mass = combined solid volume in cm3 × density × infill factor. If present,
`packs/buoy/pack.yaml` supplies:

```yaml
materials:
  PETG:
    density_g_cm3: 1.27
printing:
  infill_factor: 1.0
```

Until the pack configuration lands, missing `pack.yaml` warns and uses PETG
1.27 g/cm3. Infill defaults to 1.0 (conservative fully solid material); the factor
is written to source parameters during generation. It represents an effective
material fraction, not a slicer prediction. Density is read during each build.

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

Validation (without changing the shared lockfile)
------------------------------------------------

```sh
uv pip install -e .
UV_FROZEN=true UV_NO_SYNC=true uv run ruff check .
UV_FROZEN=true UV_NO_SYNC=true uv run pytest tests/mechanical/ -q
```

`uv.lock` is outside this seat's permitted edits and needs refreshing by its
owner after the dependency change. The step-0 registry smoke test in
`tests/smoke/` still requires every stage to declare no checks; its owner must
update that expectation for real stages.
