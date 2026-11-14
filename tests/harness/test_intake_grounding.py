"""Intake is grounded in the checks that exist: no invented check names, testable limits,
validation with retries, the pack template as fallback, and design intents kept apart."""

from pathlib import Path

import pytest
import yaml

from engenia.agents.intake import IntakeError, intake, intake_report
from engenia.agents.intake import registry as registry_module
from engenia.agents.intake.registry import check_requirement, load_registry
from engenia.contracts.models import Requirement
from engenia.core.lint import Severity, lint_data
from engenia.harness.llm import FakeLLM, text_response, tool_call_response

REPO = Path(__file__).resolve().parents[2]
REQUEST = "The buoy must weigh no more than 400 g and survive 0.5 m waves."
NAMES = ("mass_budget", "wave_tilt", "buoyancy_margin")


def req(number: int, check: str, **over) -> str:
    data = {
        "id": f"R-{number:03d}",
        "statement": "The total mass of the buoy shall not exceed 400 g.",
        "value": 400,
        "unit": "g",
        "verify": {"method": "analysis", "check": check, "limit": {"max": 400}},
        "affects": ["mechanical"],
        "status": "draft",
    }
    data.update(over)
    return yaml.safe_dump(data, sort_keys=False)


def tilt(number: int = 2) -> str:
    return req(
        number, "wave_tilt",
        statement="The buoy shall keep its tilt within 30 degrees in waves up to 0.5 m.",
        value=30, unit="deg", affects=["simulation"],
        verify={"method": "simulation", "check": "wave_tilt", "limit": {"max": 30}},
    )


def write(name: str, content: str):
    return ("write_file", {"path": name, "content": content})


@pytest.fixture
def full_root(root):
    for name in NAMES:
        (root / "verifiers" / f"{name}.py").write_text('"""stub."""\n', encoding="utf-8")
    return root


def run(llm, text=REQUEST, project="demo"):
    return intake_report(text, project, model="test-model", chat_fn=llm)


def sent_text(llm, call_index):
    return "\n".join(str(m.get("content")) for m in llm.calls[call_index].messages)


def spec_names(root):
    return sorted(p.name for p in (root / "projects" / "demo" / "spec").glob("*"))


def test_prompt_lists_exactly_the_available_checks(full_root):
    llm = FakeLLM([tool_call_response(write("R-001.yaml", req(1, "mass_budget"))),
                   text_response("ok")])
    run(llm)
    system = llm.calls[0].messages[0]["content"]
    for name in NAMES:
        assert f"- {name}:" in system
    assert "- detection_rate:" not in system  # no such verifier in this root
    assert "unit: g; limit: max" in system and "unit: deg; limit: max" in system
    assert "example:" in system and "EXACTLY a name from the list" in system
    assert "survive 0.5 m waves" in system and "design_intents.yaml" in system


def test_invented_checks_are_rejected_and_fixed(full_root):
    invented = [
        ("R-001", "float_inspection"), ("R-002", "wave_survival"),
        ("R-003", "hydrophone_inspection"),
    ]
    llm = FakeLLM([
        tool_call_response(*(write(f"{i}.yaml", req(int(i[2:]), c)) for i, c in invented)),
        text_response("done"),
        tool_call_response(
            write("R-001.yaml", req(1, "mass_budget")), write("R-002.yaml", tilt(2)),
            write("R-003.yaml", req(3, "buoyancy_margin", unit="ratio", value=0.2,
                                    verify={"method": "analysis", "check": "buoyancy_margin",
                                            "limit": {"min": 0.2}},
                                    statement="The buoy shall have a buoyancy margin of at "
                                              "least 0.2.")),
        ),
        text_response("fixed"),
    ])
    report = run(llm)

    feedback = sent_text(llm, 2)
    for _, name in invented:
        assert f"unknown check '{name}'" in feedback
    assert "Use only one of: buoyancy_margin, mass_budget, wave_tilt" in feedback
    assert sorted(r.verify.check for r in report.requirements) == sorted(NAMES)
    assert report.from_template == [] and report.dropped == []
    assert len(llm.calls) == 4


def test_vague_requirement_translated_to_a_testable_one(full_root):
    llm = FakeLLM([tool_call_response(write("R-001.yaml", tilt(1))), text_response("ok")])
    (requirement,) = run(llm).requirements
    assert requirement.verify.check == "wave_tilt"
    assert requirement.value == 30 and requirement.unit == "deg"
    assert requirement.verify.limit.max == 30


def test_run_build_tool_reports_unknown_checks(full_root):
    llm = FakeLLM([
        tool_call_response(write("R-001.yaml", req(1, "material_inspection")), ("run_build", {})),
        tool_call_response(write("R-001.yaml", req(1, "mass_budget"))),
        text_response("done"),
    ])
    run(llm)
    result = [m for m in llm.calls[1].messages if m["role"] == "tool"][-1]["content"]
    assert "unknown check 'material_inspection'" in result and '"exit_code": 1' in result


def test_still_invalid_after_three_rounds_falls_back_to_the_template(full_root):
    llm = FakeLLM([
        tool_call_response(write("R-001.yaml", req(1, "wave_survival")),
                           write("R-002.yaml", req(2, "lora_alert_inspection"))),
        text_response("done"),
    ] * 4)
    report = run(llm)

    assert llm.remaining == 0  # initial round + 3 fix rounds
    assert report.dropped == ["R-001", "R-002"]
    (requirement,) = report.requirements  # the template found "400 g" in the request
    assert requirement.verify.check == "mass_budget" and requirement.value == 400
    assert report.from_template == [requirement.id]
    assert spec_names(full_root) == [f"{requirement.id}.yaml"]
    saved = yaml.safe_load(
        (full_root / "projects" / "demo" / "build" / "intake" / "design_intents.yaml").read_text()
    )
    assert len(saved) == 2 and all("dropped" in i["reason"] for i in saved)


def test_a_valid_file_is_kept_when_another_stays_invalid(full_root):
    llm = FakeLLM([
        tool_call_response(write("R-001.yaml", req(1, "mass_budget")),
                           write("R-002.yaml", req(2, "wave_survival"))),
        text_response("done"),
    ] * 4)
    report = run(llm)
    assert [r.id for r in report.requirements] == ["R-001"]
    assert report.dropped == ["R-002"] and report.from_template == []


def test_nothing_usable_even_after_fallback_raises(full_root):
    llm = FakeLLM([tool_call_response(write("R-001.yaml", req(1, "wave_survival"))),
                   text_response("done")] * 4)
    with pytest.raises(IntakeError, match="3 fix rounds"):
        intake("Make it good.", "demo", model="test-model", chat_fn=llm)
    assert spec_names(full_root) == []


def test_design_intents_are_listed_separately_and_not_written_as_requirements(full_root):
    intents = yaml.safe_dump(
        [{"intent": "The buoy uses a hydrophone.", "reason": "no check measures sensor choice"}]
    )
    llm = FakeLLM([
        tool_call_response(write("R-001.yaml", req(1, "mass_budget")),
                           write("design_intents.yaml", intents)),
        text_response("done"),
    ])
    report = run(llm)

    assert [r.id for r in report.requirements] == ["R-001"]
    assert report.intents == [
        {"intent": "The buoy uses a hydrophone.", "reason": "no check measures sensor choice"}
    ]
    assert spec_names(full_root) == ["R-001.yaml"]  # not in the spec
    saved = full_root / "projects" / "demo" / "build" / "intake" / "design_intents.yaml"
    assert "not verified" in saved.read_text().splitlines()[0]
    assert yaml.safe_load(saved.read_text()) == report.intents


def test_no_intents_file_when_there_are_none(full_root):
    llm = FakeLLM([tool_call_response(write("R-001.yaml", req(1, "mass_budget"))),
                   text_response("ok")])
    assert run(llm).intents == []
    assert not (full_root / "projects" / "demo" / "build" / "intake"
                / "design_intents.yaml").exists()


# --- the registry rules on their own -----------------------------------------------------------


@pytest.fixture
def registry(full_root):
    return load_registry(full_root / "verifiers")


def data(text):
    return yaml.safe_load(text)


def test_unknown_check_rule(registry):
    problems = check_requirement(data(req(1, "float_inspection")), registry)
    assert len(problems) == 1 and "unknown check 'float_inspection'" in problems[0]


def test_known_check_with_right_limit_and_unit_is_clean(registry):
    assert check_requirement(data(req(1, "mass_budget")), registry) == []


def test_limit_the_check_cannot_test_is_rejected(registry):
    wrong = data(req(1, "mass_budget", verify={
        "method": "analysis", "check": "mass_budget", "limit": {"min": 400}}))
    assert "tests a max limit" in check_requirement(wrong, registry)[0]


def test_wrong_unit_and_inspection_are_rejected(registry):
    assert "measured in g" in check_requirement(data(req(1, "mass_budget", unit="kg")),
                                                registry)[0]
    inspection = data(req(1, "mass_budget", verify={
        "method": "inspection", "check": "mass_budget", "limit": {"max": 400}}))
    assert "cannot be inspection" in check_requirement(inspection, registry)[0]


def test_measured_checks_are_not_offered(root):
    (root / "verifiers" / "measured_mass.py").write_text('"""x."""\n')
    (root / "verifiers" / "_common.py").write_text('"""x."""\n')
    assert list(load_registry(root / "verifiers")) == ["mass_budget"]


def test_unlisted_verifier_still_appears_with_its_docstring(root):
    (root / "verifiers" / "new_thing.py").write_text('"""Measures the new thing.\n\nmore"""\n')
    info = load_registry(root / "verifiers")["new_thing"]
    assert info.measures == "Measures the new thing."


def test_every_real_verifier_is_described_and_every_example_passes_lint():
    real = load_registry(REPO / "verifiers")
    assert real, "no verifiers found"
    undescribed = [n for n in real if n not in {i.name for i in registry_module._KNOWN}]
    assert undescribed == [], f"describe these verifiers in registry._KNOWN: {undescribed}"
    for name, info in real.items():
        example = yaml.safe_load(info.example)
        Requirement.model_validate(example)
        errors = [f for f in lint_data("R.yaml", example, REPO / "verifiers")
                  if f.severity is Severity.ERROR]
        assert errors == [], (name, errors)
        assert check_requirement(example, real) == [], name
        assert example["verify"]["check"] == name
