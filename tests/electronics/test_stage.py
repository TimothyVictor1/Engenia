import csv
import hashlib
import json
import shutil
import tomllib
from pathlib import Path

import pytest
import tomli_w
import yaml
from pydantic import ValidationError

from engenia.agents.electronics.facts import load_facts
from engenia.agents.electronics.selection import propose_selection
from engenia.agents.electronics.stage import ElectronicsStage
from engenia.contracts.models import ChangeRequest, Contract, Requirement, StageContext

REPO = Path(__file__).resolve().parents[2]
CATALOGUE = REPO / "packs/buoy/parts/facts.toml"


def contract(bay=(70, 40, 22), pins=None):
    return Contract.model_validate(
        {
            "version": 1,
            "budgets": {
                "mechanical": {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
                "electronics": {"mass_g": 60, "bay_mm": bay, "energy_mwh_per_day": 180},
                "firmware": {"flash_kb": 512, "ram_kb": 64},
            },
            "interfaces": {
                "pin_map": pins or {"HYDROPHONE_ADC": "GPIO34", "LORA_CS": "GPIO5"},
                "mounts": {"board_holes_mm": [[3, 3], [67, 3], [3, 37], [67, 37]]},
            },
            "totals": {"mass_g_max": 400},
        }
    )


def requirement(days):
    return Requirement.model_validate(
        {
            "id": "R-003",
            "statement": "The buoy shall operate on one battery charge.",
            "value": days,
            "unit": "day",
            "affects": ["electronics", "mechanical"],
            "status": "approved",
            "verify": {"method": "analysis", "check": "battery_capacity", "limit": {"min": days}},
        }
    )


def make_context(root, days=7, bay=(70, 40, 22), pins=None):
    ctx = StageContext(root=root, project="test-buoy", stage="electronics")
    ctx.spec_dir.mkdir(parents=True)
    ctx.contract_path.parent.mkdir(parents=True)
    ctx.contract_path.write_text(contract(bay, pins).model_dump_json(), encoding="utf-8")
    (ctx.spec_dir / "R-003.yaml").write_text(
        yaml.safe_dump(requirement(days).model_dump(mode="json")), encoding="utf-8"
    )
    parts = root / "packs/buoy/parts"
    parts.mkdir(parents=True)
    shutil.copyfile(CATALOGUE, parts / "facts.toml")
    return ctx


@pytest.mark.parametrize("source", [None, "", "   "])
def test_missing_source_rejected(tmp_path, source):
    data = tomllib.loads(CATALOGUE.read_text(encoding="utf-8"))
    if source is None:
        del data["parts"][0]["source"]
    else:
        data["parts"][0]["source"] = source
    path = tmp_path / "facts.toml"
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="source"):
        load_facts(path)


def test_missing_numeric_source_rejected(tmp_path):
    data = tomllib.loads(CATALOGUE.read_text(encoding="utf-8"))
    del data["parts"][0]["sources"]["mass_g"]
    path = tmp_path / "facts.toml"
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="mass_g"):
        load_facts(path)


def test_endurance_selects_smallest_feasible_battery():
    facts = load_facts(CATALOGUE)
    week = propose_selection(contract(), [requirement(7)], facts)
    month = propose_selection(contract(), [requirement(30)], facts)
    assert week["parts"]["battery"] == "LP402030"
    assert month["parts"]["battery"] == "LP503450"
    assert week["endurance_days"] >= 7
    assert month["endurance_days"] >= 30
    assert week["battery_wh"] == pytest.approx(0.666)
    expected_current = 68 * 0.0035 + 0.15 * 0.9965 + 120 * 0.0005
    expected_current += 0.0002 * 0.9995 + 0.024 + 0.2
    assert week["avg_power_mw"] == pytest.approx(3.3 * expected_current / 0.85 + 3.7 * 0.1)
    assert sum(week["duty_cycle"].values()) == pytest.approx(1)
    assert month["requested_bay_mm"] is None
    # Input catalogue order must not influence selection.
    reversed_facts = facts.model_copy(update={"parts": list(reversed(facts.parts))})
    assert propose_selection(contract(), [requirement(30)], reversed_facts) == month


def test_no_battery_fits_one_open_request_and_build_completes(tmp_path):
    ctx = make_context(tmp_path, days=30, bay=(20, 15, 3))
    original = ctx.contract_path.read_bytes()
    stage = ElectronicsStage()
    stage.generate(ctx)
    stage.generate(ctx)
    requests = list((ctx.contract_path.parent / "change_requests").glob("CR-*.json"))
    assert len(requests) == 1
    request = ChangeRequest.model_validate_json(requests[0].read_text())
    assert request.status == "open"
    assert request.from_stage == "electronics"
    assert request.path == "budgets.electronics.bay_mm"
    assert request.requested == pytest.approx([70, 40, 15.3])
    stage.build(ctx)
    assert (ctx.build_dir / "circuit.net").is_file()
    assert stage.interface(ctx)["endurance_days"] >= 30
    assert ctx.contract_path.read_bytes() == original


def test_build_pins_power_bom_and_repeatability(tmp_path):
    pins = {"HYDROPHONE_ADC": "GPIO35", "LORA_CS": "GPIO14", "LORA_SCK": "GPIO22"}
    ctx = make_context(tmp_path / "first", pins=pins)
    stage = ElectronicsStage()
    stage.generate(ctx)
    stage.build(ctx)
    interface = stage.interface(ctx)
    assert interface["pins_used"] == pins
    assert interface["erc_errors"] == 0, (ctx.build_dir / "erc.txt").read_text()
    assert interface["avg_power_mw"] > 0
    assert interface["endurance_days"] == pytest.approx(
        interface["usable_battery_wh"] * 1000 / interface["avg_power_mw"] / 24
    )
    assert interface["board_mm"] == [70, 40, 8]
    with (ctx.build_dir / "bom.csv").open(newline="", encoding="utf-8") as stream:
        bom = list(csv.DictReader(stream))
    assert sum(float(row["mass_g"]) for row in bom) == pytest.approx(interface["mass_g"])
    assert all(row["source"] for row in bom)
    first = (ctx.build_dir / "circuit.net").read_bytes()
    # Build again from the persisted source, including a fresh process and new output directory.
    stage.build(ctx)
    assert (
        hashlib.sha256(first).digest()
        == hashlib.sha256((ctx.build_dir / "circuit.net").read_bytes()).digest()
    )
    other = make_context(tmp_path / "different-path", pins=pins)
    stage.generate(other)
    stage.build(other)
    assert (other.build_dir / "circuit.net").read_bytes() == first
    assert stage.interface(other) == interface
    # Inspect actual pad connectivity rather than merely checking echoed pin metadata.
    text = first.decode()
    assert '(name "HYDROPHONE_ADC")' in text
    assert '(pin "7")' in text  # GPIO35 is physical module pad 7.
    assert "1970-01-01" in text


def test_erc_reports_real_disconnected_supply(tmp_path):
    ctx = make_context(tmp_path)
    stage = ElectronicsStage()
    stage.generate(ctx)
    script = ctx.src_dir / "circuit.py"
    text = script.read_text(encoding="utf-8")
    script.write_text(text.replace('mcu["VCC"], radio["VCC"]', 'radio["VCC"]'), encoding="utf-8")
    stage.build(ctx)
    assert stage.interface(ctx)["erc_errors"] > 0
    assert "Unconnected pin" in (ctx.build_dir / "erc.txt").read_text()


def test_invalid_pin_fails_without_stale_interface(tmp_path):
    ctx = make_context(tmp_path)
    stage = ElectronicsStage()
    stage.generate(ctx)
    stage.build(ctx)
    path = ctx.src_dir / "selection.yaml"
    selection = yaml.safe_load(path.read_text())
    selection["pins_used"]["LORA_CS"] = "GPIO34"
    path.write_text(yaml.safe_dump(selection), encoding="utf-8")
    with pytest.raises(RuntimeError, match="SKiDL build failed"):
        stage.build(ctx)
    with pytest.raises(FileNotFoundError):
        stage.interface(ctx)


def test_unattainable_capacity_is_not_reported_as_met():
    selection = propose_selection(
        contract(bay=(90, 70, 25)), [requirement(10000)], load_facts(CATALOGUE)
    )
    assert selection["parts"]["battery"] == "LP505464"
    assert selection["endurance_days"] < selection["target_endurance_days"]


def test_request_does_not_overwrite_another_lane(tmp_path):
    ctx = make_context(tmp_path, bay=(1, 1, 1))
    directory = ctx.contract_path.parent / "change_requests"
    directory.mkdir()
    old = {
        "id": "CR-001",
        "from_stage": "mechanical",
        "path": "totals.mass_g_max",
        "requested": 500,
        "reason": "test fixture",
        "status": "accepted",
    }
    (directory / "CR-001.json").write_text(json.dumps(old))
    ElectronicsStage().generate(ctx)
    assert json.loads((directory / "CR-001.json").read_text()) == old
    assert (directory / "CR-002.json").is_file()


def test_declares_required_checks():
    assert ElectronicsStage.checks == [
        "power_budget",
        "battery_capacity",
        "erc_clean",
        "mass_budget",
        "bay_fit",
    ]


@pytest.mark.parametrize("local_default", [False, True])
def test_build_without_project_contract(tmp_path, local_default):
    ctx = make_context(tmp_path)
    shutil.rmtree(ctx.contract_path.parent)
    shutil.rmtree(ctx.root / "packs/buoy/parts")
    if local_default:
        default = ctx.root / "packs/buoy/default_contract.json"
        default.write_text(
            contract(pins={"HYDROPHONE_ADC": "GPIO35", "LORA_CS": "GPIO14"}).model_dump_json(),
            encoding="utf-8",
        )
    stage = ElectronicsStage()
    stage.generate(ctx)
    stage.build(ctx)
    interface = stage.interface(ctx)
    assert interface["erc_errors"] == 0
    assert interface["endurance_days"] >= 7
    assert (ctx.build_dir / "circuit.net").is_file()
    assert not ctx.contract_path.exists()
    if local_default:
        assert interface["pins_used"] == {"HYDROPHONE_ADC": "GPIO35", "LORA_CS": "GPIO14"}
