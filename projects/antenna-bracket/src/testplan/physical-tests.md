# Physical test plan: antenna-bracket

Generated from the approved requirements. Each test names the requirement it checks, the instrument, the procedure, the expected value and the pass limit. A measured number replaces the calculated one only when it is recorded with `engenia measure`.

## T-002 Mass of the assembled prototype (R-002)

Requirement: The total mass of the bracket shall not exceed 120 g.

- **Instrument:** Digital scale, 0.1 g resolution or better, calibrated with a known mass
- **Expected value / pass limit:** <= 120 g
- **Pass rule:** Pass when the measured value is <= 120 g.
- **Recorded with:** `engenia measure` option `--mass-g`

Procedure:

1. Assemble the prototype as it will be deployed: hull, lid, electronics, battery, seals.
2. Zero the scale. Place the assembled prototype on it and wait for a stable reading.
3. Record the mass in grams. Repeat three times, removing the prototype each time.
4. Use the mean of the three readings as the measured mass.
5. Record it with `engenia measure <project> --mass-g <value> --waterline-mm <value>`.

## T-004 Printed hull fits the print bed (R-004)

Requirement: The bracket shall fit within the print bed.

- **Instrument:** Calipers or steel ruler, 0.1 mm resolution; the printer's stated bed size
- **Expected value / pass limit:** = 1 ratio
- **Pass rule:** Pass when the measured value is = 1 ratio.

Procedure:

1. Measure the printed hull's three bounding dimensions in millimetres. Do this on the printed part, not on the CAD file.
2. Sort the three dimensions and the three bed dimensions in ascending order.
3. The part fits if each sorted dimension is no larger than the matching bed dimension.
4. Record the result as 1 (fits) or 0 (does not fit), with the dimensions.

## Not covered by a physical test

- R-001 (bending_stress): no physical test template for the check 'bending_stress'
- R-003 (wall_thickness_min): needs a cut section of the print; measure it from a sacrificial part
