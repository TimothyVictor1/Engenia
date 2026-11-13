import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from engenia.contracts.models import EvidenceLevel, Limit, Verdict, VerdictResult
from engenia.harness.checks import all_pass, run_stage_checks, write_verdicts


def make_verdict(result: str) -> Verdict:
    return Verdict(
        check="c",
        measured=1.0,
        limit=Limit(max=2),
        result=VerdictResult(result),
        evidence_level=EvidenceLevel.CALCULATED,
    )


def write_verifier(root, name, body):
    (root / "verifiers" / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")


def plan(root, *items):
    (root / "projects" / "demo" / "checks" / "checks.yaml").write_text(
        yaml.safe_dump(list(items)), encoding="utf-8"
    )


def item(check, requirement="R-001", number=1):
    return {
        "id": f"C-{number:03d}",
        "requirement": requirement,
        "check": check,
        "limit": {"max": 400},
    }


def test_all_pass_only_on_pass_results():
    assert all_pass([make_verdict("pass"), make_verdict("pass")])
    assert not all_pass([make_verdict("pass"), make_verdict("fail")])
    assert not all_pass([make_verdict("error")])
    assert all_pass([])


def test_runs_verifier_for_every_plan_entry(root, ctx, stage):
    plan(root, item("mass_budget", "R-001", 1), item("mass_budget", "R-002", 2))
    (ctx.src_dir).mkdir(parents=True)
    (ctx.src_dir / "design.txt").write_text("mass=100")
    verdicts = run_stage_checks(stage, ctx)
    assert [(v.requirement, v.result.value) for v in verdicts] == [
        ("R-001", "pass"),
        ("R-002", "pass"),
    ]


def test_stage_with_no_checks_has_no_verdicts(root, ctx, make_stage):
    assert run_stage_checks(make_stage(checks=[]), ctx) == []


def test_missing_plan_entry_is_an_error_not_a_pass(root, ctx, stage):
    plan(root)
    (verdict,) = run_stage_checks(stage, ctx)
    assert verdict.result == VerdictResult.ERROR
    assert "check plan" in verdict.message


def test_missing_verifier_file_is_an_error(root, ctx, make_stage):
    plan(root, item("ghost"))
    (verdict,) = run_stage_checks(make_stage(checks=["ghost"]), ctx)
    assert verdict.result == VerdictResult.ERROR
    assert verdict.requirement == "R-001"
    assert "ghost" in verdict.message


def test_crashing_verifier_is_an_error(root, ctx, make_stage):
    write_verifier(root, "crash", "def run(ctx):\n    raise ZeroDivisionError('x')\n")
    plan(root, item("crash"))
    (verdict,) = run_stage_checks(make_stage(checks=["crash"]), ctx)
    assert verdict.result == VerdictResult.ERROR
    assert "ZeroDivisionError" in verdict.message


def test_verifier_returning_non_verdict_is_an_error(root, ctx, make_stage):
    write_verifier(root, "liar", "def run(ctx):\n    return 'pass'\n")
    plan(root, item("liar"))
    (verdict,) = run_stage_checks(make_stage(checks=["liar"]), ctx)
    assert verdict.result == VerdictResult.ERROR
    assert "not Verdict" in verdict.message


def test_broken_check_plan_is_an_error(root, ctx, stage):
    (root / "projects" / "demo" / "checks" / "checks.yaml").write_text("- id: nonsense\n")
    (verdict,) = run_stage_checks(stage, ctx)
    assert verdict.result == VerdictResult.ERROR
    assert "unreadable" in verdict.message


def test_verifier_name_cannot_escape_verifiers_dir(root, ctx, make_stage):
    (verdict,) = run_stage_checks(make_stage(checks=["../evil"]), ctx)
    assert verdict.result == VerdictResult.ERROR


def test_edited_verifier_is_reloaded(root, ctx, make_stage):
    plan(root, item("flip"))
    stage = make_stage(checks=["flip"])
    body = """
        from engenia.contracts.models import EvidenceLevel, Verdict, VerdictResult
        def run(ctx):
            return Verdict(check='flip', measured=1.0, limit=ctx.limit,
                           result=VerdictResult.{result}, evidence_level=EvidenceLevel.CALCULATED)
    """
    write_verifier(root, "flip", body.format(result="FAIL"))
    assert run_stage_checks(stage, ctx)[0].result == VerdictResult.FAIL
    write_verifier(root, "flip", body.format(result="PASS"))  # same size, same second
    assert run_stage_checks(stage, ctx)[0].result == VerdictResult.PASS
    assert not (root / "verifiers" / "__pycache__").exists()  # loading writes nothing there


def test_write_verdicts_round_trips(ctx):
    write_verdicts(ctx, [make_verdict("fail")])
    saved = json.loads((ctx.build_dir / "verdict.json").read_text())
    assert Verdict.model_validate(saved[0]).result == VerdictResult.FAIL


# --- verifiers that import shared helpers --------------------------------------------------------

HELPER = "def helper():\n    return 42.0\n"

VERIFIER_USING_HELPER = """
    {import_line}
    from engenia.contracts.models import EvidenceLevel, Verdict, VerdictResult

    def run(ctx):
        return Verdict(check=ctx.check, requirement=ctx.requirement, measured=helper(),
                       limit=ctx.limit, result=VerdictResult.PASS,
                       evidence_level=EvidenceLevel.CALCULATED)
"""


def write_helper(root, text=HELPER):
    """Shared helper module, in a package of its own so the real repository's cannot win."""
    (root / "verifiers" / "__init__.py").write_text("")
    (root / "verifiers" / "_common.py").write_text(text)


def without_root_on_sys_path(monkeypatch, root):
    """Make the repository root absent from sys.path, as under the `engenia` console script."""
    wanted = root.resolve()
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path if Path(p or ".").resolve() != wanted]
    )
    for name in [m for m in sys.modules if m == "verifiers" or m.startswith("verifiers.")]:
        monkeypatch.delitem(sys.modules, name)


@pytest.mark.parametrize(
    "import_line",
    ["from verifiers._common import helper", "from ._common import helper"],
    ids=["absolute", "relative"],
)
def test_verifier_can_import_shared_helpers_when_root_is_not_on_sys_path(
    root, ctx, make_stage, monkeypatch, import_line
):
    write_helper(root)
    write_verifier(root, "demo", VERIFIER_USING_HELPER.format(import_line=import_line))
    plan(root, item("demo"))
    without_root_on_sys_path(monkeypatch, root)
    assert str(root.resolve()) not in [str(Path(p or ".").resolve()) for p in sys.path]

    (verdict,) = run_stage_checks(make_stage(checks=["demo"]), ctx)

    assert verdict.result == VerdictResult.PASS  # the verifier's own verdict, not an error
    assert verdict.measured == 42.0
    assert verdict.message is None
    assert not (root / "verifiers" / "__pycache__").exists()


def test_root_already_on_sys_path_is_not_added_twice(root, ctx, make_stage, monkeypatch):
    write_helper(root)
    import_line = "from verifiers._common import helper"
    write_verifier(root, "demo", VERIFIER_USING_HELPER.format(import_line=import_line))
    plan(root, item("demo"))
    without_root_on_sys_path(monkeypatch, root)
    stage = make_stage(checks=["demo"])
    run_stage_checks(stage, ctx)
    run_stage_checks(stage, ctx)
    assert [Path(p).resolve() for p in sys.path].count(root.resolve()) == 1


def test_an_edited_helper_is_picked_up_on_the_next_run(root, ctx, make_stage, monkeypatch):
    import_line = "from verifiers._common import helper"
    write_helper(root)
    write_verifier(root, "demo", VERIFIER_USING_HELPER.format(import_line=import_line))
    plan(root, item("demo"))
    without_root_on_sys_path(monkeypatch, root)
    stage = make_stage(checks=["demo"])
    assert run_stage_checks(stage, ctx)[0].measured == 42.0

    write_helper(root, HELPER.replace("42.0", "7.0"))
    assert run_stage_checks(stage, ctx)[0].measured == 7.0  # an edited helper is picked up
