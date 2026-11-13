import pytest
import yaml

from engenia.agents.intake import propose_change
from engenia.contracts.models import EvidenceLevel, Limit, Requirement, Verdict, VerdictResult
from engenia.core.lint import Severity, lint_spec_dir
from engenia.core.spec import load_requirements


def requirement(limit, value=30, statement="The buoy shall keep its tilt within 30 degrees."):
    return Requirement.model_validate({
        "id": "R-011", "statement": statement, "value": value, "unit": "deg",
        "verify": {"method": "simulation", "check": "wave_tilt", "limit": limit},
        "affects": ["simulation"], "status": "approved",
    })


def verdict(measured, limit, message="tilt too high", requirement_id="R-011",
            result=VerdictResult.FAIL):
    return Verdict(check="wave_tilt", requirement=requirement_id, measured=measured,
                   limit=Limit(**limit), result=result,
                   evidence_level=EvidenceLevel.SIMULATED, message=message)


def lint_clean(path, root):
    findings = lint_spec_dir(path.parent, root / "verifiers")
    assert [f for f in findings if f.severity is Severity.ERROR] == []


def test_max_limit_is_relaxed_and_passes_lint(ctx, root):
    req = requirement({"max": 30})
    path = propose_change(req, [verdict(34.2, {"max": 30})], ctx=ctx)

    assert path == ctx.spec_dir / "proposals" / "R-011.yaml"
    data = yaml.safe_load(path.read_text())
    assert data["verify"]["limit"] == {"max": 36}
    assert data["value"] == 36 and "36 degrees" in data["statement"]
    assert data["status"] == "draft"
    lint_clean(path, root)
    assert Requirement.model_validate(data).id == "R-011"


def test_reason_cites_the_failed_verdicts(ctx):
    path = propose_change(requirement({"max": 30}), [verdict(34.2, {"max": 30})], ctx=ctx)
    text = path.read_text()
    assert "wave_tilt (fail): measured 34.2" in text and "tilt too high" in text
    assert text.splitlines()[0].startswith("# Proposed change to R-011")


def test_min_limit_is_lowered(ctx, root):
    req = requirement({"min": 2}, value=2, statement="The hull shall keep a factor of 2.")
    path = propose_change(req, [verdict(1.6, {"min": 2})], ctx=ctx)
    data = yaml.safe_load(path.read_text())
    assert data["verify"]["limit"] == {"min": 1.52}
    lint_clean(path, root)


def test_value_that_was_not_the_limit_is_kept(ctx):
    req = requirement({"max": 30}, value=25, statement="The buoy shall tilt about 25 degrees.")
    data = yaml.safe_load(propose_change(req, [verdict(34.2, {"max": 30})], ctx=ctx).read_text())
    assert data["value"] == 25


def test_proposal_does_not_disturb_the_spec(ctx, root):
    (ctx.spec_dir / "R-011.yaml").write_text(yaml.safe_dump(
        requirement({"max": 30}).model_dump(mode="json")))
    propose_change(requirement({"max": 30}), [verdict(34.2, {"max": 30})], ctx=ctx)
    assert [r.id for r in load_requirements(ctx.spec_dir)] == ["R-011"]
    assert load_requirements(ctx.spec_dir)[0].verify.limit.max == 30


def test_verdicts_for_other_requirements_are_ignored(ctx):
    with pytest.raises(ValueError):
        propose_change(requirement({"max": 30}),
                       [verdict(34.2, {"max": 30}, requirement_id="R-002")], ctx=ctx)


def test_error_verdict_without_a_number_gives_no_proposal(ctx):
    with pytest.raises(ValueError):
        propose_change(requirement({"max": 30}),
                       [verdict(None, {"max": 30}, result=VerdictResult.ERROR)], ctx=ctx)
    assert not (ctx.spec_dir / "proposals").exists()
