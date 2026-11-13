# Buoy electronics catalogue

## Page 1 — design assumptions and provenance

`facts.toml` is the allow-list for the sprint's analytical design. A source string
names the component datasheet and pages. `sources` maps **every numeric component
field** to its particular source or explicitly identified engineering allowance.
Missing, blank, or incomplete provenance is rejected. Approval here means inclusion
in this design catalogue, not manufacturer approval or physical qualification.

The default operating profile samples for 0.003 of elapsed time, transmits for
0.0005, and sleeps for 0.9965. These are **design assumptions**, not inferred
product requirements. MCU and radio current are mode dependent; the MAX4466 and
regulator remain powered during sleep. ESP32 Wi-Fi/Bluetooth are disabled. The
MCU figure uses the chip's 240 MHz modem-sleep upper current and ULP deep-sleep
current; it is not a measured module current. A continuous **0.2 mA** support
allowance covers bias resistors and average external-memory overhead. Firmware
must power down unused flash/PSRAM and honor the duty profile. Sustained recording,
radio receive windows, wake-up energy beyond that allowance, and temperature
effects need a revised profile and validation.

Regulator efficiency is assumed to be **0.85** (not a guaranteed low-load minimum).
The Pololu datasheet advertises typical efficiency above 90%; the lower modelling
value still requires measurement at this duty profile. Battery energy uses
**minimum**, not typical, capacity multiplied by nominal voltage; only **0.8** is
usable, reserving capacity for ageing, retention and cutoff effects. A bare cell's
active/sleep current fields are **zero parasitic electronic load**, not its allowed
discharge current. Consumers determine the load; self-discharge is covered by the
capacity reserve. The catalogue does not include a protection or charging circuit;
these must be designed before hardware manufacture.

`board_mm = [70, 40, 8]` is the proposed populated carrier-board envelope. The
modules sit on that board, with the battery underneath and **2 mm** of assembly
clearance. Fit uses the complete stack, and allows in-plane battery rotation.
The **12 g** support-assembly allocation includes PCB, passives, antenna, wiring
and connectors, and is included in mass and BOM totals. It is an engineering
allocation, not a CAD measurement. **0.1 g** for the MAX4466 is a provisional
engineering mass allowance because the referenced manufacturer datasheet does not
specify a package mass. It must be replaced with a qualified supplier declaration
or measurement before treating the mass budget as verified. Other dimensions and
masses come from the linked datasheets or explicitly named product specifications.
Battery body dimensions exclude unfolded solder tabs; the assembly allowance
assumes folded, insulated tab terminations.

The circuit uses a non-inverting MAX4466 stage with 100 kOhm/10 kOhm feedback
(nominal gain 11), a 1 MOhm/1 MOhm midpoint divider, 1 MOhm input bias resistor,
1 uF AC coupling and midpoint bypass, a 10 kOhm/1 uF MCU enable network, and
100 nF plus 10 uF supply decoupling. These values are design choices based on the
MAX4466 typical applications and ESP32 peripheral schematic, not purchased-part
measurements. ERC checks connectivity and drive classes; it does not prove analog
gain, bandwidth, stability, RF matching, layout, or battery safety.

## Source documents

Page numbers refer to printed PDF pages. HTML supplements use the named section
because they have no fixed printed pagination.

- [Espressif ESP32-WROVER-B datasheet v2.4](https://www.espressif.com/sites/default/files/documentation/esp32-wrover-b_datasheet_en.pdf),
  pp. 9–10 pin definitions, p. 27 supply, pp. 28–29 memory, p. 38 dimensions.
  [Adafruit 3979 Technical Details](https://www.adafruit.com/product/3979) supplies
  the 2.4 g module mass and 31.5 × 18 × 3.3 mm dimensions.
- [Espressif ESP32 Series datasheet v5.3](https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf),
  p. 30 Table 4-2: 68 mA upper modem-sleep current, 150 uA ULP deep sleep.
- [HopeRF RFM95W/96W/98W datasheet V2.0](https://cdn.sparkfun.com/assets/a/9/6/1/0/RFM95W-V2.0.pdf),
  pp. 10–11 pins, pp. 12–13 electrical characteristics, p. 122 outline.
  [Seeed RFM95 Technical Details](https://www.seeedstudio.com/RFM95-Ultra-long-Range-Transceiver-Module-LoRa-Module-support-868M-frequency-p-2807.html)
  gives 2 g gross mass, used conservatively; its shipping dimensions are not used.
- [Analog Devices MAX4465–MAX4469 Rev. 2](https://www.analog.com/media/en/technical-documentation/data-sheets/MAX4465-MAX4469.pdf),
  pp. 1–2 pins/supply/current, p. 11 package selection, and
  [SOT23-5 outline 21-0057, p. 1](https://mds.analog.com/api/public/content/sot-23_21-0057.pdf).
- [Pololu S7V8F3 specifications](https://www.pololu.com/product/2122/specs),
  Dimensions, General specifications, and no-load current note.
- [EEMB LP402030 H2213](https://eemb.oss-accelerate.aliyuncs.com//uploads/20230309/c67568920283be08e6dc2fcfb59fcdf7.pdf),
  pp. 3–4 electrical, mass and dimensions; p. 5 retention.
- [EEMB LP503450 H0750](https://eemb.oss-accelerate.aliyuncs.com//uploads/20230311/9b4528f45ac3cb1d13483e978793975b.pdf),
  pp. 3–4 electrical, mass and dimensions; p. 5 retention.
- [EEMB LP505464 H2072](https://eemb.oss-accelerate.aliyuncs.com//uploads/20230315/858b4689fd14fd5e3731f6f44fb35cf5.pdf),
  pp. 3–4 electrical, mass and dimensions; p. 5 retention.

## Stage behavior

`generate()` requires the architect's `contract.json` and an approved
`battery_capacity` requirement. It snapshots the selected IDs, all duty fractions,
source facts and proposed envelope in `selection.yaml`, and copies a runnable
SKiDL script into `src/electronics/circuit.py`. Build uses only those generated
inputs; it neither reselects parts nor mutates the contract.

Smallest battery means lowest body volume, then capacity, mass and ID. If no
endurance-capable assembly fits, the fallback minimizes required bay volume
among capable cells and submits one open change request. Repeated identical
generation reuses the existing open request. If no cell has enough capacity,
the highest-endurance fallback exposes the shortfall; changing the bay alone
cannot solve an impossible capacity requirement.

The minimal contract specifies `HYDROPHONE_ADC` and `LORA_CS`. Optional contract
signals are `LORA_SCK`, `LORA_MOSI`, `LORA_MISO`, `LORA_RESET`, and `LORA_DIO0`.
Absent SPI/reset/interrupt signals use documented circuit-local defaults on
GPIO18/23/19/27/26, returned separately as `internal_pins`. `pins_used` contains
exactly the contract signals, derived from their actual SKiDL connections.
Unknown, duplicate, unsupported or conflicting pin assignments fail explicitly.

Build runs SKiDL ERC and exports a KiCad netlist using local symbol definitions.
No installed KiCad libraries are needed. Stable references/tags, a fixed serializer
date, normalized source metadata and sorted output prevent volatile netlist hashes.
`erc_errors` counts SKiDL errors **and warnings**, so an undriven or disconnected
power pin cannot be reported clean. The BOM includes the selected components and
the support-assembly allocation. This is an executable connectivity model, not a
routed PCB; custom module footprints and protection circuits need hardware review.

Run `uv run pytest tests/electronics -q` and `uv run ruff check .` from the repo root.
The scaffold smoke tests still assert that all stages have empty check lists and
produce placeholders; their owner must update those tests and supply an architect
contract for the real pipeline.
