# Deck-sensor electronics catalogue

## Page 1 — design assumptions and provenance

`facts.toml` is the allow-list for the sprint's analytical design. A source string
names the component datasheet and pages. `sources` maps **every numeric component
field** to its particular source or explicitly identified engineering allowance.
Missing, blank, or incomplete provenance is rejected. Approval here means inclusion
in this design catalogue, not manufacturer approval or physical qualification.

The default operating profile samples for 0.002 of elapsed time, transmits for
0.0005, and sleeps for 0.9975. These are **design assumptions**, not inferred
product requirements. The `active_modes` matrix says which functions draw active
current in which mode: the vibration channel (piezo disc and its MAX4466 preamp)
stays powered through sleep so event detection keeps working, the environment
sensor is read once per sample burst, the MCU sleeps between bursts, and the radio
wakes only to transmit. ESP32 Wi-Fi/Bluetooth are disabled. The MCU figure uses the
chip's 240 MHz modem-sleep upper current and ULP deep-sleep current; it is not a
measured module current. A continuous **0.2 mA** support allowance covers bias
resistors and average external-memory overhead. Firmware must power down unused
flash/PSRAM and honor the duty profile. Sustained recording, radio receive windows,
wake-up energy beyond that allowance, and temperature effects need a revised
profile and validation.

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
allocation, not a CAD measurement. **0.1 g** for the MAX4466 and **0.05 g** for the
BME280 are provisional engineering mass allowances because the referenced
manufacturer datasheets do not specify package masses. They must be replaced with
qualified supplier declarations or measurements before treating the mass budget as
verified. Other dimensions and masses come from the linked datasheets or explicitly
named product specifications. Battery body dimensions exclude unfolded solder
tabs; the assembly allowance assumes folded, insulated tab terminations.

The vibration channel is a piezo disc into the same non-inverting MAX4466 stage the
buoy uses for its hydrophone: 100 kOhm/10 kOhm feedback (nominal gain 11), a
1 MOhm/1 MOhm midpoint divider, 1 MOhm input bias resistor, 1 uF AC coupling and
midpoint bypass, a 10 kOhm/1 uF MCU enable network, and 100 nF plus 10 uF supply
decoupling. The BME280 hangs on the I2C bus (internal ESP32 defaults GPIO21/22) in
I2C mode with CSB tied high and SDO low (address 0x76), plus 100 nF decoupling. The
radio matches the buoy's RFM95W wiring. These values are design choices based on
typical applications and module reference schematics, not purchased-part
measurements. The piezo disc's sensitivity and capacitance are not characterized
here; the firmware detector works on normalized ADC units, and its detection-rate
and false-alarm numbers are validated on the seeded synthetic dataset, not on field
recordings. ERC checks connectivity and drive classes; it does not prove analog
gain, bandwidth, stability, RF matching, layout, or battery safety.

## Source documents

Page numbers refer to printed PDF pages. HTML supplements use the named section
because they have no fixed printed pagination. Parts shared with the buoy catalogue
(`packs/buoy/parts/README.md`) keep the same sources.

- [Bosch BME280 datasheet BST-BME280-DS002](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf),
  pp. 11–14 electrical characteristics (supply range, 3.4 uA at 1 Hz normal mode,
  0.1 uA sleep), pp. 28–29 pin description, p. 47 mechanical data
  (2.5 × 2.5 × 0.93 mm LGA-8 with metal lid).
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
