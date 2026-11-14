# Physical test plan: deck-sensor

Generated from the approved requirements. Each test names the requirement it checks, the instrument, the procedure, the expected value and the pass limit. A measured number replaces the calculated one only when it is recorded with `engenia measure`.

## T-001 Mass of the assembled prototype (R-001)

Requirement: The assembled deck sensor shall have a total mass of at most 400 g.

- **Instrument:** Digital scale, 0.1 g resolution or better, calibrated with a known mass
- **Expected value / pass limit:** <= 400 g
- **Pass rule:** Pass when the measured value is <= 400 g.
- **Recorded with:** `engenia measure` option `--mass-g`

Procedure:

1. Assemble the prototype as it will be deployed: hull, lid, electronics, battery, seals.
2. Zero the scale. Place the assembled prototype on it and wait for a stable reading.
3. Record the mass in grams. Repeat three times, removing the prototype each time.
4. Use the mean of the three readings as the measured mass.
5. Record it with `engenia measure <project> --mass-g <value> --waterline-mm <value>`.

## T-002 Printed hull fits the print bed (R-002)

Requirement: The printed enclosure shall fit within the 220 x 220 x 250 mm print bed.

- **Instrument:** Calipers or steel ruler, 0.1 mm resolution; the printer's stated bed size
- **Expected value / pass limit:** = 1 fits
- **Pass rule:** Pass when the measured value is = 1 fits.

Procedure:

1. Measure the printed hull's three bounding dimensions in millimetres. Do this on the printed part, not on the CAD file.
2. Sort the three dimensions and the three bed dimensions in ascending order.
3. The part fits if each sorted dimension is no larger than the matching bed dimension.
4. Record the result as 1 (fits) or 0 (does not fit), with the dimensions.

## T-003 Battery endurance (R-003)

Requirement: The deck sensor shall operate for at least 14 days on one battery charge.

- **Instrument:** Inline power meter or source meter (current to 0.01 mA, logging), the real battery (rated capacity from its datasheet), a thermometer
- **Expected value / pass limit:** >= 14 day
- **Pass rule:** Pass when the measured value is >= 14 day.

Procedure:

1. Fully charge the battery. Put the power meter in series with the battery.
2. Run the firmware in its normal duty cycle at about 20 C for at least 24 hours, logging current and voltage.
3. Compute the average power in mW over the whole log, including every sleep and transmit period.
4. Endurance in days = battery Wh x 1000 / average power mW / 24 x 0.8, where 0.8 is the derating the design checks use.
5. For a final proof, run the unit on the battery until it stops and record the days.

## T-007 Electronics and battery fit the bay (R-007)

Requirement: The electronics assembly shall fit inside the mechanical battery bay.

- **Instrument:** Calipers, 0.1 mm resolution; the printed hull and the real board and battery
- **Expected value / pass limit:** = 1 fits
- **Pass rule:** Pass when the measured value is = 1 fits.

Procedure:

1. Measure the bay in the printed hull, and the board and the battery, in millimetres.
2. Place the board, then the battery, in the bay in the orientation used in the design. Check that nothing is forced and the lid closes.
3. Compare the sorted dimensions of each part with the sorted dimensions of the bay.
4. Record the result as 1 (both fit) or 0 (either does not), with the dimensions.

## Not covered by a physical test

- R-004 (wall_thickness_min): needs a cut section of the print; measure it from a sacrificial part
- R-005 (power_budget): covered indirectly by the battery endurance test (average power is logged)
- R-006 (erc_clean): an electrical rule check of the netlist, not a physical measurement
- R-008 (detection_rate): software behaviour, shown by replaying audio, not at the bench
