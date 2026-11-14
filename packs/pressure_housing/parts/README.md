# pressure_housing parts

## `facts.toml`: electronics catalogue

Every part entry is copied unchanged, with its field-level sources, from the buoy pack
(ESP32-WROVER-B, MAX4466EUK as the hydrophone preamp, S7V8F3, the EEMB cells) or the enclosure
pack (BME280). See `packs/buoy/parts/README.md` and `packs/enclosure/parts/README.md` for the
datasheet pages. Only the `[design]` profile is the pod's own:

- duty cycle 0.003 sample, 0 transmit, 0.997 sleep. There is no radio, so no transmit time.
- the hydrophone channel stays powered through sleep so events are caught; the MCU and the
  environment sensor wake for a sample burst.
- `board_mm = [60, 32, 8]`: a 60 mm sensor carrier board. The default contract's bay is
  64 x 36 x 22 mm, the board plus battery stack with 2 mm of assembly clearance.
- 8 g of support assembly (PCB, passives, wiring) and 0.2 mA of support current are design
  allowances, not measurements.

## `o_rings.toml`: the standard O-ring table

Metric ID x cross-section sizes, NBR 70 Shore A, density 1.25 g/cm3 for the ring mass. Groove
design rules (static radial seal, squeeze 15 to 30 %, stretch up to 5 %) follow the Parker
O-Ring Handbook; the nominal size series follows ISO 3601-1. These are catalogue sizes, not a
quotation: confirm a size with the supplier before buying.
