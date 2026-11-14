# Physical test plan: sensor-pod

Generated from the approved requirements. Each test names the requirement it checks, the instrument, the procedure, the expected value and the pass limit. A measured number replaces the calculated one only when it is recorded with `engenia measure`.

## T-001 Mass of the assembled prototype (R-001)

Requirement: The total mass of the pod shall not exceed 250 g.

- **Instrument:** Digital scale, 0.1 g resolution or better, calibrated with a known mass
- **Expected value / pass limit:** <= 250 g
- **Pass rule:** Pass when the measured value is <= 250 g.
- **Recorded with:** `engenia measure` option `--mass-g`

Procedure:

1. Assemble the prototype as it will be deployed: hull, lid, electronics, battery, seals.
2. Zero the scale. Place the assembled prototype on it and wait for a stable reading.
3. Record the mass in grams. Repeat three times, removing the prototype each time.
4. Use the mean of the three readings as the measured mass.
5. Record it with `engenia measure <project> --mass-g <value> --waterline-mm <value>`.

## T-004 Buoyancy margin (R-004)

Requirement: The pod shall stay near neutral buoyancy, with a buoyancy margin of at least -0.05.

- **Instrument:** Digital scale (0.1 g), vessel big enough to submerge the prototype, thermometer, hydrometer
- **Expected value / pass limit:** >= -0.05 ratio
- **Pass rule:** Pass when the measured value is >= -0.05 ratio.

Procedure:

1. Weigh the assembled, sealed prototype dry: total mass m in grams.
2. Fill the vessel with water and measure its density in g/cm3 (hydrometer).
3. Put the vessel on the scale and zero it. Push the prototype fully under water with a thin rod, without touching the vessel. Do not let it touch the bottom.
4. Read the scale: the added grams divided by the water density is the displaced volume V in cm3.
5. Compute margin = (V x density - m) / m. Repeat three times and use the mean.
6. Keep the prototype under water for 10 minutes and check the inside for leaks.

## T-005 Electronics and battery fit the bay (R-005)

Requirement: The electronics assembly shall fit the pod's electronics bay, which is sized for a sensor board of up to 60 mm.

- **Instrument:** Calipers, 0.1 mm resolution; the printed hull and the real board and battery
- **Expected value / pass limit:** = 1 ratio
- **Pass rule:** Pass when the measured value is = 1 ratio.

Procedure:

1. Measure the bay in the printed hull, and the board and the battery, in millimetres.
2. Place the board, then the battery, in the bay in the orientation used in the design. Check that nothing is forced and the lid closes.
3. Compare the sorted dimensions of each part with the sorted dimensions of the bay.
4. Record the result as 1 (both fit) or 0 (either does not), with the dimensions.

## T-006 Printed hull fits the print bed (R-006)

Requirement: The pod shall fit within the print bed.

- **Instrument:** Calipers or steel ruler, 0.1 mm resolution; the printer's stated bed size
- **Expected value / pass limit:** = 1 ratio
- **Pass rule:** Pass when the measured value is = 1 ratio.

Procedure:

1. Measure the printed hull's three bounding dimensions in millimetres. Do this on the printed part, not on the CAD file.
2. Sort the three dimensions and the three bed dimensions in ascending order.
3. The part fits if each sorted dimension is no larger than the matching bed dimension.
4. Record the result as 1 (fits) or 0 (does not fit), with the dimensions.

## Not covered by a physical test

- R-002 (stress_safety_factor): needs a pressure rig and a destructive test, not a bench measurement
- R-003 (wall_thickness_min): needs a cut section of the print; measure it from a sacrificial part
