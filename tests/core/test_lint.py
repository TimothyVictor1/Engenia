"""One failing example per rule, then the command line."""

import copy

import pytest
import yaml
from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.core.lint import Severity, lint_data, lint_spec_dir
from engenia.core.spec import load_requirements

runner = CliRunner()

GOOD = {
    "id": "R-001",
    "statement": "The total mass of the buoy shall not exceed 400 g.",
    "value": 400,
    "unit": "g",
    "verify": {"method": "analysis", "check": "mass_budget", "limit": {"max": 400}},
    "affects": ["mechanical", "electronics"],
    "status": "approved",
}


def variant(**changes):
    """GOOD with changes applied. A value of ... deletes the field; verify_x edits verify.x."""
    data = copy.deepcopy(GOOD)
    for key, value in changes.items():
        target, name = (data["verify"], key[7:]) if key.startswith("verify_") else (data, key)
        if value is ...:
            del target[name]
        else:
            target[name] = value
    return data


@pytest.fixture
def verifiers(tmp_path):
    folder = tmp_path / "verifiers"
    folder.mkdir()
    (folder / "mass_budget.py").write_text("def run(ctx): ...\n")
    return folder


def rules_of(findings, severity=None):
    return sorted(f.rule for f in findings if severity is None or f.severity is severity)


def test_a_good_requirement_has_no_findings(verifiers):
    assert lint_data("R-001.yaml", GOOD, verifiers) == []


FAILING = [
    pytest.param(
        "L1",
        variant(statement="The buoy shall float and shall not leak."),
        "exactly one",
        id="L1-two-shalls",
    ),
    pytest.param("L1", variant(statement="The buoy floats."), "found 0", id="L1-no-shall"),
    pytest.param(
        "L2",
        variant(statement="The buoy shall be robust."),
        "vague word \"robust\"",
        id="L2-vague-word",
    ),
    pytest.param(
        "L3", variant(value=...), "value is required", id="L3-missing-value"
    ),
    pytest.param("L3", variant(unit=...), "unit is required", id="L3-missing-unit"),
    pytest.param(
        "L4", variant(verify_limit={}), "at least one of min, max, equals", id="L4-empty-limit"
    ),
    pytest.param(
        "L4", variant(verify_limit=...), "at least one of min, max, equals", id="L4-no-limit"
    ),
    pytest.param(
        "L5", variant(affects=["mechanical", "plumbing"]), "'plumbing'", id="L5-unknown-stage"
    ),
]


@pytest.mark.parametrize(("rule", "data", "text"), FAILING)
def test_rule_fails_its_example(rule, data, text, verifiers):
    findings = lint_data("R-001.yaml", data, verifiers)
    assert rules_of(findings, Severity.ERROR) == [rule]
    (finding,) = findings
    assert text in finding.message
    assert str(finding).startswith(f"R-001.yaml: {rule} error:")


@pytest.mark.parametrize(
    "word",
    [
        "robust", "fast", "sufficient", "adequate",
        "easy", "user-friendly", "appropriate", "as needed",
    ],
)
def test_l2_catches_every_vague_word(word, verifiers):
    data = variant(statement=f"The buoy shall be {word.upper()} to use.")
    assert rules_of(lint_data("R-001.yaml", data, verifiers)) == ["L2"]


def test_l2_matches_whole_words_only(verifiers):
    data = variant(statement="The buoy shall be fastened with a fasting-proof clip.")
    assert lint_data("R-001.yaml", data, verifiers) == []


def test_l3_does_not_apply_to_inspection(verifiers):
    data = variant(value=..., unit=..., verify_method="inspection")
    assert lint_data("R-001.yaml", data, verifiers) == []


def test_l4_does_not_apply_to_inspection(verifiers):
    data = variant(value=..., unit=..., verify_method="inspection", verify_limit=...)
    assert lint_data("R-001.yaml", data, verifiers) == []
    data = variant(value=..., unit=..., verify_method="inspection", verify_limit={})
    assert lint_data("R-001.yaml", data, verifiers) == []


def test_l4_still_applies_to_every_other_method(verifiers):
    for method in ("analysis", "simulation", "test"):
        data = variant(verify_method=method, verify_limit=...)
        assert rules_of(lint_data("R-001.yaml", data, verifiers)) == ["L4"], method


def test_l6_is_a_warning_for_a_missing_verifier(verifiers):
    data = variant(verify_check="buoyancy_margin")
    (finding,) = lint_data("R-001.yaml", data, verifiers)
    assert (finding.rule, finding.severity) == ("L6", Severity.WARNING)
    assert "verifiers/buoyancy_margin.py" in finding.message


def test_l6_never_looks_outside_the_verifiers_folder(verifiers, tmp_path):
    (tmp_path / "secret.py").write_text("")
    data = variant(verify_check="../secret")
    assert rules_of(lint_data("R-001.yaml", data, verifiers)) == ["L6"]


def test_every_rule_reports_in_one_pass(verifiers):
    data = variant(
        statement="The buoy shall be robust and shall float.",
        value=...,
        verify_limit={},
        affects=["plumbing"],
        verify_check="nope",
    )
    assert rules_of(lint_data("R-001.yaml", data, verifiers)) == [
        "L1", "L2", "L3", "L4", "L5", "L6",
    ]


def test_schema_problems_the_rules_do_not_cover_are_reported_as_s0(verifiers):
    findings = lint_data("R-001.yaml", variant(owner="tim"), verifiers)
    assert rules_of(findings) == ["S0"]
    assert "owner" in findings[0].message


def test_s0_is_not_repeated_when_a_rule_already_explained_the_file(verifiers):
    findings = lint_data("R-001.yaml", variant(statement="The buoy floats."), verifiers)
    assert rules_of(findings) == ["L1"]


def test_a_file_that_is_not_a_mapping(verifiers):
    assert rules_of(lint_data("R-001.yaml", ["a", "list"], verifiers)) == ["S0"]


def test_ids_are_checked_across_the_folder(project_dir, root):
    spec_dir = project_dir / "spec"
    (spec_dir / "R-004.yaml").write_text((spec_dir / "R-001.yaml").read_text())
    findings = lint_spec_dir(spec_dir, root / "verifiers")
    errors = {(f.file, f.rule) for f in findings if f.severity is Severity.ERROR}
    assert errors == {
        ("R-004.yaml", "S1"),
        ("R-001.yaml", "S2"),
        ("R-004.yaml", "S2"),
    }


def test_bad_yaml_is_an_s0_finding(project_dir, root):
    (project_dir / "spec" / "R-002.yaml").write_text("id: [unclosed\n")
    findings = lint_spec_dir(project_dir / "spec", root / "verifiers")
    assert [(f.file, f.rule) for f in findings if f.severity is Severity.ERROR] == [
        ("R-002.yaml", "S0")
    ]


# --- engenia lint <project> ----------------------------------------------------------------


def test_cli_passes_the_fixture_spec_with_warnings(root):
    result = runner.invoke(app, ["lint", "harbour-listener"])
    assert result.exit_code == 0, result.output
    assert "L6 warning" in result.output
    assert "0 error(s), 3 warning(s)" in result.output
    # warnings do not stop a file being ok; they follow its ok line
    assert result.output.splitlines()[:2] == [
        "ok   R-001.yaml",
        "R-001.yaml: L6 warning: verify.check 'mass_budget' has no file verifiers/mass_budget.py",
    ]


def test_cli_is_clean_once_the_verifiers_exist(root):
    (root / "verifiers").mkdir()
    spec_dir = root / "projects" / "harbour-listener" / "spec"
    checks = {r.verify.check for r in load_requirements(spec_dir)}
    assert len(checks) == 3
    for name in checks:  # one verifier file for every check the fixture spec names
        (root / "verifiers" / f"{name}.py").write_text("")
    result = runner.invoke(app, ["lint", "harbour-listener"])
    assert result.exit_code == 0
    assert "0 error(s), 0 warning(s)" in result.output
    assert [ln for ln in result.output.splitlines() if ln.startswith("ok")] == [
        "ok   R-001.yaml",
        "ok   R-003.yaml",
        "ok   R-005.yaml",
    ]


def test_cli_does_not_print_ok_for_a_file_with_an_error(root, project_dir, edit):
    edit(project_dir / "spec" / "R-003.yaml", "shall operate", "shall operate a robust")
    result = runner.invoke(app, ["lint", "harbour-listener"])
    lines = result.output.splitlines()
    assert result.exit_code == 1
    assert "ok   R-001.yaml" in lines and "ok   R-005.yaml" in lines
    assert any(ln.startswith("R-003.yaml: L2 error") for ln in lines)
    assert not any(ln.startswith("ok   R-003") for ln in lines)


@pytest.mark.parametrize(("rule", "data", "text"), FAILING)
def test_cli_exits_1_on_each_failing_example(rule, data, text, project_dir, root):
    (project_dir / "spec" / "R-009.yaml").write_text(
        yaml.safe_dump({**data, "id": "R-009"}, sort_keys=False)
    )
    result = runner.invoke(app, ["lint", "harbour-listener"])
    assert result.exit_code == 1, result.output
    assert f"R-009.yaml: {rule} error:" in result.output
    assert text in result.output


def test_cli_unknown_project_exits_1(root):
    assert runner.invoke(app, ["lint", "nope"]).exit_code == 1
