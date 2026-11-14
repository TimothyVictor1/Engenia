"""The bracket pack and the mechanical-only mode it needs (skip_stages).

The tests own their fixtures and never read the antenna-bracket example project. Sizing is a pure
function and fast; the geometry test builds CAD and is marked slow.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
import yaml

from engenia.agents.architect.stage import check_contract, derive_budgets
from engenia.agents.electronics.stage import ElectronicsStage
from engenia.agents.firmware.stage import FirmwareStage
from engenia.contracts.models import Contract, Requirement, StageContext
from engenia.core.intake_template import template_intake
from engenia.harness.packs import skipped_stages
from engenia.sim.loads import parse_load, scenario_load_factor
from engenia.stages.integration.stage import IntegrationStage
from packs.bracket.templates import bracket as br

REPO = Path(__file__).resolve().parents[2]
PACK = REPO / "packs" / "bracket"
ALLOWABLE = 35 * 0.6
G = 9.80665
CLAMP = ("The bracket shall hold a 0.5 kg load 150 mm from the axis of a 40 mm mast with a "
         "bending safety factor of at least 3.")
WALL = ("The bracket shall hold a 0.5 kg load 150 mm from the wall with a bending safety "
        "factor of 3.")


@pytest.fixture
def pack():
    return yaml.safe_load((PACK / "pack.yaml").read_text())


@pytest.fixture
def contract():
    return Contract.model_validate_json((PACK / "default_contract.json").read_text())


def requirement(rid, statement, check, limit, status="approved"):
    return Requirement.model_validate({
        "id": rid, "statement": statement, "value": next(iter(limit.values())), "unit": "x",
        "verify": {"method": "analysis", "check": check, "limit": limit},
        "affects": ["mechanical"], "status": status,
    })


def bending(statement=CLAMP, factor=3.0):
    return requirement("R-001", statement, "bending_stress", {"min": factor})


def size(pack, contract, requirements):
    return br.propose(contract, requirements, pack, PACK)


# --- loads ------------------------------------------------------------------------------------


def test_the_load_parser_reads_mass_lever_and_mast():
    load = parse_load(CLAMP)
    assert load.force_n == pytest.approx(0.5 * G) and load.lever_mm == 150
    assert load.mast_mm == 40
    assert parse_load("hold 12 N 80 mm from the wall").mast_mm is None
    assert parse_load("hold 1 kg") is None and parse_load("a 150 mm from the wall") is None


def test_the_pack_scenarios_carry_a_load_factor_for_the_check():
    assert scenario_load_factor(PACK, "bending_stress") == 2.0
    assert scenario_load_factor(PACK, "stress_safety_factor") == 1.0
    assert scenario_load_factor(REPO / "packs" / "buoy", "bending_stress") == 1.0


# --- sizing -----------------------------------------------------------------------------------


def test_a_mast_in_the_statement_makes_a_clamp_sized_to_that_mast(pack, contract):
    params, decisions = size(pack, contract, [bending()])
    assert params["style"] == "mast_clamp" and params["mast_diameter_mm"] == 40
    assert params["bore_diameter_mm"] == pytest.approx(40 + pack["bracket"]["clamp"][
        "fit_clearance_mm"])
    assert params["lever_mm"] == 150
    titles = [d["title"] for d in decisions]
    for wanted in ("Style", "Design load", "Clamp diameter", "Arm section", "Fillet",
                   "Mounting holes", "Overall size"):
        assert wanted in titles


def test_a_bigger_mast_gives_a_bigger_clamp(pack, contract):
    small, _ = size(pack, contract, [bending(CLAMP)])
    big, _ = size(pack, contract, [bending(CLAMP.replace("40 mm mast", "60 mm mast"))])
    assert big["bore_diameter_mm"] > small["bore_diameter_mm"]
    assert big["ring_height_mm"] >= small["ring_height_mm"]


def test_no_mast_makes_an_l_bracket(pack, contract):
    params, _ = size(pack, contract, [bending(WALL)])
    assert params["style"] == "l_bracket" and params["base_height_mm"] > 0
    assert "bore_diameter_mm" not in params


def test_the_arm_is_sized_so_the_verifier_would_pass(pack, contract):
    """Whichever of stress, aspect or wall governs, the safety factor reaches the limit under
    the pack's gust scenario, by the same formula the verifier uses."""
    for statement in (CLAMP, WALL, CLAMP.replace("0.5 kg", "5 kg")):
        reqs = [bending(statement, 3.0)]
        params, _ = size(pack, contract, reqs)
        p = br.Params(**params)
        load = parse_load(statement)
        force = load.force_n * scenario_load_factor(PACK, "bending_stress")
        sigma = 6 * force * (load.lever_mm - p.root_offset_mm) / (
            p.arm_width_mm * p.arm_depth_mm**2)
        assert ALLOWABLE / sigma >= 3.0


def test_a_heavier_load_gives_a_thicker_arm(pack, contract):
    light, _ = size(pack, contract, [bending(WALL.replace("0.5 kg", "0.2 kg"))])
    heavy, _ = size(pack, contract, [bending(WALL.replace("0.5 kg", "3 kg"))])
    assert heavy["arm_depth_mm"] > light["arm_depth_mm"]


def test_the_wall_requirement_sets_the_thinnest_section(pack, contract):
    wall = requirement("R-002", "The bracket shall have a wall of 5 mm.", "wall_thickness_min",
                       {"min": 5.0})
    params, decisions = size(pack, contract, [bending(), wall])
    assert params["ring_wall_mm"] >= 5.0 and params["plate_thickness_mm"] >= 5.0
    assert "R-002" in next(d for d in decisions if d["title"] == "Clamp diameter")["requirements"]


def test_sizing_is_deterministic(pack, contract):
    assert size(pack, contract, [bending()]) == size(pack, contract, [bending()])


def test_a_bracket_without_a_stated_load_is_refused(pack, contract):
    vague = bending("The bracket shall be strong.")
    draft = requirement("R-001", CLAMP, "bending_stress", {"min": 3.0}, status="draft")
    for requirements in ([vague], [], [draft]):
        with pytest.raises(ValueError, match="names the load"):
            size(pack, contract, requirements)


def test_a_load_inside_the_clamp_is_refused(pack, contract):
    with pytest.raises(ValueError, match="inside"):
        size(pack, contract, [bending(CLAMP.replace("150 mm", "20 mm"))])


def test_a_small_envelope_reports_what_the_bracket_needs(pack, contract):
    tight = Contract.model_validate({
        **contract.model_dump(mode="json"),
        "budgets": {**contract.model_dump(mode="json")["budgets"],
                    "mechanical": {"mass_g": 120, "outer_envelope_mm": [100, 50, 50]}}})
    with pytest.raises(br.BracketEnvelopeError) as raised:
        size(pack, tight, [bending()])
    assert raised.value.available == [100, 50, 50] and raised.value.required[-1] > 100


def test_params_reject_inconsistent_geometry(pack, contract):
    good, _ = size(pack, contract, [bending()])
    for change in ({"style": "wedge"}, {"bore_diameter_mm": 30.0}, {"arm_depth_mm": 99.0},
                   {"fillet_mm": 9.0}, {"lever_mm": 20.0}, {"plate_size_mm": 20.0},
                   {"infill_factor": 2.0}, {"arm_width_mm": float("inf")}):
        with pytest.raises(ValueError):
            br.Params(**{**good, **change})


def test_the_facts_publish_the_root_section_the_verifier_reads(pack, contract):
    params, _ = size(pack, contract, [bending()])
    p = br.Params(**params)
    facts = br.extra_facts(p)
    assert facts["bending_root_section_mm"] == [p.arm_width_mm, p.arm_depth_mm]
    assert facts["bending_root_offset_mm"] == pytest.approx(p.ring_outer_radius_mm)
    assert facts["wall_min_mm"] <= min(p.ring_wall_mm, p.arm_width_mm)
    assert br.hardware(p) == {"clamp_bolt": pack["bracket"]["clamp"]["bolt_mass_g"]}
    wall, _ = size(pack, contract, [bending(WALL)])
    assert br.hardware(br.Params(**wall)) == {}


# --- intake -----------------------------------------------------------------------------------


def test_intake_writes_the_load_into_the_statement_and_the_safety_factor_as_the_limit(tmp_path):
    request = ("A bracket that holds a 0.5 kg antenna 150 mm from a 40 mm mast, safety factor "
               ">= 3, under 120 g, walls at least 3 mm, fits the print bed")
    paths = template_intake(request, "b", tmp_path, pack="bracket")
    found = {yaml.safe_load(p.read_text())["verify"]["check"]: yaml.safe_load(p.read_text())
             for p in paths}
    assert set(found) == {"bending_stress", "mass_budget", "wall_thickness_min",
                          "print_bed_fit"}
    bend = found["bending_stress"]
    assert bend["verify"]["limit"] == {"min": 3} and bend["value"] == 3
    assert parse_load(bend["statement"]).mast_mm == 40
    assert found["mass_budget"]["verify"]["limit"] == {"max": 120}


def test_intake_makes_a_wall_bracket_when_no_mast_is_named(tmp_path):
    paths = template_intake("A bracket that holds a 2 kg camera 80 mm from the wall, safety "
                            "factor 2", "b", tmp_path, pack="bracket")
    data = yaml.safe_load(paths[0].read_text())
    assert parse_load(data["statement"]).mast_mm is None and data["value"] == 2


# --- mechanical-only mode ---------------------------------------------------------------------


def ctx_for(tmp_path, stage, pack="bracket"):
    project = tmp_path / "projects" / "p"
    (project / "contract").mkdir(parents=True, exist_ok=True)
    (project / "spec").mkdir(exist_ok=True)
    (project / "project.yaml").write_text(f"pack: {pack}\n")
    default = REPO / "packs" / pack / "default_contract.json"
    (project / "contract" / "contract.json").write_text(default.read_text())
    return StageContext(project="p", stage=stage, root=tmp_path)


def test_the_bracket_pack_skips_electronics_and_firmware(tmp_path):
    assert skipped_stages(ctx_for(tmp_path, "electronics")) == {"electronics", "firmware"}
    assert skipped_stages(ctx_for(tmp_path, "electronics", pack="buoy")) == frozenset()


def test_skip_stages_rejects_unknown_stages_and_electronics_without_firmware(tmp_path):
    ctx = ctx_for(tmp_path, "electronics")
    local = tmp_path / "packs" / "bracket"
    local.mkdir(parents=True)
    for skip in (["mechanical"], ["electronics"], "electronics"):
        (local / "pack.yaml").write_text(yaml.safe_dump({"skip_stages": skip}))
        with pytest.raises(ValueError, match="skip_stages"):
            skipped_stages(ctx)


def test_skipped_electronics_and_firmware_publish_not_applicable_facts(tmp_path):
    electronics, firmware = ElectronicsStage(), FirmwareStage()
    e_ctx, f_ctx = ctx_for(tmp_path, "electronics"), ctx_for(tmp_path, "firmware")
    electronics.generate(e_ctx)
    electronics.build(e_ctx)
    facts = electronics.interface(e_ctx)
    assert facts["not_applicable"] is True and facts["mass_g"] == 0.0
    assert facts["contract_version"] == 2 and electronics.checks == []
    firmware.generate(f_ctx)
    firmware.build(f_ctx)
    facts = firmware.interface(f_ctx)
    assert facts["not_applicable"] is True and facts["contract_version"] == 2
    assert firmware.checks == []
    assert not (f_ctx.src_dir / "tools").exists()  # no detector sources were generated


def test_the_stages_run_normally_again_for_a_pack_that_skips_nothing(tmp_path):
    ctx = ctx_for(tmp_path, "electronics", pack="enclosure")
    stage = ElectronicsStage()
    stage.generate(ctx)
    assert (ctx.src_dir / "selection.yaml").is_file() and stage.checks


def test_integration_ignores_board_checks_for_skipped_stages_and_keeps_mass(tmp_path):
    ctx = ctx_for(tmp_path, "integration")
    for stage, facts in {
        "mechanical": {"dry_mass_g": 40.0, "contract_version": 2},
        "electronics": {"not_applicable": True, "mass_g": 0.0, "contract_version": 2},
        "firmware": {"not_applicable": True, "contract_version": 2},
    }.items():
        path = ctx.stage_interface_path(stage)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"stage": stage, "key": "sha256:" + "0" * 64, "facts": facts}))
    stage = IntegrationStage()
    stage.build(ctx)
    verdicts = json.loads((ctx.build_dir / "verdict.json").read_text())
    assert {v["check"]: v["result"] for v in verdicts} == {
        "int_mass_total": "pass", "int_contract_version": "pass"}
    assert stage.interface(ctx)["total_mass_g"] == 40.0
    assert json.loads((ctx.build_dir / "mismatches.json").read_text()) == []


def test_the_architect_accepts_zero_electronics_budgets_only_when_skipped():
    text = (PACK / "default_contract.json").read_text()
    assert not check_contract(text).ok
    assert check_contract(text, frozenset({"electronics", "firmware"})).ok
    data = json.loads(text)
    data["budgets"]["electronics"]["mass_g"] = 10
    problems = check_contract(json.dumps(data), frozenset({"electronics", "firmware"})).problems
    assert any("budget must be 0" in p for p in problems)


def test_the_whole_mass_requirement_goes_to_the_mechanical_budget(pack):
    default = json.loads((PACK / "default_contract.json").read_text())
    requirement_ = requirement("R-002", "The bracket shall weigh at most 120 g.", "mass_budget",
                               {"max": 120.0})
    derived, _ = derive_budgets(copy.deepcopy(default), [requirement_], default)
    assert derived["budgets"]["electronics"]["mass_g"] == 0
    assert derived["budgets"]["mechanical"]["mass_g"] == pytest.approx(108.0)


# --- geometry (builds CAD) --------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("statement", [CLAMP, WALL])
def test_the_template_builds_one_closed_solid_that_matches_its_parameters(
        pack, contract, statement):
    params, _ = size(pack, contract, [bending(statement)])
    p = br.Params(**params)
    part = br.build(p)["bracket"]
    assert part.is_valid and len(part.solids()) == 1
    box = part.bounding_box().size
    if p.style == "mast_clamp":
        assert box.X == pytest.approx(p.lever_mm + p.ring_outer_radius_mm + p.ear_length_mm,
                                      abs=0.01)
        assert box.Y == pytest.approx(max(p.plate_size_mm, 2 * p.ring_outer_radius_mm), abs=0.01)
        # Mass of the printed volume is within the 120 g budget with room to spare.
        assert part.volume / 1000 * 1.27 < 120
    else:
        assert box.X == pytest.approx(p.lever_mm + p.plate_thickness_mm, abs=0.01)
        assert box.Z == pytest.approx(p.base_height_mm, abs=0.01)
    assert not math.isnan(part.volume)
