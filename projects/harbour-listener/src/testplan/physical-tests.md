# Physical test plan: harbour-listener

Generated from the approved requirements. Each test names the requirement it checks, the instrument, the procedure, the expected value and the pass limit. A measured number replaces the calculated one only when it is recorded with `engenia measure`.

## T-001 Mass of the assembled prototype (R-001)

Requirement: The total mass of the buoy shall not exceed 400 g.

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

Requirement: The hull shall fit within the 220 x 220 x 250 mm print bed.

- **Instrument:** Calipers or steel ruler, 0.1 mm resolution; the printer's stated bed size
- **Expected value / pass limit:** = 1 fits
- **Pass rule:** Pass when the measured value is = 1 fits.

Procedure:

1. Measure the printed hull's three bounding dimensions in millimetres. Do this on the printed part, not on the CAD file.
2. Sort the three dimensions and the three bed dimensions in ascending order.
3. The part fits if each sorted dimension is no larger than the matching bed dimension.
4. Record the result as 1 (fits) or 0 (does not fit), with the dimensions.

## T-003 Battery endurance (R-003)

Requirement: The buoy shall operate for at least 7 days on one battery charge.

- **Instrument:** Inline power meter or source meter (current to 0.01 mA, logging), the real battery (rated capacity from its datasheet), a thermometer
- **Expected value / pass limit:** >= 7 day
- **Pass rule:** Pass when the measured value is >= 7 day.

Procedure:

1. Fully charge the battery. Put the power meter in series with the battery.
2. Run the firmware in its normal duty cycle at about 20 C for at least 24 hours, logging current and voltage.
3. Compute the average power in mW over the whole log, including every sleep and transmit period.
4. Endurance in days = battery Wh x 1000 / average power mW / 24 x 0.8, where 0.8 is the derating the design checks use.
5. For a final proof, run the unit on the battery until it stops and record the days.

## T-005 Buoyancy margin (R-005)

Requirement: The buoy shall have a buoyancy margin of at least 0.20.

- **Instrument:** Digital scale (0.1 g), vessel big enough to submerge the prototype, thermometer, hydrometer
- **Expected value / pass limit:** >= 0.2 ratio
- **Pass rule:** Pass when the measured value is >= 0.2 ratio.

Procedure:

1. Weigh the assembled, sealed prototype dry: total mass m in grams.
2. Fill the vessel with water and measure its density in g/cm3 (hydrometer).
3. Put the vessel on the scale and zero it. Push the prototype fully under water with a thin rod, without touching the vessel. Do not let it touch the bottom.
4. Read the scale: the added grams divided by the water density is the displaced volume V in cm3.
5. Compute margin = (V x density - m) / m. Repeat three times and use the mean.
6. Keep the prototype under water for 10 minutes and check the inside for leaks.

## T-006 Metacentric height by inclining test (R-006)

Requirement: The buoy shall float upright with a metacentric height of at least 5 mm.

- **Instrument:** Calm water tank, digital inclinometer (0.1 degree), a small known mass (about 5 g), ruler
- **Expected value / pass limit:** >= 5 mm
- **Pass rule:** Pass when the measured value is >= 5 mm.

Procedure:

1. Float the assembled prototype upright in calm water and let it settle.
2. Place the known mass w (grams) on the lid at a measured distance d (mm) from the axis.
3. Read the heel angle theta once it settles. Use a small angle, below 10 degrees.
4. Compute GM = w x d / (M x tan(theta)) in mm, where M is the total mass of the prototype in grams, including w.
5. Repeat with the mass on the opposite side and use the mean of the two GM values.

## T-011 Tilt in waves (R-011)

Requirement: The buoy shall keep its tilt within 30 degrees in 0.5 m significant wave height.

- **Instrument:** Wave tank or sheltered water with a known wave height, wave staff or ruler, inclinometer (logging) or a fixed camera with a protractor overlay
- **Expected value / pass limit:** <= 30 deg
- **Pass rule:** Pass when the measured value is <= 30 deg.

Procedure:

1. Float the prototype free in the tank at the wave height named in the requirement.
2. Run waves at the scenario period for at least 20 wave cycles.
3. Record the maximum tilt from vertical in degrees from the inclinometer log or video.
4. Repeat at every wave height in the requirement and keep the largest reading.

## Not covered by a physical test

- R-004 (wall_thickness_min): needs a cut section of the print; measure it from a sacrificial part
- R-007 (power_budget): covered indirectly by the battery endurance test (average power is logged)
- R-008 (erc_clean): an electrical rule check of the netlist, not a physical measurement
- R-009 (detection_rate): software behaviour, shown by replaying audio, not at the bench
- R-010 (false_alarm_rate): software behaviour, shown by replaying audio, not at the bench
- R-012 (stress_safety_factor): needs a pressure rig and a destructive test, not a bench measurement
