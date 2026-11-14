"""The electronics stage builds whatever product pack the project names.

The buoy catalogue keeps its five functions; the enclosure pack swaps the hydrophone
channel for a piezo preamp and an I2C environment sensor, and needs no endurance
requirement to build. Selection and power math follow the pack's `active_modes`
matrix, not hard-coded roles.
"""

import csv
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from engenia.agents.electronics.circuit_template import create_circuit
from engenia.agents.electronics.facts import Facts, load_facts, required_functions
from engenia.agents.electronics.selection import propose_selection
from engenia.agents.electronics.stage import ElectronicsStage
from engenia.contracts.models import Contract, Requirement, StageContext

REPO = Path(__file__).resolve().parents[2]
ENCLOSURE = REPO / "packs/enclosure"
ENCLOSURE_CATALOGUE = ENCLOSURE / "parts/facts.toml"


def requirement(days):
    return Requirement.model_validate(
        {
            "id": "R-003",
            "statement": "The deck sensor shall operate on one battery charge.",
            "value": days,
            "unit": "day",
            "affects": ["electronics", "mechanical"],
            "status": "approved",
            "verify": {"method": "analysis", "check": "battery_capacity", "limit": {"min": days}},
        }
    )


def make_context(root, days=None):
    """A deck-sensor electronics context whose pack files resolve from the repository."""
    ctx = StageContext(root=root, project="deck", stage="electronics")
    ctx.spec_dir.mkdir(parents=True)
    ctx.contract_path.parent.mkdir(parents=True)
    ctx.contract_path.write_text((ENCLOSURE / "default_contract.json").read_text())
    (ctx.project_dir / "project.yaml").write_text("pack: enclosure\n", encoding="utf-8")
    if days is not None:
        (ctx.spec_dir / "R-003.yaml").write_text(
            yaml.safe_dump(requirement(days).model_dump(mode="json")), encoding="utf-8"
        )
    return ctx


def enclosure_facts():
    return load_facts(ENCLOSURE_CATALOGUE)


def enclosure_contract():
    return Contract.model_validate_json((ENCLOSURE / "default_contract.json").read_text())


def test_buoy_functions_are_the_active_modes_plus_battery_and_regulator():
    facts = load_facts(REPO / "packs/buoy/parts/facts.toml")
    assert required_functions(facts) == {
        "microcontroller", "lora", "audio", "battery", "regulator",
    }


def test_enclosure_functions_come_from_its_catalogue():
    assert required_functions(enclosure_facts()) == {
        "microcontroller", "lora", "environment_sensor", "vibration_amp",
        "battery", "regulator",
    }


@pytest.mark.parametrize("matrix", [
    {"microcontroller": ["sample", "transmit", "night"]},
    {"battery": ["sleep"]},
    {"regulator": ["sample"]},
    {"": ["sample"]},
])
def test_design_profile_rejects_a_bad_matrix(matrix):
    profile = enclosure_facts().design.model_dump()
    profile["active_modes"] = matrix
    with pytest.raises(ValidationError):
        type(enclosure_facts().design).model_validate(profile)


def test_catalogue_without_a_declared_function_rejected():
    facts = enclosure_facts()
    parts = [p.model_dump(mode="json") for p in facts.parts if p.function != "environment_sensor"]
    with pytest.raises(ValidationError, match="missing parts for.*environment_sensor"):
        Facts.model_validate({"design": facts.design.model_dump(mode="json"), "parts": parts})


def test_selection_without_an_endurance_requirement_still_builds():
    selection = propose_selection(enclosure_contract(), [], enclosure_facts())
    assert selection["target_endurance_days"] == 0.0
    # The smallest fitting cell by volume wins when nothing is promised.
    assert selection["parts"]["battery"] == "LP402030"
    assert selection["requested_bay_mm"] is None


def test_enclosure_power_follows_the_matrix():
    selection = propose_selection(enclosure_contract(), [requirement(14)], enclosure_facts())
    # duty 0.002/0.0005/0.9975; amp stays powered in sleep, sensor sleeps between bursts.
    assert selection["mode_power_mw"]["sample"] == pytest.approx(
        3.3 * (0.2 + 68 + 0.0002 + 0.0034 + 0.024) / 0.85 + 0.37, rel=1e-9
    )
    assert selection["mode_power_mw"]["transmit"] == pytest.approx(
        3.3 * (0.2 + 68 + 120 + 0.0001 + 0.024) / 0.85 + 0.37, rel=1e-9
    )
    assert selection["mode_power_mw"]["sleep"] == pytest.approx(
        3.3 * (0.2 + 0.15 + 0.0002 + 0.0001 + 0.024) / 0.85 + 0.37, rel=1e-9
    )
    assert selection["avg_power_mw"] < 25
    assert selection["endurance_days"] > 14
    assert selection["parts"]["battery"] == "LP503450"
    assert selection["requested_bay_mm"] is None


def test_enclosure_stage_builds_the_deck_sensor_netlist(tmp_path):
    ctx = make_context(tmp_path, days=14)
    stage = ElectronicsStage()
    stage.generate(ctx)
    stage.build(ctx)
    interface = stage.interface(ctx)
    assert interface["pins_used"] == {
        "SENSOR_SDA": "GPIO21", "SENSOR_SCL": "GPIO22",
        "VIBRATION_ADC": "GPIO34", "LORA_CS": "GPIO5",
    }
    assert interface["internal_pins"] == {
        "LORA_SCK": "GPIO18", "LORA_MISO": "GPIO19", "LORA_MOSI": "GPIO23",
        "LORA_RESET": "GPIO27", "LORA_DIO0": "GPIO26",
    }
    assert interface["erc_errors"] == 0, (ctx.build_dir / "erc.txt").read_text()
    assert interface["endurance_days"] > 14
    netlist = (ctx.build_dir / "circuit.net").read_text(encoding="utf-8")
    assert "BME280" in netlist and "PIEZO_IN" in netlist
    assert "HYDROPHONE" not in netlist
    with (ctx.build_dir / "bom.csv").open(newline="", encoding="utf-8") as stream:
        ids = {row["id"] for row in csv.DictReader(stream)}
    assert {"ESP32-WROVER-B", "RFM95W-868S2", "BME280", "MAX4466EUK", "LP503450", "S7V8F3"} <= ids


def test_contract_signals_without_the_matching_part_rejected():
    selection = {
        "parts": {
            "battery": "LP503450", "lora": "RFM95W-868S2",
            "microcontroller": "ESP32-WROVER-B", "regulator": "S7V8F3",
            "vibration_amp": "MAX4466EUK",
        },
        "pins_used": {
            "SENSOR_SDA": "GPIO21", "SENSOR_SCL": "GPIO22",
            "VIBRATION_ADC": "GPIO34", "LORA_CS": "GPIO5",
        },
    }
    with pytest.raises(ValueError, match="unsupported electronics signal"):
        create_circuit(selection)


def test_a_pinned_signal_on_a_non_adc_pin_rejected(tmp_path):
    ctx = make_context(tmp_path, days=14)
    stage = ElectronicsStage()
    stage.generate(ctx)
    selection_path = ctx.src_dir / "selection.yaml"
    selection = yaml.safe_load(selection_path.read_text(encoding="utf-8"))
    selection["pins_used"]["VIBRATION_ADC"] = "GPIO14"  # not an ADC1 input pin
    selection_path.write_text(yaml.safe_dump(selection), encoding="utf-8")
    with pytest.raises(RuntimeError, match="ADC1"):
        stage.build(ctx)
