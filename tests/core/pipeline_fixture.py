"""Prepare verifier inputs and explicit approval in scratch pipeline fixtures."""
import shutil
from pathlib import Path

from engenia.agents.verification.stage import deterministic_plan, dump_plan, load_requirements
from engenia.core.hashing import file_hash


def approve_fixture(root: Path, project: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    for name in ('packs', 'verifiers'):
        shutil.copytree(repo / name, root / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    directory = root / 'projects' / project
    checks = directory / 'checks/checks.yaml'
    checks.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    pack = yaml.safe_load((root / 'packs/buoy/pack.yaml').read_text())
    checks.write_text(dump_plan(deterministic_plan(load_requirements(directory / 'spec'), pack)))
    (checks.parent / 'APPROVED').write_text(file_hash(checks) + '\n')
