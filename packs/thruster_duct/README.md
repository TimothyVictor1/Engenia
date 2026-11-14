# packs/thruster_duct

Product pack for a printed PETG thruster duct: a Kort-nozzle-like ring around a small thruster
propeller with a bolted mounting flange. Same file shape as the other packs: `pack.yaml`,
`default_contract.json`, `intake.yaml`, a build123d template under `templates/` and red-team
scenarios under `scenarios/`. A duct has no electronics and no firmware, so the pack uses
`skip_stages: [electronics, firmware]` (the mechanical-only mode the bracket pack added) and
ships no parts catalogue; the flange bolts belong to the thruster mount and are not counted.

## Files

- `pack.yaml`: the `duct` template (`module:` names the Python module; checks `mass_budget`,
  `print_bed_fit`, `wall_thickness_min`, `prop_clearance`, `bending_stress`), the PETG
  `structure:` numbers the flange check uses (allowable 35 MPa x printed knock-down 0.6), and
  the `duct:` rules the proposer sizes from. Every number there is a rule, not a dimension.
- `templates/duct.py`: `propose(contract, requirements, pack, pack_dir)` returns the parameters
  and a decision record per choice; `build(params)` revolves the profile, drills the bolt circle
  and fillets the lip and the wall-to-flange junction; `extra_facts` publishes what the
  verifiers read.
- `default_contract.json`: a 135 g mechanical budget (150 g total), a 140 x 140 x 90 mm envelope
  and zero electronics and firmware budgets.
- `intake.yaml`: "fits a 70 mm propeller with 1-2 mm tip clearance, 4 x M4 flange carrying 15 N
  of thrust at safety factor 3, under 150 g" becomes requirements. The propeller, the bolts and
  the thrust go into the statements, because the requirement schema has no field for them.
- `scenarios/`: `thrust_surge` (flange load x 2) and `debris_and_swell` (a 0.1 mm inward
  swelling shift of the throat). They name checks, not requirement ids.

## The profile and how it is sized

The inner surface is a flared inlet that contracts at the pack's inlet half-angle to a short
straight land at the propeller plane, then a diffuser at the pack's diffuser half-angle to the
exit; the outside follows it at a constant wall; the flange sits at the exit end. The numbers
that make it Kort-nozzle-like are all pack parameters: length over throat diameter 0.5, the
plane 0.45 of the way along, a land of 0.12 propeller diameters, 15 degree inlet, 7 degree
diffuser.

- **Throat** = propeller + 2 x tip clearance. The clearance is the middle of the requirement's
  band after the scenario's swelling shift: `(min + shift + max) / 2` on a 0.05 mm grid; with one
  bound only, `margin` inside it.
- **Wall** = the larger of the pack's 1.6 mm and the wall requirements, on a 0.4 mm line grid.
- **Flange**: bolts from any requirement ("4 x M4"), else the pack's default; clearance hole =
  bolt + 0.5 mm; width = hole + 2 x edge; thickness from the thrust if a requirement names one
  (`sigma = 6 M / (b h^2)`, with the thrust x the scenario factor acting half a flange width
  from the bolt line over the net section between the holes), never below 0.8 x the bolt size
  or the wall.

A duct that does not fit the contract's envelope raises a change request on
`budgets.mechanical.outer_envelope_mm`; a mass over budget raises one on
`budgets.mechanical.mass_g`.

## Checks

- `verifiers/prop_clearance.py` (new, registered in the intake registry, with a canary):
  `clearance = (duct inner diameter - propeller diameter) / 2` must satisfy the requirement's
  `min` and `max`, and `clearance - shift` must still satisfy `min`. The propeller diameter
  comes from the requirement statement, the throat from the mechanical interface, the shift from
  the pack's scenario files. Evidence level `calculated`.
- `bending_stress` (from the bracket pack, extended): a requirement that states a force with no
  distance takes its lever from the mechanical fact `bending_lever_mm`. The duct publishes the
  flange's net section at the bolt line and the flange half-width as the lever.
- `mass_budget`, `print_bed_fit` and `wall_thickness_min` are reused as they are.

The flange is checked with the closed-form beam check, not `stress_safety_factor`: the duct has
no pressure or thrust field solution behind it (the finite element solver loads hydrostatic
pressure on a vessel), so the honest, simplest model is a beam.

## Status (honest)

- The throat clearance is geometric: blade deflection, propeller runout and the thruster's own
  mounting tolerance are not modelled. The 0.1 mm swelling shift is an assumption, not a moisture
  measurement; the 2 x thrust surge is an allowance, not a hydrodynamic calculation.
- The flange check is a beam over the net section between bolt holes, with the whole thrust
  carried by the flange. It ignores how the duct actually loads its supports (a Kort nozzle
  carries part of the total thrust), prying of the bolts and peel at the layer lines.
- On the example the flange thickness is set by the 0.8 x bolt rule, not stress: the safety
  factor comes out near 48 against 3 asked for.
- No flow simulation: the nozzle profile is a plausible Kort-like shape from pack parameters,
  not an optimised one, and nothing here predicts thrust or efficiency.
