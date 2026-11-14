"""`engenia intake`: a plain-language request to draft requirement files."""

import yaml
from typer.testing import CliRunner

from engenia.core.cli import app

runner = CliRunner()
REQUEST = (
    "The buoy must weigh no more than 400 g. It should run for 7 days on one battery. "
    "Walls at least 3 mm thick; buoyancy margin of 0.2. Keep average power under 25 mW. "
    "Detection rate of 0.9 for a 200 Hz tone, at most 2 false alarms per hour. "
    "Tilt within 30 degrees. Safety factor of 2 at 3 m."
)


def spec_files(root, project="newbuoy"):
    return sorted((root / "projects" / project / "spec").glob("R-*.yaml"))


def test_without_a_model_the_pack_template_writes_drafts_that_pass_lint(root, monkeypatch):
    for name in ("ENGENIA_LLM_API_KEY", "ENGENIA_LLM_BASE_URL", "ENGENIA_DESIGN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(app, ["intake", REQUEST, "--project", "newbuoy"])
    assert result.exit_code == 0, result.output
    assert "template (buoy pack" in result.output
    data = [yaml.safe_load(p.read_text()) for p in spec_files(root)]
    assert [d["id"] for d in data] == [f"R-{n:03d}" for n in range(1, 10)]
    assert {d["status"] for d in data} == {"draft"}  # intake never approves
    by_check = {d["verify"]["check"]: d for d in data}
    assert by_check["mass_budget"]["verify"]["limit"] == {"max": 400}
    assert by_check["battery_capacity"]["verify"]["limit"] == {"min": 7}
    assert by_check["false_alarm_rate"]["value"] == 2 and by_check["wave_tilt"]["value"] == 30
    assert runner.invoke(app, ["lint", "newbuoy"]).exit_code == 0


def test_a_number_the_request_does_not_give_is_not_invented(root, monkeypatch):
    monkeypatch.delenv("ENGENIA_LLM_API_KEY", raising=False)
    result = runner.invoke(app, ["intake", "It must weigh at most 250 g and look nice.",
                                 "--project", "newbuoy"])
    assert result.exit_code == 0
    assert [yaml.safe_load(p.read_text())["verify"]["check"] for p in spec_files(root)] == [
        "mass_budget"
    ]


def test_a_request_with_nothing_the_pack_knows_writes_nothing_and_exits_1(root, monkeypatch):
    monkeypatch.delenv("ENGENIA_LLM_API_KEY", raising=False)
    result = runner.invoke(app, ["intake", "Make it good.", "--project", "newbuoy"])
    assert result.exit_code == 1 and "nothing was written" in result.output
    assert spec_files(root) == []


def test_running_intake_twice_adds_nothing_and_never_touches_an_approved_file(
    root, project_dir, monkeypatch
):
    monkeypatch.delenv("ENGENIA_LLM_API_KEY", raising=False)
    before = {p.name: p.read_bytes() for p in (project_dir / "spec").glob("R-*.yaml")}
    # the fixture has no power requirement; ids continue after R-005
    ask = ["intake", "Draw at most 25 mW of power.", "--project", "harbour-listener"]
    first = runner.invoke(app, ask)
    assert first.exit_code == 0 and (project_dir / "spec" / "R-006.yaml").is_file()
    again = runner.invoke(app, ask)
    assert again.exit_code == 1
    for name, content in before.items():
        assert (project_dir / "spec" / name).read_bytes() == content


def test_with_model_keys_the_model_is_used(root, monkeypatch):
    from engenia.contracts.models import Requirement

    for name in ("ENGENIA_LLM_API_KEY", "ENGENIA_LLM_BASE_URL", "ENGENIA_DESIGN_MODEL"):
        monkeypatch.setenv(name, "x")
    seen = []

    def fake(text, project):
        seen.append((text, project))
        return [Requirement.model_validate({
            "id": "R-001", "statement": "The buoy shall exist.", "value": None,
            "verify": {"method": "inspection", "check": "exists"}, "affects": ["mechanical"],
            "status": "draft",
        })]

    monkeypatch.setattr("engenia.agents.intake.intake", fake)
    result = runner.invoke(app, ["intake", "Anything.", "--project", "newbuoy"])
    assert seen == [("Anything.", "newbuoy")]
    assert "from the model" in result.output


def test_intake_prints_check_value_unit_and_limit_for_each_requirement(root, monkeypatch):
    for name in ("ENGENIA_LLM_API_KEY", "ENGENIA_LLM_BASE_URL", "ENGENIA_DESIGN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(app, ["intake", "It must weigh at most 250 g. Tilt within 30 degrees.",
                                 "--project", "newbuoy"])
    assert result.exit_code == 0, result.output
    assert "check: mass_budget; value: 250 g; limit: max 250" in result.output
    assert "check: wave_tilt; value: 30 deg; limit: max 30" in result.output
    assert "design intents" not in result.output  # none, so no empty list


def test_design_intents_print_as_a_separate_not_verified_list(root, monkeypatch):
    from engenia.contracts.models import Requirement

    for name in ("ENGENIA_LLM_API_KEY", "ENGENIA_LLM_BASE_URL", "ENGENIA_DESIGN_MODEL"):
        monkeypatch.setenv(name, "x")

    def fake(text, project):
        build = root / "projects" / project / "build" / "intake"
        build.mkdir(parents=True)
        (build / "design_intents.yaml").write_text(yaml.safe_dump(
            [{"intent": "The buoy uses a hydrophone.", "reason": "no check measures it"}]
        ))
        data = {
            "id": "R-001", "statement": "The buoy shall weigh at most 400 g.", "value": 400,
            "unit": "g", "verify": {"method": "analysis", "check": "mass_budget",
                                    "limit": {"max": 400}},
            "affects": ["mechanical"], "status": "draft",
        }
        (root / "projects" / project / "spec").mkdir(parents=True, exist_ok=True)
        (root / "projects" / project / "spec" / "R-001.yaml").write_text(yaml.safe_dump(data))
        return [Requirement.model_validate(data)]

    monkeypatch.setattr("engenia.agents.intake.intake", fake)
    out = runner.invoke(app, ["intake", "Anything.", "--project", "newbuoy"]).output
    assert "check: mass_budget; value: 400 g; limit: max 400" in out
    assert "design intents (not verified):" in out
    listing = out.split("design intents (not verified):")[1]
    assert "The buoy uses a hydrophone. (no check measures it)" in listing
    assert "R-001" not in listing  # intents are not mixed into the requirements
