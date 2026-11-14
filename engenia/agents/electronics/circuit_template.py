"""Generated SKiDL circuit; run with Python circuit.py <build-directory>.

Local symbols use datasheet pad numbers; no machine-wide KiCad library is needed.
The netlist is a connectivity design, not a routed PCB or a fabrication release.

The circuit is assembled from the selection's part functions: an optional radio (lora),
amplifier channels (audio preamp for a hydrophone, vibration_amp for a piezo disc), I2C
environment sensors, the regulator and the battery. Every signal the contract pins must be
wired; internal ESP32 defaults cover the radio SPI/reset/DIO0 lines and the I2C bus unless
the contract pins them.
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
SENSOR_INTERNAL = {"SENSOR_SDA": "GPIO21", "SENSOR_SCL": "GPIO22"}
RADIO_SIGNALS = {
    "LORA_CS": ("5", "NSS", T.INPUT),
    "LORA_SCK": ("4", "SCK", T.INPUT),
    "LORA_MOSI": ("3", "MOSI", T.INPUT),
    "LORA_MISO": ("2", "MISO", T.OUTPUT),
    "LORA_RESET": ("6", "RESET", T.INPUT),
    "LORA_DIO0": ("14", "DIO0", T.OUTPUT),
}
ADC1_PINS = {"GPIO32", "GPIO33", "GPIO34", "GPIO35", "GPIO36", "GPIO39"}
# Amplifier output signals: name -> the part function that must drive them.
ADC_SIGNALS = {"HYDROPHONE_ADC": "audio", "VIBRATION_ADC": "vibration_amp"}
I2C_SIGNALS = ("SENSOR_SDA", "SENSOR_SCL")
# One entry per amplifier channel the catalogue supports: role, transducer connector,
# output signal, and the local net names of its bias network. A project's selection
# picks a subset; the first channel owns the shared MCU_EN strap.
AMPLIFIER_CHANNELS = (
    (
        "audio",
        "HYDROPHONE",
        "HYDROPHONE_ADC",
        {
            "bias": "AUDIO_BIAS",
            "feedback": "AUDIO_FB",
            "input": "HYDROPHONE_IN",
            "plus": "AUDIO_PLUS",
        },
    ),
    (
        "vibration_amp",
        "PIEZO",
        "VIBRATION_ADC",
        {"bias": "VIB_BIAS", "feedback": "VIB_FB", "input": "PIEZO_IN", "plus": "VIB_PLUS"},
    ),
)
SENSOR_SYMBOLS = {"environment_sensor": "BME280"}
AMP_PINS = [
    ("1", "OUT", T.OUTPUT),
    ("2", "GND", T.PWRIN),
    ("3", "INP", T.INPUT),
    ("4", "INM", T.INPUT),
    ("5", "VCC", T.PWRIN),
]
TRANSDUCER_PINS = [
    ("1", "SIGNAL", T.PASSIVE),
    ("2", "GND", T.PASSIVE),
]
SENSOR_PINS = {
    "BME280": (
        [
            ("1", "GND", T.PWRIN),
            ("2", "CSB", T.INPUT),
            ("3", "VCC", T.PWRIN),
            ("4", "SDO", T.INPUT),
            ("5", "SDA", T.INPUT),
            ("6", "SCL", T.INPUT),
            ("7", "VDDIO", T.PWRIN),
            ("8", "GND2", T.PWRIN),
        ],
        "Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_Staggered",
    ),
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
    parts = selection["parts"]
    roles = set(parts)
    # These symbol models are intentionally tied to the approved catalogue.
    supported = {
        "microcontroller": "ESP32-WROVER-B",
        "lora": "RFM95W-868S2",
        "audio": "MAX4466EUK",
        "vibration_amp": "MAX4466EUK",
        "environment_sensor": "BME280",
        "regulator": "S7V8F3",
    }
    unknown = roles - set(supported) - {"battery"}
    if unknown or any(parts[role] != supported[role] for role in roles if role in supported):
        raise ValueError("selected part has no approved symbol model")
    channels = [
        (role, transducer, signal, names)
        for role, transducer, signal, names in AMPLIFIER_CHANNELS
        if role in roles
    ]
    sensors = sorted(roles & set(SENSOR_SYMBOLS))
    pins = selection["pins_used"]
    required = set()
    allowed = set()
    if "lora" in roles:
        required.add("LORA_CS")
        allowed |= set(RADIO_SIGNALS)
    for _, _, signal, _ in channels:
        required.add(signal)
        allowed.add(signal)
    if sensors:
        allowed |= set(I2C_SIGNALS)
    if set(pins) - allowed:
        raise ValueError("pin_map has an unsupported electronics signal")
    if required - set(pins):
        raise ValueError(f"pin_map requires {sorted(required)}")
    internal_defaults = dict(DEFAULT_INTERNAL) if "lora" in roles else {}
    if sensors:
        internal_defaults.update(SENSOR_INTERNAL)
    internal = {key: value for key, value in internal_defaults.items() if key not in pins}
    wiring = {**internal, **pins}
    if len(set(wiring.values())) != len(wiring):
        raise ValueError("pin_map aliases signals or conflicts with internal SPI/reset pins")
    if any(pin not in GPIO_PADS for pin in wiring.values()):
        raise ValueError("pin_map uses an unavailable ESP32-WROVER-B GPIO")
    for signal, gpio in pins.items():
        if signal in ADC_SIGNALS and gpio not in ADC1_PINS:
            raise ValueError(f"{signal} must use an ADC1 pin")
    c = Circuit()
    c.no_files = True
    c.track_src = False
    c.track_abs_path = False
    number = iter(range(1, 100))  # U1, U2, ... in creation order
    mcu_pins = [
        ("1", "GND", T.PWRIN),
        ("2", "VCC", T.PWRIN),
        ("3", "EN", T.INPUT),
        ("15", "GND2", T.PWRIN),
        ("38", "GND3", T.PWRIN),
    ]
    # I2C pins are not input-only on the MCU side: the master drives the bus, so the
    # net keeps a driving pin for ERC (the sensor side listens).
    input_signals = set(ADC_SIGNALS) | {"LORA_MISO", "LORA_DIO0"}
    for signal, gpio in sorted(wiring.items()):
        is_input = signal in input_signals
        if not is_input and gpio in {"GPIO34", "GPIO35", "GPIO36", "GPIO39"}:
            raise ValueError(f"{signal}: input-only GPIO cannot drive a radio input")
        mcu_pins.append((GPIO_PADS[gpio], gpio, T.INPUT if is_input else T.OUTPUT))
    mcu = make_part(
        c, parts["microcontroller"], f"U{next(number)}", mcu_pins, "RF_Module:ESP32-WROVER"
    )
    radio = None
    if "lora" in roles:
        radio = make_part(
            c,
            parts["lora"],
            f"U{next(number)}",
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
    amplifiers = {}
    for role, _, _signal, _ in channels:
        amplifiers[role] = make_part(
            c,
            parts[role],
            f"U{next(number)}",
            AMP_PINS,
            "Package_TO_SOT_SMD:SOT-23-5",
        )
    sensors = {
        role: make_part(c, parts[role], f"U{next(number)}", *SENSOR_PINS[SENSOR_SYMBOLS[role]])
        for role in sensors
    }
    reg = make_part(
        c,
        parts["regulator"],
        f"U{next(number)}",
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
    if radio is not None:
        gnd += radio["GND"], radio["GND2"], radio["GND3"]
    gnd += [amp["GND"] for amp in amplifiers.values()]
    gnd += [sensor["GND"] for sensor in sensors.values()] + [
        sensor["GND2"] for sensor in sensors.values()
    ]
    vbat += battery["POS"], reg["VIN"], reg["SHDN"]
    vcc += reg["VOUT"], mcu["VCC"]
    if radio is not None:
        vcc += radio["VCC"]
    vcc += [amp["VCC"] for amp in amplifiers.values()]
    vcc += [sensor["VCC"] for sensor in sensors.values()] + [
        sensor["VDDIO"] for sensor in sensors.values()
    ]
    nets = {}
    for signal, gpio in sorted(wiring.items()):
        net = nets[signal] = Net(signal, circuit=c)
        net += mcu[gpio]
        if signal in ADC_SIGNALS:
            net += amplifiers[ADC_SIGNALS[signal]]["OUT"]
        elif radio is not None and signal in RADIO_SIGNALS:
            net += radio[RADIO_SIGNALS[signal][1]]
    for role in sensors:
        for signal in I2C_SIGNALS:
            nets[signal] += sensors[role][signal.removeprefix("SENSOR_")]
    passive_number = iter(range(1, 100))

    def passive(value, a, b, footprint="Resistor_SMD:R_0603_1608Metric"):
        part = make_part(
            c,
            value,
            f"R{next(passive_number)}",
            [("1", "A", T.PASSIVE), ("2", "B", T.PASSIVE)],
            footprint,
        )
        a += part["1"]
        b += part["2"]

    def capacitor(value, a, b):
        passive(value, a, b, "Capacitor_SMD:C_0603_1608Metric")

    en = None
    for index, (role, transducer, signal, names) in enumerate(channels):
        amp = amplifiers[role]
        bias, feedback, input_net, plus = [
            Net(name, circuit=c)
            for name in (names["bias"], names["feedback"], names["input"], names["plus"])
        ]
        if index == 0:
            en = Net("MCU_EN", circuit=c)
            en += mcu["EN"]
        plus += amp["INP"]
        feedback += amp["INM"]
        passive("1Meg", vcc, bias)
        passive("1Meg", bias, gnd)
        passive("100k", nets[signal], feedback)
        passive("10k", feedback, bias)
        if index == 0:
            passive("10k", vcc, en)
        passive("1Meg", bias, plus)
        capacitor("1uF", input_net, plus)
        capacitor("100nF", vcc, gnd)
        capacitor("10uF", vcc, gnd)
        if index == 0:
            capacitor("1uF", en, gnd)
        capacitor("1uF", bias, gnd)
        connector = make_part(
            c,
            transducer,
            "J1" if index == 0 else f"J{index + 2}",
            TRANSDUCER_PINS,
            "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        )
        input_net += connector["SIGNAL"]
        gnd += connector["GND"]
    for role in sensors:
        sensor = sensors[role]
        # I2C mode straps: CSB high selects the bus, SDO low selects address 0x76.
        sensor["CSB"] += vcc
        sensor["SDO"] += gnd
        capacitor("100nF", vcc, gnd)
    if radio is not None:
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
