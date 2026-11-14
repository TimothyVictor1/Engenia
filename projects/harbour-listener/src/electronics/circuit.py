"""Generated SKiDL circuit; run with Python circuit.py <build-directory>.

Local symbols use datasheet pad numbers; no machine-wide KiCad library is needed.
The netlist is a connectivity design, not a routed PCB or a fabrication release.
"""

from __future__ import annotations

import importlib
import io
import json
import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml
from skidl import SKIDL, Circuit, Net, Part, Pin
from skidl.logger import erc_logger
from skidl.pin import pin_types as T

GPIO_PADS = {
    "GPIO36": "4",
    "GPIO39": "5",
    "GPIO34": "6",
    "GPIO35": "7",
    "GPIO32": "8",
    "GPIO33": "9",
    "GPIO25": "10",
    "GPIO26": "11",
    "GPIO27": "12",
    "GPIO14": "13",
    "GPIO12": "14",
    "GPIO13": "16",
    "GPIO15": "23",
    "GPIO2": "24",
    "GPIO0": "25",
    "GPIO4": "26",
    "GPIO5": "29",
    "GPIO18": "30",
    "GPIO19": "31",
    "GPIO21": "33",
    "GPIO3": "34",
    "GPIO1": "35",
    "GPIO22": "36",
    "GPIO23": "37",
}
DEFAULT_INTERNAL = {
    "LORA_SCK": "GPIO18",
    "LORA_MISO": "GPIO19",
    "LORA_MOSI": "GPIO23",
    "LORA_RESET": "GPIO27",
    "LORA_DIO0": "GPIO26",
}
RADIO_SIGNALS = {
    "LORA_CS": ("5", "NSS", T.INPUT),
    "LORA_SCK": ("4", "SCK", T.INPUT),
    "LORA_MOSI": ("3", "MOSI", T.INPUT),
    "LORA_MISO": ("2", "MISO", T.OUTPUT),
    "LORA_RESET": ("6", "RESET", T.INPUT),
    "LORA_DIO0": ("14", "DIO0", T.OUTPUT),
}


def make_part(circuit, name, ref, pins, footprint):
    return Part(
        name=name,
        tool=SKIDL,
        ref=ref,
        tag=ref,
        circuit=circuit,
        value=name,
        footprint=footprint,
        pins=[Pin(num=num, name=pin_name, func=kind) for num, pin_name, kind in pins],
    )


def create_circuit(selection):
    pins = selection["pins_used"]
    if not {"HYDROPHONE_ADC", "LORA_CS"} <= pins.keys():
        raise ValueError("pin_map requires HYDROPHONE_ADC and LORA_CS")
    if set(pins) - ({"HYDROPHONE_ADC"} | RADIO_SIGNALS.keys()):
        raise ValueError("pin_map has an unsupported electronics signal")
    internal = {key: value for key, value in DEFAULT_INTERNAL.items() if key not in pins}
    wiring = {**internal, **pins}
    if len(set(wiring.values())) != len(wiring):
        raise ValueError("pin_map aliases signals or conflicts with internal SPI/reset pins")
    if any(pin not in GPIO_PADS for pin in wiring.values()):
        raise ValueError("pin_map uses an unavailable ESP32-WROVER-B GPIO")
    if pins["HYDROPHONE_ADC"] not in {"GPIO32", "GPIO33", "GPIO34", "GPIO35", "GPIO36", "GPIO39"}:
        raise ValueError("HYDROPHONE_ADC must use an ADC1 pin")
    c = Circuit()
    c.no_files = True
    c.track_src = False
    c.track_abs_path = False
    parts = selection["parts"]
    # These symbol models are intentionally tied to the approved catalogue.
    supported = {
        "microcontroller": "ESP32-WROVER-B",
        "lora": "RFM95W-868S2",
        "audio": "MAX4466EUK",
        "regulator": "S7V8F3",
    }
    if any(parts[role] != part for role, part in supported.items()):
        raise ValueError("selected part has no approved symbol model")
    mcu_pins = [
        ("1", "GND", T.PWRIN),
        ("2", "VCC", T.PWRIN),
        ("3", "EN", T.INPUT),
        ("15", "GND2", T.PWRIN),
        ("38", "GND3", T.PWRIN),
    ]
    for signal, gpio in sorted(wiring.items()):
        is_input = signal in {"HYDROPHONE_ADC", "LORA_MISO", "LORA_DIO0"}
        if not is_input and gpio in {"GPIO34", "GPIO35", "GPIO36", "GPIO39"}:
            raise ValueError(f"{signal}: input-only GPIO cannot drive a radio input")
        mcu_pins.append((GPIO_PADS[gpio], gpio, T.INPUT if is_input else T.OUTPUT))
    mcu = make_part(c, parts["microcontroller"], "U1", mcu_pins, "RF_Module:ESP32-WROVER")
    radio = make_part(
        c,
        parts["lora"],
        "U2",
        [
            ("1", "GND", T.PWRIN),
            ("8", "GND2", T.PWRIN),
            ("10", "GND3", T.PWRIN),
            ("13", "VCC", T.PWRIN),
            ("9", "ANT", T.PASSIVE),
            *RADIO_SIGNALS.values(),
        ],
        "RF_Module:HOPERF_RFM9XW",
    )
    audio = make_part(
        c,
        parts["audio"],
        "U3",
        [
            ("1", "OUT", T.OUTPUT),
            ("2", "GND", T.PWRIN),
            ("3", "INP", T.INPUT),
            ("4", "INM", T.INPUT),
            ("5", "VCC", T.PWRIN),
        ],
        "Package_TO_SOT_SMD:SOT-23-5",
    )
    reg = make_part(
        c,
        parts["regulator"],
        "U4",
        [
            ("VIN", "VIN", T.PWRIN),
            ("GND", "GND", T.PWRIN),
            ("VOUT", "VOUT", T.PWROUT),
            ("SHDN", "SHDN", T.INPUT),
        ],
        "Module:Pololu_S7V8F3",
    )
    battery = make_part(
        c,
        parts["battery"],
        "BT1",
        [
            ("1", "POS", T.PWROUT),
            ("2", "NEG", T.PWROUT),
        ],
        "Connector_Wire:SolderWire-1x02_P3.81mm_D0.8mm_OD1.6mm",
    )
    gnd, vbat, vcc = [Net(name, circuit=c) for name in ("GND", "VBAT", "VCC_3V3")]
    gnd += battery["NEG"], reg["GND"], mcu["GND"], mcu["GND2"], mcu["GND3"]
    gnd += radio["GND"], radio["GND2"], radio["GND3"], audio["GND"]
    vbat += battery["POS"], reg["VIN"], reg["SHDN"]
    vcc += reg["VOUT"], mcu["VCC"], radio["VCC"], audio["VCC"]
    nets = {}
    for signal, gpio in sorted(wiring.items()):
        net = nets[signal] = Net(signal, circuit=c)
        net += mcu[gpio]
        net += audio["OUT"] if signal == "HYDROPHONE_ADC" else radio[RADIO_SIGNALS[signal][1]]

    def passive(ref, value, a, b, footprint="Resistor_SMD:R_0603_1608Metric"):
        part = make_part(c, value, ref, [("1", "A", T.PASSIVE), ("2", "B", T.PASSIVE)], footprint)
        a += part["1"]
        b += part["2"]

    def capacitor(ref, value, a, b):
        passive(ref, value, a, b, "Capacitor_SMD:C_0603_1608Metric")

    bias, feedback, input_net, audio_plus, en = [
        Net(name, circuit=c)
        for name in ("AUDIO_BIAS", "AUDIO_FB", "HYDROPHONE_IN", "AUDIO_PLUS", "MCU_EN")
    ]
    audio_plus += audio["INP"]
    feedback += audio["INM"]
    en += mcu["EN"]
    passive("R1", "1Meg", vcc, bias)
    passive("R2", "1Meg", bias, gnd)
    passive("R3", "100k", nets["HYDROPHONE_ADC"], feedback)
    passive("R4", "10k", feedback, bias)
    passive("R5", "10k", vcc, en)
    passive("R6", "1Meg", bias, audio_plus)
    capacitor("C1", "1uF", input_net, audio_plus)
    capacitor("C2", "100nF", vcc, gnd)
    capacitor("C3", "10uF", vcc, gnd)
    capacitor("C4", "1uF", en, gnd)
    capacitor("C5", "1uF", bias, gnd)
    hydrophone = make_part(
        c,
        "HYDROPHONE",
        "J1",
        [
            ("1", "SIGNAL", T.PASSIVE),
            ("2", "GND", T.PASSIVE),
        ],
        "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    )
    input_net += hydrophone["SIGNAL"]
    gnd += hydrophone["GND"]
    antenna = make_part(
        c,
        "ANTENNA",
        "J2",
        [
            ("1", "RF", T.PASSIVE),
            ("2", "GND", T.PASSIVE),
        ],
        "Connector_Coaxial:U.FL_Hirose_U.FL-R-SMT-1_Vertical",
    )
    rf = Net("RF_ANT", circuit=c)
    rf += antenna["RF"], radio["ANT"]
    gnd += antenna["GND"]
    actual_pins = {}
    for signal, gpio in pins.items():
        pin = mcu[gpio]
        if pin.net.name != signal:
            raise ValueError(f"{signal}: generated connection differs from contract")
        actual_pins[signal] = pin.name
    return c, actual_pins, internal


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    selection = yaml.safe_load(
        Path(__file__).with_name("selection.yaml").read_text(encoding="utf-8")
    )
    circuit, pins, internal = create_circuit(selection)
    capture = io.StringIO()
    handler = logging.StreamHandler(capture)
    erc_logger.addHandler(handler)
    try:
        circuit.ERC()
        errors, warnings = erc_logger.error.count, erc_logger.warning.count
    finally:
        erc_logger.removeHandler(handler)
    # Sort diagnostics because SKiDL visits nets through a set.
    messages = sorted(line for line in capture.getvalue().splitlines() if line.strip())
    (output / "erc.txt").write_text(
        "\n".join(messages) + f"\nerrors: {errors}\nwarnings: {warnings}\n", encoding="utf-8"
    )
    (output / "erc.json").write_text(
        json.dumps(
            {
                "errors": errors,
                "warnings": warnings,
                "pins_used": pins,
                "internal_pins": internal,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    # SKiDL 2.2.3 embeds wall-clock time unconditionally. Replace only its
    # serializer's clock in this isolated process, never the application's clock.
    serializer = importlib.import_module("skidl.tools.kicad9.gen_netlist")
    serializer.time = SimpleNamespace(strftime=lambda _: "1970-01-01T00:00:00Z")
    netlist = circuit.generate_netlist(
        tool="kicad9", do_backup=False, track_src=False, track_abs_path=False
    )
    # Normalize serializer source metadata across Windows/POSIX and checkout paths.
    netlist = re.sub(r'\(source "[^"]*"\)', '(source "circuit.py")', netlist)
    (output / "circuit.net").write_text(netlist, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
