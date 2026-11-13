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
    assert json.loads(architect_ctx.contract_path.read_text())["budgets"]["mechanical"][
        "mass_g"] == 280
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
