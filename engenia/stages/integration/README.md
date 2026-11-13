# Integration facts

`IntegrationStage` has no generation step and runs its checks directly during
build. `checks` is empty because these are package functions, not verifier files.

Upstream `interface.json` envelopes carry these fields inside `facts`:

- Mechanical: `dry_mass_g`, `battery_bay_mm`, `centre_of_mass_mm`,
  `battery_bay_centre_mm`, `displaced_volume_cm3`, `contract_version`.
- Electronics: `mass_g` (including battery), `board_mm`, `battery_mm`,
  `board_holes_mm`, `pins_used`, `contract_version`.
- Firmware: `pins_used`, `contract_version`.

Dimensions and positions are three-element arrays in mm; positions share the
mechanical coordinate frame. The electronics mass is concentrated at
`battery_bay_centre_mm`. Missing assembly inputs produce null derived values;
no bay position or contract version is inferred. Each component is checked
against the bay with 1 mm clearance on each side along all three axes.
This bounding-box check does not prove that components fit simultaneously.

`failed_checks` includes both failures and errors. `mismatches.json` is always
rewritten, including an empty list on success. Each record contains `check`,
`stages`, and `message`. Contract-version mismatches name only stages whose
version is missing, stubbed, or differs from the active contract. Other records
name the upstream stages supplying the compared facts. The contract itself is
never a rerun target.

Every pass/fail verdict has numeric evidence so gate G2 can recompute it.
Mount, pin, and contract-version comparisons report 1.0 for equality and 0.0
for a mismatch, with `equals: 1`. Bay fit reports the smallest per-side
clearance, `min((bay_dimension - component_dimension) / 2)`, across both
components and all three axes, with `min: 1.0` mm. Mass reports the assembly
mass in grams against the contract maximum. Missing facts remain error
verdicts with a null measurement.
