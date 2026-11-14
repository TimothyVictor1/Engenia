import json

import pytest

from engenia.agents.architect.stage import ArchitectStage, check_contract
from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, LLMError, text_response, tool_call_response


def default(root) -> dict:
    return json.loads((root / "packs" / "buoy" / "default_contract.json").read_text())


def contract_text(root, *, mech=300, elec=60, total=400) -> str:
    data = default(root)
    data["budgets"]["mechanical"]["mass_g"] = mech
    data["budgets"]["electronics"]["mass_g"] = elec
    data["totals"]["mass_g_max"] = total
    return json.dumps(data)


def attempt(text: str):
    return [
        tool_call_response(("write_file", {"path": "contract.json", "content": text})),
        text_response("done"),
    ]


def test_default_contract_passes_the_script_check(root):
    report = check_contract((root / "packs" / "buoy" / "default_contract.json").read_text())
    assert report.ok, report.problems
    assert report.sums == {"mass_g_sum": 360, "mass_g_max": 400, "mass_g_headroom": 40}


def test_without_a_model_the_default_contract_is_used(root, architect_ctx):
    stage = ArchitectStage()
    stage.generate(architect_ctx)
    stage.build(architect_ctx)
    assert json.loads(architect_ctx.contract_path.read_text()) == default(root)
    facts = stage.interface(architect_ctx)
    assert facts["source"] == "default"
    assert facts["mass_g_sum"] == 360
    assert (architect_ctx.build_dir / "contract.json").is_file()


def test_model_contract_is_used(root, architect_ctx, monkeypatch):
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "design-model")
    llm = FakeLLM(attempt(contract_text(root, mech=280, elec=50)))
    stage = ArchitectStage(chat_fn=llm)
    stage.generate(architect_ctx)
    stage.build(architect_ctx)
    assert llm.calls[0].model == "design-model"
    assert stage.interface(architect_ctx)["source"] == "model"
    # R-001 (total mass 400 g) governs the mass budgets, so the model's 280/50 split is replaced
    # by the derived 300/60; the budgets no requirement sets stay the model's.
    budgets = json.loads(architect_ctx.contract_path.read_text())["budgets"]
    assert budgets["mechanical"]["mass_g"] == 300 and budgets["electronics"]["mass_g"] == 60
    sources = stage.interface(architect_ctx)["budget_sources"]
    assert sources["budgets.mechanical.mass_g"] == "R-001"
    assert sources["budgets.firmware.flash_kb"] == "model"
    first_user = [m["content"] for m in llm.calls[0].messages if m["role"] == "user"][0]
    assert "R-001" in first_user and "PETG" in first_user  # requirements and pack.yaml


def test_measured_sums_go_back_to_the_model(root, architect_ctx):
    llm = FakeLLM(attempt(contract_text(root, mech=350, elec=80)) + attempt(contract_text(root)))
    stage = ArchitectStage(chat_fn=llm, model="m")
    stage.generate(architect_ctx)
    feedback = [m["content"] for m in llm.calls[2].messages if m["role"] == "user"][-1]
    assert '"mass_g_sum": 430' in feedback
    assert "over totals.mass_g_max 400.0 g by 30.0 g" in feedback
    assert stage.interface(architect_ctx)["source"] == "model"
    assert llm.remaining == 0


def test_three_failed_attempts_fall_back_to_default(root, architect_ctx):
    bad = contract_text(root, mech=350, elec=80)
    llm = FakeLLM(attempt(bad) * 3)
    stage = ArchitectStage(chat_fn=llm, model="m", limits=Limits(max_attempts=3))
    stage.generate(architect_ctx)
    stage.build(architect_ctx)
    assert llm.remaining == 0
    assert stage.interface(architect_ctx)["source"] == "default"
    assert json.loads(architect_ctx.contract_path.read_text()) == default(root)


def test_unavailable_model_falls_back_to_default(root, architect_ctx):
    stage = ArchitectStage(chat_fn=FakeLLM([LLMError("down")]), model="m")
    stage.generate(architect_ctx)
    stage.build(architect_ctx)
    assert stage.interface(architect_ctx)["source"] == "default"


def test_model_that_writes_nothing_is_a_failed_attempt(root, architect_ctx):
    llm = FakeLLM([text_response("I am done")] * 2)
    stage = ArchitectStage(chat_fn=llm, model="m", limits=Limits(max_attempts=2))
    stage.generate(architect_ctx)
    assert stage.interface(architect_ctx)["source"] == "default"


def test_stale_contract_does_not_count_as_the_models_work(root, architect_ctx):
    architect_ctx.contract_path.parent.mkdir(parents=True)
    architect_ctx.contract_path.write_text(contract_text(root, mech=100, elec=10))
    llm = FakeLLM([text_response("done")])
    stage = ArchitectStage(chat_fn=llm, model="m", limits=Limits(max_attempts=1))
    stage.generate(architect_ctx)
    assert json.loads(architect_ctx.contract_path.read_text()) == default(root)


def test_model_can_write_only_contract_json(root, architect_ctx):
    architect_ctx.contract_path.parent.mkdir(parents=True)
    llm = FakeLLM([
        tool_call_response(
            ("write_file", {"path": "change_requests/CR-001.json", "content": "{}"}),
            ("write_file", {"path": "../spec/R-001.yaml", "content": "x"}),
        ),
        text_response("done"),
    ])
    ArchitectStage(chat_fn=llm, model="m", limits=Limits(max_attempts=1)).generate(architect_ctx)
    results = [m["content"] for m in llm.calls[1].messages if m["role"] == "tool"]
    assert "you may write only contract.json" in results[0]
    assert "outside the allowed directory" in results[1]
    assert not (architect_ctx.contract_path.parent / "change_requests").exists()


@pytest.mark.parametrize(
    ("edit", "needle"),
    [
        (lambda d: d["totals"].update(mass_g_max=300), "over totals.mass_g_max"),
        (lambda d: d["budgets"]["electronics"].update(mass_g=-1), "must be greater than 0"),
        (lambda d: d["budgets"]["electronics"].update(bay_mm=[200, 200, 22]), "does not fit"),
        (lambda d: d["interfaces"]["pin_map"].update(X="GPIO34"), "used by both"),
        (lambda d: d["interfaces"]["mounts"].update(board_holes_mm=[[99, 3]]), "outside"),
        (lambda d: d.update(extra=1), "extra"),
    ],
)
def test_check_contract_rejects(root, edit, needle):
    data = default(root)
    edit(data)
    report = check_contract(json.dumps(data))
    assert not report.ok
    assert any(needle in p.lower() or needle in p for p in report.problems), report.problems


def test_check_contract_rejects_non_json():
    assert not check_contract("{not json").ok


def test_build_rejects_an_invalid_contract(root, architect_ctx):
    architect_ctx.contract_path.parent.mkdir(parents=True)
    architect_ctx.contract_path.write_text(contract_text(root, mech=390, elec=60))
    with pytest.raises(ValueError, match="mass_g_sum"):
        ArchitectStage().build(architect_ctx)


def set_requirement(root, number, check, limit, unit="g"):
    import yaml

    from tests.agents.conftest import requirement

    req = requirement(number, check, limit)
    req["unit"] = unit
    (root / "projects" / "demo" / "spec" / f"R-{number:03d}.yaml").write_text(
        yaml.safe_dump(req), encoding="utf-8"
    )


def test_budgets_come_from_this_projects_mass_requirement(root, architect_ctx):
    set_requirement(root, 1, "mass_budget", {"max": 250})
    stage = ArchitectStage()
    stage.generate(architect_ctx)
    stage.build(architect_ctx)
    contract = json.loads(architect_ctx.contract_path.read_text())
    budgets = contract["budgets"]
    mech, elec = budgets["mechanical"]["mass_g"], budgets["electronics"]["mass_g"]
    assert contract["totals"]["mass_g_max"] == 250
    assert mech + elec <= 250 * 0.9 + 0.01  # a margin is kept back
    assert mech > elec > 0
    assert check_contract(architect_ctx.contract_path.read_text()).ok
    sources = stage.interface(architect_ctx)["budget_sources"]
    assert sources["totals.mass_g_max"] == sources["budgets.mechanical.mass_g"] == "R-001"
    assert sources["budgets.electronics.mass_g"] == "R-001"
    assert sources["budgets.firmware.ram_kb"] == "pack default"  # no requirement sets it
    beside = json.loads((architect_ctx.contract_path.parent / "budget_sources.json").read_text())
    assert beside == sources


def test_the_strictest_mass_requirement_wins(root, architect_ctx):
    set_requirement(root, 5, "mass_budget", {"max": 320})
    ArchitectStage().generate(architect_ctx)
    contract = json.loads(architect_ctx.contract_path.read_text())
    assert contract["totals"]["mass_g_max"] == 320  # R-005 is stricter than R-001's 400


def test_a_draft_requirement_sets_no_budget(root, architect_ctx):
    set_requirement(root, 5, "mass_budget", {"max": 100})
    import yaml

    path = root / "projects" / "demo" / "spec" / "R-005.yaml"
    data = yaml.safe_load(path.read_text())
    data["status"] = "draft"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    ArchitectStage().generate(architect_ctx)
    assert json.loads(architect_ctx.contract_path.read_text())["totals"]["mass_g_max"] == 400


def test_a_power_requirement_sets_the_energy_budget(root, architect_ctx):
    set_requirement(root, 6, "power_budget", {"max": 5}, unit="mW")
    stage = ArchitectStage()
    stage.generate(architect_ctx)
    contract = json.loads(architect_ctx.contract_path.read_text())
    assert contract["budgets"]["electronics"]["energy_mwh_per_day"] == 108  # 5 mW x 24 h x 0.9
    sources = json.loads((architect_ctx.contract_path.parent / "budget_sources.json").read_text())
    assert sources["budgets.electronics.energy_mwh_per_day"] == "R-006"


def test_without_a_mass_requirement_the_pack_default_stays(root, architect_ctx):
    (root / "projects" / "demo" / "spec" / "R-001.yaml").unlink()
    ArchitectStage().generate(architect_ctx)
    assert json.loads(architect_ctx.contract_path.read_text()) == default(root)
