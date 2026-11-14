"""A scripted failure must never publish a successful cache/run outcome."""
import json

import pytest
from typer.testing import CliRunner

from engenia.contracts.models import StageContext
from engenia.core.cache import Cache
from engenia.core.cli import app
from engenia.core.engine import execute
from engenia.core.hashing import compute_keys
from engenia.core.runstate import RunStore

from .conftest import EXAMPLE_PROJECT, FakeStage


@pytest.mark.parametrize('result', ['fail', 'error'])
def test_scripted_failure_blocks_dependants_and_is_not_cached(
    root, recorder, monkeypatch, result,
):
    stage = FakeStage('mechanical', [], recorder)
    stage.checks = ['wall_thickness_min']
    downstream = FakeStage('integration', ['mechanical'], recorder)
    independent = FakeStage('redteam', [], recorder)
    registry = {s.name: s for s in (stage, downstream, independent)}
    ctx = StageContext(root=root, project=EXAMPLE_PROJECT, stage='mechanical')
    ctx.checks_path.parent.mkdir()
    ctx.checks_path.write_text('''- id: C-001
  requirement: R-001
  check: wall_thickness_min
  limit: {min: 3}
''')
    verifier = root / 'verifiers/wall_thickness_min.py'
    verifier.parent.mkdir()
    verifier.write_text(f'''from engenia.contracts.models import Verdict
def run(ctx):
    return Verdict(check=ctx.check, requirement=ctx.requirement,
                   measured=1, limit=ctx.limit, result={result!r}, evidence_level="calculated")
''')
    monkeypatch.setattr('engenia.core.engine._default_registry', lambda: registry)
    outcome = CliRunner().invoke(app, ['run', EXAMPLE_PROJECT])
    assert outcome.exit_code == 1, outcome.output
    assert 'scripted checks failed' in outcome.output
    assert 'integration' not in recorder.stages('build')
    assert 'redteam' in recorder.stages('build')
    assert not Cache(root / 'store').has(compute_keys(stage, ctx).stage_key)
    assert json.loads((ctx.build_dir / 'verdict.json').read_text())[0]['result'] == result
    store = RunStore(root)
    try:
        assert store.runs()[0]['status'] == 'failed'
        statuses = {s['stage']: s['status'] for s in store.stage_runs(1)}
        assert statuses['mechanical'] == 'failed'
        assert statuses['integration'] == 'blocked'
    finally:
        store.close()


def test_final_build_owned_failure_fails_run(root, recorder):
    class Final(FakeStage):
        def build(self, ctx):
            super().build(ctx)
            (ctx.build_dir / 'verdict.json').write_text(json.dumps([{
                'check': 'final_gate', 'measured': 1, 'limit': {'equals': 0},
                'result': 'fail', 'evidence_level': 'calculated',
            }]))
    final = Final('final', [], recorder)
    report = execute(EXAMPLE_PROJECT, root, registry={'final': final})
    assert not report.ok
    assert 'final' in report.failed


def test_verifier_edit_invalidates_a_previously_passing_cache(root, recorder):
    stage = FakeStage('mechanical', [], recorder)
    stage.checks = ['wall_thickness_min']
    ctx = StageContext(root=root, project=EXAMPLE_PROJECT, stage='mechanical')
    ctx.checks_path.parent.mkdir()
    ctx.checks_path.write_text('''- id: C-001
  requirement: R-001
  check: wall_thickness_min
  limit: {min: 3}
''')
    path = root / 'verifiers/wall_thickness_min.py'
    path.parent.mkdir()
    source = '''from engenia.contracts.models import Verdict
def run(ctx):
    return Verdict(check=ctx.check, requirement=ctx.requirement,
                   measured=4, limit=ctx.limit, result="pass", evidence_level="calculated")
'''
    path.write_text(source)
    reg = {'mechanical': stage}
    first = execute(EXAMPLE_PROJECT, root, registry=reg)
    assert first.ok
    assert execute(EXAMPLE_PROJECT, root, registry=reg).results[0].status == 'cached'
    failing = source.replace('measured=4', 'measured=1').replace('result="pass"', 'result="fail"')
    path.write_text(failing)
    failed = execute(EXAMPLE_PROJECT, root, registry=reg)
    assert not failed.ok
    assert 'wall_thickness_min: fail' in failed.failed['mechanical']


def test_resume_rejects_a_failed_stored_verdict(root, recorder):
    mechanical = FakeStage('mechanical', [], recorder)
    integration = FakeStage('integration', ['mechanical'], recorder, fail_in='build')
    registry = {'mechanical': mechanical, 'integration': integration}
    first = execute(EXAMPLE_PROJECT, root, registry=registry)
    assert 'integration' in first.failed
    ctx = StageContext(root=root, project=EXAMPLE_PROJECT, stage='mechanical')
    (ctx.build_dir / 'verdict.json').write_text(json.dumps([{
        'check': 'build', 'measured': 1, 'limit': {'equals': 0},
        'result': 'fail', 'evidence_level': 'calculated',
    }]))
    resumed = execute(EXAMPLE_PROJECT, root, registry=registry, resume=True)
    assert resumed.run_id == first.run_id
    assert 'mechanical' in resumed.failed
    assert resumed.blocked == {'integration': ['mechanical']}


def test_repair_can_fix_an_early_scripted_failure(root, recorder):
    from types import SimpleNamespace

    from engenia.core.repair_loop import run_with_repair

    stage = FakeStage('mechanical', [], recorder)
    stage.checks = ['wall_thickness_min']
    ctx = StageContext(root=root, project=EXAMPLE_PROJECT, stage='mechanical')
    ctx.checks_path.parent.mkdir()
    ctx.checks_path.write_text('''- id: C-001
  requirement: R-001
  check: wall_thickness_min
  limit: {min: 3}
''')
    path = root / 'verifiers/wall_thickness_min.py'
    path.parent.mkdir()
    path.write_text('''from engenia.contracts.models import Verdict
def run(ctx):
    fixed = (ctx.src_dir / "code.txt").read_text() == "fixed"
    return Verdict(check=ctx.check, requirement=ctx.requirement,
                   measured=4 if fixed else 1, limit=ctx.limit,
                   result="pass" if fixed else "fail", evidence_level="calculated")
''')
    calls = []

    def repair(stage, ctx, failed):
        calls.append([v.check for v in failed])
        (ctx.src_dir / 'code.txt').write_text('fixed')
        return 'repaired'

    report = run_with_repair(
        EXAMPLE_PROJECT, root, registry={'mechanical': stage}, repair=repair,
        gate=lambda *a, **kw: SimpleNamespace(passed=True, report={'verdicts': []}),
    )
    assert report.passed
    assert calls == [['wall_thickness_min']]
    assert report.run.ok
