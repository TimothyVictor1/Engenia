"""The AI proposer hook, with FakeLLM and fixtures of its own."""

import json
from pathlib import Path

import pytest

from engenia.agents.electronics.facts import load_facts
from engenia.agents.electronics.selection import propose_selection
from engenia.agents.mechanical.stage import propose_params
from engenia.contracts.models import Contract, Requirement, StageContext
from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, ModelFamilyError, text_response, tool_call_response
from engenia.harness.proposer import (
    propose_electronics_selection,
    propose_mechanical_params,
    proposer_fact,
)

REPO = Path(__file__).resolve().parents[2]
BUILD = ("run_build", {})
LIMITS = Limits(max_attempts=3)

CONTRACT = Contract.model_validate({
    "version": 1,
    "budgets": {
        "mechanical": {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
        "electronics": {"mass_g": 60, "bay_mm": [70, 40, 22], "energy_mwh_per_day": 180},
        "firmware": {"flash_kb": 512, "ram_kb": 64},
    },
    "interfaces": {
        "pin_map": {"HYDROPHONE_ADC": "GPIO34"},
        "mounts": {"board_holes_mm": [[3, 3], [67, 3], [3, 37], [67, 37]]},
    },
    "totals": {"mass_g_max": 400},
})


def requirement(number, check, limit, unit="mm", value=3):
    return Requirement.model_validate({
        "id": f"R-{number:03d}", "statement": f"The buoy shall satisfy {check}.",
        "value": value, "unit": unit,
        "verify": {"method": "analysis", "check": check, "limit": limit},
        "affects": ["mechanical", "electronics"], "status": "approved",
    })


WALL = requirement(1, "wall_thickness_min", {"min": 3})
ENDURANCE = requirement(2, "battery_capacity", {"min": 30}, unit="day", value=30)


def writes(proposal):
    return [tool_call_response(("write_file", {"path": "proposal.json",
                                               "content": json.dumps(proposal)}), BUILD),
            text_response("done")]


def user_texts(llm, call):
    return [m["content"] for m in llm.calls[call].messages if m["role"] == "user"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("ENGENIA_LLM_BASE_URL", "ENGENIA_DESIGN_MODEL", "ENGENIA_VERIFY_MODEL"):
        monkeypatch.delenv(name, raising=False)


def ctx_for(root, stage):
    return StageContext(project="demo", stage=stage, root=root)


# --- mechanical --------------------------------------------------------------------------------


def hull(ctx, llm):
    return propose_mechanical_params(
        ctx, CONTRACT, [WALL], propose_params, model="design/model", limits=LIMITS, chat_fn=llm
    )


def test_bad_hull_numbers_reach_the_model_and_a_later_attempt_passes(root):
    ctx = ctx_for(root, "mechanical")
    good = propose_params(CONTRACT, [WALL])
    bad = {**good, "wall_thickness_mm": 1.0, "outer_diameter_mm": 500}
    llm = FakeLLM(writes(bad) + writes(good))

    params = hull(ctx, llm)

    assert params == good
    assert proposer_fact(ctx) == "model"
    feedback = user_texts(llm, 2)[-1]  # first call of attempt 2
    assert "hull_diameter_mm" in feedback and "500.0" in feedback
    assert "hull_wall_mm" in feedback and "1.0" in feedback
    assert '"max": 160.0' in feedback


def test_always_bad_hull_falls_back_to_the_deterministic_function(root):
    ctx = ctx_for(root, "mechanical")
    bad = {**propose_params(CONTRACT, [WALL]), "wall_thickness_mm": 1.0}
    llm = FakeLLM(writes(bad) * 3)

    params = hull(ctx, llm)

    assert params == propose_params(CONTRACT, [WALL])
    assert proposer_fact(ctx) == "deterministic"
    assert llm.remaining == 0


def test_unbuildable_hull_is_rejected_with_the_reason(root):
    ctx = ctx_for(root, "mechanical")
    good = propose_params(CONTRACT, [WALL])
    unbuildable = {**good, "outer_diameter_mm": 60}  # the bay does not fit the sealing rim
    llm = FakeLLM(writes(unbuildable) + writes(good))

    assert hull(ctx, llm) == good
    assert "hull_buildable" in user_texts(llm, 2)[-1]


def test_unparseable_proposal_file_counts_as_a_failed_attempt(root):
    ctx = ctx_for(root, "mechanical")
    good = propose_params(CONTRACT, [WALL])
    garbage = [tool_call_response(("write_file", {"path": "proposal.json", "content": "{nope"}),
                                  BUILD), text_response("done")]
    llm = FakeLLM(garbage + writes(good))

    assert hull(ctx, llm) == good
    assert "not valid JSON" in user_texts(llm, 2)[-1]


def test_no_model_is_deterministic_and_makes_no_call(root):
    ctx = ctx_for(root, "mechanical")

    params = propose_mechanical_params(ctx, CONTRACT, [WALL], propose_params, limits=LIMITS)

    assert params == propose_params(CONTRACT, [WALL])
    assert proposer_fact(ctx) == "deterministic"


def test_a_model_without_an_endpoint_falls_back(root, monkeypatch):
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "design/model")  # no ENGENIA_LLM_BASE_URL
    ctx = ctx_for(root, "mechanical")

    params = propose_mechanical_params(ctx, CONTRACT, [WALL], propose_params)

    assert params == propose_params(CONTRACT, [WALL])
    assert proposer_fact(ctx) == "deterministic"


def test_the_proposal_never_touches_the_real_src_folder_except_the_record(root):
    ctx = ctx_for(root, "mechanical")
    good = propose_params(CONTRACT, [WALL])

    hull(ctx, FakeLLM(writes(good)))

    assert sorted(p.name for p in ctx.src_dir.iterdir()) == ["proposer.json"]


def test_same_family_stops_the_proposer_instead_of_falling_back(root, monkeypatch):
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Qwen/A")
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Qwen/B")
    llm = FakeLLM([])
    with pytest.raises(ModelFamilyError):
        hull(ctx_for(root, "mechanical"), llm)
    assert llm.calls == []


# --- electronics -------------------------------------------------------------------------------

FACTS = load_facts(REPO / "packs/buoy/parts/facts.toml")


def select(ctx, llm):
    return propose_electronics_selection(
        ctx, CONTRACT, [ENDURANCE], FACTS, propose_selection,
        model="design/model", limits=LIMITS, chat_fn=llm,
    )


def test_bad_part_choice_is_fed_back_and_a_later_attempt_passes(root):
    ctx = ctx_for(root, "electronics")
    expected = propose_selection(CONTRACT, [ENDURANCE], FACTS)
    bad = {"parts": {**expected["parts"], "battery": "no-such-battery"}}
    llm = FakeLLM(writes(bad) + writes({"parts": expected["parts"]}))

    selection = select(ctx, llm)

    assert selection == expected
    assert proposer_fact(ctx) == "model"
    assert "no-such-battery" in user_texts(llm, 2)[-1]


def test_always_bad_selection_falls_back_and_is_recorded(root):
    ctx = ctx_for(root, "electronics")
    expected = propose_selection(CONTRACT, [ENDURANCE], FACTS)
    bad = {"parts": {**expected["parts"], "battery": "no-such-battery"}}
    llm = FakeLLM(writes(bad) * 3)

    assert select(ctx, llm) == expected
    assert proposer_fact(ctx) == "deterministic"
