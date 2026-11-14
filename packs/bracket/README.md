# packs/bracket

Product pack for printed PETG mounting brackets: a **mast clamp** with a cantilever arm and a
four-hole mounting plate, and an **L-bracket** for a wall. Same file shape as the other packs:
`pack.yaml`, `default_contract.json`, `intake.yaml`, a build123d template under `templates/`
and red-team scenarios under `scenarios/`. A bracket has no electronics, so the pack declares
`skip_stages: [electronics, firmware]` (see below) and ships no parts catalogue; the clamp bolt
is a mass allowance in `pack.yaml`.

## Files

- `pack.yaml`: the `bracket` template (`module:` names the Python module; checks `mass_budget`,
  `print_bed_fit`, `wall_thickness_min`, `bending_stress`), the PETG `structure:` numbers the
  `bending_stress` verifier uses (allowable 35 MPa x printed knock-down 0.6 = 21 MPa) and the
  `bracket:` rules the proposer sizes from. Every number there is a rule, not a dimension.
- `templates/bracket.py`: `propose(contract, requirements, pack, pack_dir)` returns the
  parameters and a decision record per choice; `build(params)` returns the printed `bracket`;
  `extra_facts` publishes the cantilever root section and offset for the verifier.
- `default_contract.json`: a 120 g mechanical budget, a 220 x 100 x 100 mm envelope and zero
  electronics and firmware budgets.
- `intake.yaml`: "holds a 0.5 kg antenna 150 mm from a 40 mm mast, safety factor >= 3, under
  120 g" becomes requirements. The load and distance go into the statement, because the
  requirement schema has no field for them.
- `scenarios/`: `gust_load` (load x 2) and `ice_loading` (load x 1.5). They name checks
  (`target_checks`), not requirement ids.

## Choosing the style and sizing it

A mast diameter in the bending requirement ("a 40 mm mast") makes a clamp whose bore is the
mast plus the printed fit clearance (the **clamp diameter** parameter); no mast makes an
L-bracket. `propose` then sizes the arm from the load:

- design load = requirement load x the largest `load_factor` of the pack scenarios that target
  `bending_stress` (2 for the gust);
- moment at the arm root = load x (distance - root offset). The root offset is the clamp's outer
  radius, and 0 for the L-bracket (the wall face);
- arm width (clamp) or shelf thickness (L-bracket) from `sigma = 6 M / (b h^2)` at the
  requirement's safety factor x a 1.15 margin; never below the wall minimum (the larger of the
  pack's 3.2 mm and any `wall_thickness_min` requirement), and never taller than 4 x its width.

The mounting plate is a square pattern of four M4 clearance holes; the clamp is a split ring with
two ears and a bolt hole; fillets (up to 2 mm) round every inside corner of the arm. When the
bracket does not fit the contract's envelope the stage raises a change request on
`budgets.mechanical.outer_envelope_mm`; a mass over budget raises one on
`budgets.mechanical.mass_g`.

## bending_stress

`verifiers/bending_stress.py`: root stress `sigma = M c / I` of a b x h rectangle, `M = F x
(lever - root offset)`, safety factor = allowable / sigma against the requirement's limit. The
load and lever come from the requirement statement (`engenia.sim.loads`); the root section from
the mechanical interface; scenario load factors from this pack's scenario files. Evidence level
`calculated`. It is registered in the intake registry and has a canary.

## Mechanical-only mode

`skip_stages` in `pack.yaml` (electronics and firmware only) makes those stages publish
`not_applicable` interfaces with zero mass instead of building. The graph, cache and gate treat
them as any stage; the integration stage drops the board, mounts and pin checks and keeps the
mass total and the contract-version check; the architect accepts zero electronics and firmware
budgets and requires them to be zero.

## Status (honest)

- The check is a closed-form beam formula on the arm root: no shear, torsion, clamp friction,
  fillet stress concentration, stiffness or fatigue. The antenna's own moment is not modelled.
- The 2 x gust and 1.5 x ice load factors are assumptions, not wind or icing calculations.
- On the antenna example the arm is set by its 4:1 aspect limit, not by stress: the safety
  factor comes out at about 5.9 under the gust against 3 asked for. A larger load or safety
  factor gives a stress-governed arm.
- The clamp bolt mass (4 g) is an allowance, not a vendor figure. The clamp's holding force on
  the mast is not analysed. Printed PETG strength at low temperature is not modelled.
