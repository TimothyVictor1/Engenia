"""`engenia approve` and `engenia release`: the human sign-offs."""

import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from engenia.core import release as release_module
from engenia.core.cli import app

runner = CliRunner()
PROJECT = "harbour-listener"


def sha(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def checks(project_dir):
    folder = project_dir / "checks"
    folder.mkdir()

    def write(text):
        (folder / "checks.yaml").write_text(text)
        (folder / "checks.sha256").write_text(sha(text) + "\n")

    write("- id: C-005\n  requirement: R-005\n  check: buoyancy_margin\n  limit: {min: 0.2}\n")
    return folder, write


def test_approve_shows_the_plan_asks_and_copies_the_hash(root, checks):
    folder, _ = checks
    result = runner.invoke(app, ["approve", PROJECT], input="y\n")
    assert result.exit_code == 0, result.output
    assert "+ - id: C-005" in result.output and "Approve this?" in result.output
    assert (folder / "APPROVED").read_text() == (folder / "checks.sha256").read_text()


def test_declining_approves_nothing(root, checks):
    folder, _ = checks
    result = runner.invoke(app, ["approve", PROJECT], input="n\n")
    assert result.exit_code == 1 and "not approved" in result.output
    assert not (folder / "APPROVED").exists()


def test_a_later_approval_shows_only_what_changed(root, checks):
    folder, write = checks
    runner.invoke(app, ["approve", PROJECT, "--yes"])
    again = runner.invoke(app, ["approve", PROJECT])
    assert again.exit_code == 0 and "already approved" in again.output
    write("- id: C-005\n  requirement: R-005\n  check: buoyancy_margin\n  limit: {min: 0.3}\n")
    result = runner.invoke(app, ["approve", PROJECT, "--yes"])
    assert "-  limit: {min: 0.2}" in result.output and "+  limit: {min: 0.3}" in result.output
    assert "+ - id: C-005" not in result.output
    assert (folder / "APPROVED").read_text() == (folder / "checks.sha256").read_text()


def test_a_stale_hash_is_refused(root, checks):
    folder, _ = checks
    (folder / "checks.yaml").write_text("- edited by hand\n")
    result = runner.invoke(app, ["approve", PROJECT, "--yes"])
    assert result.exit_code == 1 and "not the hash of the current" in result.output
    assert not (folder / "APPROVED").exists()


def test_no_plan_is_an_error(root):
    result = runner.invoke(app, ["approve", PROJECT, "--yes"])
    assert result.exit_code == 1 and "does not exist" in result.output


PROPOSAL = """# Proposed change to R-005
id: R-005
statement: The buoy shall have a buoyancy margin of at least 0.15.
value: 0.15
unit: ratio
verify:
  method: analysis
  check: buoyancy_margin
  limit: {min: 0.15}
affects: [mechanical]
status: draft
"""


def test_a_proposal_is_shown_then_accepted_as_an_approved_requirement(root, project_dir):
    proposals = project_dir / "spec" / "proposals"
    proposals.mkdir()
    (proposals / "R-005.yaml").write_text(PROPOSAL)
    declined = runner.invoke(app, ["approve", PROJECT, "--proposal", "R-005"], input="n\n")
    assert declined.exit_code == 1 and "-  limit: {min: 0.20}" in declined.output
    assert "0.20" in (project_dir / "spec" / "R-005.yaml").read_text()  # unchanged

    result = runner.invoke(app, ["approve", PROJECT, "--proposal", "R-005", "--yes"])
    assert result.exit_code == 0, result.output
    text = (project_dir / "spec" / "R-005.yaml").read_text()
    assert "min: 0.15" in text and "status: approved" in text
    assert not (proposals / "R-005.yaml").exists()
    assert "engenia approve" in result.output  # the plan must be approved again


@pytest.mark.parametrize("name", ["R-999", "../x", "R-5"])
def test_a_proposal_that_is_not_there_or_not_an_id_is_an_error(root, name):
    result = runner.invoke(app, ["approve", PROJECT, "--proposal", name, "--yes"])
    assert result.exit_code == 1


def test_a_proposal_that_fails_lint_is_refused(root, project_dir):
    proposals = project_dir / "spec" / "proposals"
    proposals.mkdir()
    (proposals / "R-005.yaml").write_text(PROPOSAL.replace("shall", "should"))
    result = runner.invoke(app, ["approve", PROJECT, "--proposal", "R-005", "--yes"])
    assert result.exit_code == 1
    assert "0.20" in (project_dir / "spec" / "R-005.yaml").read_text()


# --- release ----------------------------------------------------------------------------------


def gate(passed, failed=("G1",)):
    gates = {"approval": {"passed": True}, "G1": {"passed": passed}}
    if not passed:
        gates = {name: {"passed": False} for name in failed}
    return lambda project, root, **kw: SimpleNamespace(passed=passed, report={"gates": gates})


@pytest.fixture
def built(project_dir):
    (project_dir / "build" / "mechanical").mkdir(parents=True)
    (project_dir / "build" / "mechanical" / "out.txt").write_text("hull")
    (project_dir / "build" / "mechanical" / "transcripts").mkdir()
    (project_dir / "build" / "mechanical" / "transcripts" / "run-1.txt").write_text("chatter")
    (project_dir / "build" / "mismatches.json").write_text("[]\n")
    (project_dir / "checks").mkdir()
    (project_dir / "checks" / "APPROVED").write_text("sha256:x\n")


def test_release_refuses_when_the_gate_did_not_pass(root, built):
    with pytest.raises(release_module.ReleaseError, match="gate did not pass"):
        release_module.release(PROJECT, root, gate=gate(False))
    assert not (root / "release").exists()


def test_release_zips_build_outputs_and_evidence_under_a_content_hash(root, built):
    path = release_module.release(PROJECT, root, gate=gate(True))
    assert path.parent == root / "release" and path.name.startswith(f"{PROJECT}-")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("MANIFEST.json"))
    assert {"build/mechanical/out.txt", "build/mismatches.json", "MANIFEST.json",
            "evidence/spec/R-001.yaml", "evidence/checks/APPROVED"} <= names
    assert not any("transcripts" in n for n in names)  # chatter differs on every run
    assert set(manifest) == names - {"MANIFEST.json"}
    digest = hashlib.sha256(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()[:12]
    assert path.name == f"{PROJECT}-{digest}.zip"


def test_the_same_contents_give_the_same_zip_bytes(root, built):
    first = release_module.release(PROJECT, root, gate=gate(True))
    data = first.read_bytes()
    first.unlink()
    assert release_module.release(PROJECT, root, gate=gate(True)).read_bytes() == data


def test_changed_contents_give_a_new_name(root, built, project_dir):
    first = release_module.release(PROJECT, root, gate=gate(True))
    (project_dir / "build" / "mechanical" / "out.txt").write_text("a different hull")
    assert release_module.release(PROJECT, root, gate=gate(True)) != first


def test_the_release_command_exits_1_when_the_gate_fails(root, built, monkeypatch):
    monkeypatch.setattr("engenia.gate.gate.run_gate", gate(False))
    result = runner.invoke(app, ["release", PROJECT])
    assert result.exit_code == 1 and "nothing was released" in result.output
