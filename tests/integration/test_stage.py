"""Integration fixtures deliberately require no CAD or real project."""

import json
from pathlib import Path

import pytest

from engenia.contracts.models import StageContext, Verdict
from engenia.stages.integration.stage import CHECKS, IntegrationStage


@pytest.fixture
def assembly(tmp_path):
    contract = json.loads(Path("packs/buoy/default_contract.json").read_text())
    facts = {
        "mechanical": {"dry_mass_g": 300, "battery_bay_mm": [70, 40, 22],
                       "centre_of_mass_mm": [0, 0, 10], "battery_bay_centre_mm": [0, 0, 30],
                       "displaced_volume_cm3": 500, "contract_version": 1},
        "electronics": {"mass_g": 60, "board_mm": [68, 38, 20], "battery_mm": [50, 30, 18],
                        "board_holes_mm": contract["interfaces"]["mounts"]["board_holes_mm"],
                        "pins_used": contract["interfaces"]["pin_map"], "contract_version": 1},
        "firmware": {"pins_used": contract["interfaces"]["pin_map"], "contract_version": 1},
    }
    ctx = StageContext(root=tmp_path, project="fixture", stage="integration")
    ctx.contract_path.parent.mkdir(parents=True)
    ctx.contract_path.write_text(json.dumps(contract))
    return ctx, contract, facts


def run(assembly):
    ctx, _, facts = assembly
    for stage, data in facts.items():
        path = ctx.stage_interface_path(stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stage": stage, "key": "sha256:" + "0" * 64,
                                    "facts": data}))
    stage = IntegrationStage()
    stage.generate(ctx)
    assert ctx.src_dir.is_dir()
    assert list(ctx.src_dir.iterdir()) == []
    stage.build(ctx)
    verdicts = json.loads((ctx.build_dir / "verdict.json").read_text())
    for verdict in verdicts:
        Verdict.model_validate(verdict)
    return {v["check"]: v for v in verdicts}


@pytest.mark.parametrize("check,_stages", CHECKS)
def test_pass(assembly, check, _stages):
    assert run(assembly)[check.__name__]["result"] == "pass"


@pytest.mark.parametrize("check,stage,fact,value,stages", [
    ("int_board_fits_bay", "electronics", "board_mm", [69, 38, 20],
     ["mechanical", "electronics"]),
    ("int_mounts_match", "electronics", "board_holes_mm", [[0, 0]], ["electronics"]),
    ("int_mass_total", "electronics", "mass_g", 101, ["mechanical", "electronics"]),
    ("int_pins_match", "firmware", "pins_used", {}, ["electronics", "firmware"]),
    ("int_contract_version", "electronics", "contract_version", 0, ["electronics"]),
])
def test_fail(assembly, check, stage, fact, value, stages):
    ctx, _, facts = assembly
    facts[stage][fact] = value
    verdict = run(assembly)[check]
    assert verdict["result"] == "fail"
    assert verdict["requirement"] == ("R-001" if check == "int_mass_total" else None)
    mismatches = json.loads((ctx.build_dir / "mismatches.json").read_text())
    assert mismatches == [{"check": check, "stages": stages, "message": verdict["message"]}]


@pytest.mark.parametrize("stage", ["mechanical", "electronics", "firmware"])
def test_stub(assembly, stage):
    assembly[2][stage] = {"stub": True}
    verdicts = run(assembly)
    for check, stages in CHECKS:
        if stage in stages:
            verdict = verdicts[check.__name__]
            assert verdict["result"] == "error"
            assert stage in verdict["message"]


@pytest.mark.parametrize("stage,fact,check", [
    ("mechanical", "battery_bay_mm", "int_board_fits_bay"),
    ("electronics", "battery_mm", "int_board_fits_bay"),
    ("electronics", "board_mm", "int_board_fits_bay"),
    ("electronics", "board_holes_mm", "int_mounts_match"),
    ("mechanical", "dry_mass_g", "int_mass_total"),
    ("electronics", "mass_g", "int_mass_total"),
    ("firmware", "pins_used", "int_pins_match"),
    ("electronics", "pins_used", "int_pins_match"),
    *[(s, "contract_version", "int_contract_version")
      for s in ("mechanical", "electronics", "firmware")],
])
def test_missing_fact(assembly, stage, fact, check):
    del assembly[2][stage][fact]
    verdict = run(assembly)[check]
    assert verdict["result"] == "error"
    assert stage in verdict["message"] and fact in verdict["message"]


def test_assembly_and_reproduction(assembly):
    run(assembly)
    ctx = assembly[0]
    result = IntegrationStage().interface(ctx)
    assert result == {"total_mass_g": 360, "centre_of_mass_mm": [0, 0, pytest.approx(40 / 3)],
                      "displaced_volume_cm3": 500, "failed_checks": []}
    before = {p.name: p.read_bytes() for p in ctx.build_dir.iterdir()}
    run(assembly)
    assert before == {p.name: p.read_bytes() for p in ctx.build_dir.iterdir()}
    assembly[2]["firmware"]["contract_version"] = 0
    run(assembly)
    assert IntegrationStage().interface(ctx)["failed_checks"] == ["int_contract_version"]
    assembly[2]["firmware"]["contract_version"] = 1
    run(assembly)
    assert json.loads((ctx.build_dir / "mismatches.json").read_text()) == []


def test_fallback_contract(assembly):
    ctx, contract, _ = assembly
    fallback = ctx.root / "packs/buoy/default_contract.json"
    fallback.parent.mkdir(parents=True)
    fallback.write_text(json.dumps(contract))
    ctx.contract_path.unlink()
    assert all(v["result"] == "pass" for v in run(assembly).values())


def test_missing_interface(assembly):
    ctx = assembly[0]
    run(assembly)
    ctx.stage_interface_path("electronics").unlink()
    IntegrationStage().build(ctx)
    verdicts = json.loads((ctx.build_dir / "verdict.json").read_text())
    assert all(v["result"] == "error" for v in verdicts)


def test_unknown_assembly_location(assembly):
    del assembly[2]["mechanical"]["battery_bay_centre_mm"]
    run(assembly)
    facts = IntegrationStage().interface(assembly[0])
    assert facts["centre_of_mass_mm"] is None
    assert facts["total_mass_g"] == 360


def test_packaged_fallback_contract(assembly):
    assembly[0].contract_path.unlink()
    assert all(v["result"] == "pass" for v in run(assembly).values())
