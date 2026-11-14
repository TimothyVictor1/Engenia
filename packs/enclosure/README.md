# packs/enclosure

Product pack for a deck-mounted sensor box (temperature, humidity, vibration). Same file
shape as `packs/buoy/`: a `pack.yaml` (materials, printing, templates, firmware detector
family), a build123d template under `templates/`, and a parts catalogue under `parts/`.

- `pack.yaml` declares the `deck_enclosure` template and the checks it is measured with,
  and names the `threshold` firmware detector family (a block-RMS level sampler; the buoy
  pack uses the tone detector).
- `templates/deck_enclosure.py` builds a rectangular gasketed enclosure
  (`DeckEnclosureParams`, `build_deck_enclosure -> (body, lid)`), with the same parameter
  names the mechanical stage proposes.
- `parts/facts.toml` is the sourced electronics catalogue: ESP32-WROVER-B, RFM95W-868S2,
  BME280 environment sensor, MAX4466EUK as the piezo vibration preamp, S7V8F3 regulator
  and the EEMB cells. `parts/README.md` holds the design assumptions and provenance.
- `default_contract.json` pins the product signals: `SENSOR_SDA`/`SENSOR_SCL` (I2C bus,
  internal GPIO21/22 defaults), `VIBRATION_ADC` (ADC1 pin for the piezo channel) and
  `LORA_CS`. No hydrophone signal: this product does not float and has no audio channel.

## What reads this pack

A project opts in with `pack: enclosure` in `projects/<p>/project.yaml`. The architect
loads `default_contract.json` and shows this `pack.yaml` to the design model.
Mechanical builds the `deck_enclosure` body and lid, electronics loads
`parts/facts.toml`, and firmware copies the `threshold` detector sources.

## Status (honest)

The detection-rate and false-alarm numbers are validated on the seeded synthetic dataset
(`tools/make_data.py` in the firmware templates), not on field recordings, and the piezo
disc's sensitivity is not characterized; the detector works on normalized ADC units.
Masses for the BME280 and MAX4466 are provisional engineering allowances, not vendor
masses. See `parts/README.md` for the full limitation list.
