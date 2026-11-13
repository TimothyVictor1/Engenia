import json
import shutil
import subprocess

import pytest

from engenia.core import hashing
from engenia.core.engine import _NOT_OUTPUTS
from engenia.core.hashing import (
    EMPTY_TREE_HASH,
    SPEC_WIDE_STAGES,
    HashError,
    canonical_json,
    compute_keys,
    file_hash,
    input_key,
    stage_key,
    tree_hash,
)
from engenia.core.spec import SpecError
from engenia.stages.registry import REGISTRY
from engenia.stages.stub import StubStage


def write_interface(project_dir, stage, facts, indent=None):
    path = project_dir / "build" / stage / "interface.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"stage": stage, "key": "sha256:" + "0" * 64, "facts": facts}
    path.write_text(json.dumps(body, indent=indent), encoding="utf-8")


@pytest.fixture
def mech(root, project_dir, make_ctx, fake_stage):
    """A mechanical stage whose upstream (architect) has an interface, plus its context."""
    write_interface(project_dir, "architect", {"hull": "round"})
    return fake_stage("mechanical"), make_ctx("mechanical")


# --- file_hash and tree_hash ----------------------------------------------------------------


def test_file_hash_known_values(tmp_path):
    (tmp_path / "abc").write_bytes(b"abc")
    (tmp_path / "empty").write_bytes(b"")
    assert file_hash(tmp_path / "abc") == (
        "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert file_hash(tmp_path / "empty") == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_canonical_json_sorts_keys_and_drops_whitespace():
    assert canonical_json({"b": [1, 2], "a": {"d": 1, "c": None}}) == (
        b'{"a":{"c":null,"d":1},"b":[1,2]}'
    )
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_tree_hash_ignores_creation_order_times_and_empty_dirs(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    for d, order in ((one, ["a", "sub/b", "c"]), (two, ["c", "sub/b", "a"])):
        for name in order:
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(f"data {name}")
    (two / "empty-dir").mkdir()
    (two / "a").touch()  # changes the file time only
    assert tree_hash(one) == tree_hash(two)


@pytest.mark.parametrize("change", ["content", "rename", "add", "remove"])
def test_tree_hash_changes_with_content_names_and_files(tmp_path, change):
    (tmp_path / "a").write_text("one")
    (tmp_path / "b").write_text("two")
    before = tree_hash(tmp_path)
    if change == "content":
        (tmp_path / "a").write_text("ONE")
    elif change == "rename":
        (tmp_path / "a").rename(tmp_path / "z")
    elif change == "add":
        (tmp_path / "c").write_text("")
    else:
        (tmp_path / "b").unlink()
    assert tree_hash(tmp_path) != before


def test_tree_hash_does_not_confuse_a_split_name_with_content(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir(), two.mkdir()
    (one / "ab").write_text("c")
    (two / "a").write_text("bc")
    assert tree_hash(one) != tree_hash(two)


def test_tree_hash_exclude_and_junk_files(tmp_path):
    (tmp_path / "a").write_text("one")
    base = tree_hash(tmp_path)
    (tmp_path / ".DS_Store").write_bytes(b"\0")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert tree_hash(tmp_path) == base
    (tmp_path / "interface.json").write_text("{}")
    assert tree_hash(tmp_path) != base
    assert tree_hash(tmp_path, exclude=["interface.json"]) == base


def test_tree_hash_of_an_empty_directory(tmp_path):
    assert tree_hash(tmp_path) == EMPTY_TREE_HASH


def test_tree_hash_rejects_symlinks(tmp_path):
    (tmp_path / "real").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(HashError, match="symlink"):
        tree_hash(tmp_path)


def test_excluding_a_directory_excludes_everything_under_it(tmp_path):
    (tmp_path / "out.txt").write_text("design")
    (tmp_path / "interface.json").write_text("{}")
    base = tree_hash(tmp_path, exclude=_NOT_OUTPUTS)
    log = tmp_path / "transcripts"
    (log / "deep").mkdir(parents=True)
    (log / "a.txt").write_text("attempt 1")
    (log / "deep" / "b.txt").write_text("attempt 2")
    (log / "deep" / "link").symlink_to(tmp_path / "out.txt")  # never looked at
    assert tree_hash(tmp_path, exclude=_NOT_OUTPUTS) == base
    (log / "a.txt").write_text("attempt 1, rewritten")
    assert tree_hash(tmp_path, exclude=_NOT_OUTPUTS) == base


@pytest.mark.parametrize(
    "name", ["transcripts.txt", "my-transcripts/x.txt", "sub/transcripts/x.txt"]
)
def test_only_the_top_level_transcripts_directory_is_excluded(tmp_path, name):
    (tmp_path / "out.txt").write_text("design")
    base = tree_hash(tmp_path, exclude=_NOT_OUTPUTS)
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("this is part of the outputs")
    assert tree_hash(tmp_path, exclude=_NOT_OUTPUTS) != base


# --- stage keys -----------------------------------------------------------------------------


def test_key_is_stable_and_well_formed(mech):
    stage, ctx = mech
    key = stage_key(stage, ctx)
    assert key == stage_key(stage, ctx)
    assert key.startswith("sha256:") and len(key) == len("sha256:") + 64
    assert input_key(stage, ctx) != key


def test_parts_are_reported_in_the_fixed_order(mech):
    keys = compute_keys(*mech)
    assert list(keys.parts) == [
        "requirements", "upstream", "contract", "src", "stage_class", "container",
    ]


def test_editing_an_affecting_requirement_changes_the_key(mech, project_dir, edit):
    stage, ctx = mech
    before = stage_key(stage, ctx)
    edit(project_dir / "spec" / "R-005.yaml", "value: 0.20", "value: 0.25")
    assert stage_key(stage, ctx) != before
    assert input_key(stage, ctx) != compute_keys(stage, ctx).stage_key


def test_editing_a_requirement_that_does_not_affect_the_stage_leaves_the_key(
    root, project_dir, make_ctx, fake_stage, edit
):
    write_interface(project_dir, "architect", {})
    stage, ctx = fake_stage("electronics"), make_ctx("electronics")
    before = stage_key(stage, ctx)
    edit(project_dir / "spec" / "R-005.yaml", "value: 0.20", "value: 0.25")  # mechanical only
    assert stage_key(stage, ctx) == before


def test_whitespace_only_change_in_a_spec_file_changes_the_key(mech, project_dir):
    """Spec files are hashed as bytes, so even a trailing newline counts. By design."""
    stage, ctx = mech
    before = stage_key(stage, ctx)
    with open(project_dir / "spec" / "R-005.yaml", "a", encoding="utf-8") as handle:
        handle.write("\n")
    assert stage_key(stage, ctx) != before


def test_a_comment_only_change_in_a_spec_file_changes_the_key(mech, project_dir):
    stage, ctx = mech
    before = stage_key(stage, ctx)
    with open(project_dir / "spec" / "R-005.yaml", "a", encoding="utf-8") as handle:
        handle.write("# reviewed\n")
    assert stage_key(stage, ctx) != before


def test_a_broken_spec_is_an_error_not_a_key(mech, project_dir):
    (project_dir / "spec" / "R-009.yaml").write_text("id: R-009\n")
    with pytest.raises(SpecError):
        stage_key(*mech)


def test_upstream_interface_change_changes_the_key(mech, project_dir):
    stage, ctx = mech
    before = stage_key(stage, ctx)
    write_interface(project_dir, "architect", {"hull": "square"})
    assert stage_key(stage, ctx) != before


def test_reformatting_an_interface_file_does_not_change_the_key(mech, project_dir):
    stage, ctx = mech
    before = stage_key(stage, ctx)
    write_interface(project_dir, "architect", {"hull": "round"}, indent=4)
    assert stage_key(stage, ctx) == before


def test_missing_upstream_interface_is_an_error(root, make_ctx, fake_stage):
    with pytest.raises(HashError, match="needs 'architect', which has no interface.json"):
        stage_key(fake_stage("mechanical"), make_ctx("mechanical"))


def test_an_interface_that_is_not_json_is_an_error(mech, project_dir):
    (project_dir / "build" / "architect" / "interface.json").write_text("{nope")
    with pytest.raises(HashError, match="not valid JSON"):
        stage_key(*mech)


def test_contract_counts_only_for_stages_that_read_it(
    root, project_dir, make_ctx, fake_stage
):
    write_interface(project_dir, "architect", {})
    contract = project_dir / "contract" / "contract.json"
    contract.parent.mkdir(parents=True)
    reader = fake_stage("mechanical", reads_contract=True)
    plain = fake_stage("mechanical", reads_contract=False)
    ctx = make_ctx("mechanical")

    absent = (stage_key(reader, ctx), stage_key(plain, ctx))
    contract.write_text('{"version": 1}')
    present = (stage_key(reader, ctx), stage_key(plain, ctx))
    contract.write_text('{ "version" : 2 }')
    edited = (stage_key(reader, ctx), stage_key(plain, ctx))
    contract.write_text('{"version":2}')
    reformatted = (stage_key(reader, ctx), stage_key(plain, ctx))

    assert len({absent[0], present[0], edited[0]}) == 3  # the reader notices every change
    assert absent[1] == present[1] == edited[1]  # the other stage never looks
    assert reformatted == edited  # canonical JSON: layout does not matter


def test_source_files_change_the_stage_key_but_not_the_input_key(mech, project_dir):
    stage, ctx = mech
    keys = compute_keys(stage, ctx)
    code = project_dir / "src" / "mechanical" / "hull.py"
    code.parent.mkdir(parents=True)
    code.write_text("x = 1\n")
    after = compute_keys(stage, ctx)
    assert after.stage_key != keys.stage_key
    assert after.input_key == keys.input_key
    code.write_text("x = 2\n")
    assert compute_keys(stage, ctx).stage_key != after.stage_key


def test_junk_files_in_src_do_not_change_the_key(mech, project_dir):
    stage, ctx = mech
    (project_dir / "src" / "mechanical").mkdir(parents=True)
    (project_dir / "src" / "mechanical" / "hull.py").write_text("x = 1\n")
    before = stage_key(stage, ctx)
    (project_dir / "src" / "mechanical" / ".DS_Store").write_bytes(b"junk")
    (project_dir / "src" / "mechanical" / "__pycache__").mkdir()
    (project_dir / "src" / "mechanical" / "__pycache__" / "hull.pyc").write_bytes(b"junk")
    assert stage_key(stage, ctx) == before


def test_the_stage_class_source_is_part_of_the_key(mech, fake_stage):
    stage, ctx = mech

    class Tweaked(type(stage)):
        def build(self, ctx):
            super().build(ctx)

    other = Tweaked(stage.name, stage.needs, stage.recorder)
    assert stage_key(other, ctx) != stage_key(stage, ctx)


def test_base_classes_in_the_engenia_package_are_part_of_the_key(
    root, project_dir, make_ctx, monkeypatch
):
    asked = []
    real = hashing.inspect.getsource
    monkeypatch.setattr(
        hashing.inspect, "getsource", lambda klass: asked.append(klass.__name__) or real(klass)
    )
    write_interface(project_dir, "verify_plan", {})

    class ProbeStage(StubStage):
        name = "architect"
        needs = ["verify_plan"]

    stage_key(ProbeStage(), make_ctx("architect"))
    assert asked == ["ProbeStage", "StubStage"]  # not Protocol, not object


def test_the_stage_name_is_part_of_the_key(root, project_dir, make_ctx, fake_stage):
    write_interface(project_dir, "architect", {})
    a = stage_key(fake_stage("mechanical"), make_ctx("mechanical"))
    b = stage_key(fake_stage("electronics"), make_ctx("electronics"))
    assert a != b


def test_container_digest_counts_for_its_own_family_only(
    root, project_dir, make_ctx, fake_stage
):
    write_interface(project_dir, "architect", {})
    mech, elec = fake_stage("mechanical"), fake_stage("electronics")
    cad = root / "containers" / "cad" / "DIGEST"
    before = (stage_key(mech, make_ctx("mechanical")), stage_key(elec, make_ctx("electronics")))

    cad.parent.mkdir(parents=True)
    cad.write_text("sha256:aaaa\n")
    first = (stage_key(mech, make_ctx("mechanical")), stage_key(elec, make_ctx("electronics")))
    cad.write_text("sha256:bbbb\n")
    second = (stage_key(mech, make_ctx("mechanical")), stage_key(elec, make_ctx("electronics")))
    cad.write_text("sha256:bbbb")  # the trailing newline is not part of the digest
    third = (stage_key(mech, make_ctx("mechanical")), stage_key(elec, make_ctx("electronics")))

    assert len({before[0], first[0], second[0]}) == 3
    assert before[1] == first[1] == second[1]
    assert third == second


def test_a_stage_class_can_name_its_own_container_family(
    root, project_dir, make_ctx, fake_stage
):
    write_interface(project_dir, "architect", {})
    stage = fake_stage("redteam")
    stage.container_family = "sim"
    plain = fake_stage("redteam")
    ctx = make_ctx("redteam")
    base, plain_base = stage_key(stage, ctx), stage_key(plain, ctx)
    digest = root / "containers" / "sim" / "DIGEST"
    digest.parent.mkdir(parents=True)
    digest.write_text("sha256:cccc")
    assert stage_key(stage, ctx) != base
    assert stage_key(plain, ctx) == plain_base  # redteam has no default family


@pytest.mark.parametrize("family", ["../x", "a/b", "", ".."])
def test_container_family_names_cannot_escape_the_containers_folder(
    root, project_dir, make_ctx, fake_stage, family
):
    write_interface(project_dir, "architect", {})
    stage = fake_stage("mechanical")
    stage.container_family = family
    if family:
        with pytest.raises(HashError, match="container family"):
            stage_key(stage, make_ctx("mechanical"))
    else:  # an empty name means "no override": fall back to the default family
        stage_key(stage, make_ctx("mechanical"))


# --- the same key on every machine ----------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_stage_key_is_identical_across_two_fresh_clones(tmp_path, fake_stage, make_ctx, root):
    """Commit a project, clone it twice into different folders, and compare keys."""
    origin = tmp_path / "origin"
    project = origin / "projects" / "harbour-listener"
    shutil.copytree(root / "projects" / "harbour-listener" / "spec", project / "spec")
    (project / "src" / "mechanical").mkdir(parents=True)
    (project / "src" / "mechanical" / "hull.py").write_text("x = 1\n")
    (project / "contract").mkdir()
    (project / "contract" / "contract.json").write_text('{"version": 1}\n')
    (origin / "containers" / "cad").mkdir(parents=True)
    (origin / "containers" / "cad" / "DIGEST").write_text("sha256:abc\n")

    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
           "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]
    subprocess.run([*git, "init", "-q", str(origin)], check=True)
    subprocess.run([*git, "-C", str(origin), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(origin), "commit", "-q", "-m", "inputs"], check=True)

    keys = []
    for name in ("clone-one", "somewhere" + "/else/clone-two"):
        clone = tmp_path / name
        clone.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([*git, "clone", "-q", str(origin), str(clone)], check=True)
        # build/ is not committed: both clones rebuild the same upstream interface
        write_interface(clone / "projects" / "harbour-listener", "architect", {"hull": "round"})
        stage = fake_stage("mechanical", reads_contract=True)
        ctx = make_ctx("mechanical", base=clone)
        keys.append((stage_key(stage, ctx), input_key(stage, ctx)))

    assert keys[0] == keys[1]
    assert keys[0][0] != keys[0][1]


# --- spec-wide stages -----------------------------------------------------------------------


def test_the_spec_wide_stages_are_the_agreed_seven():
    assert SPEC_WIDE_STAGES == {
        "verify_plan", "architect", "testplan", "simprep", "redteam", "final", "report",
    }


@pytest.mark.parametrize("name", sorted(SPEC_WIDE_STAGES))
def test_a_spec_wide_stage_changes_key_when_any_requirement_changes(
    root, project_dir, make_ctx, fake_stage, edit, name
):
    for need in REGISTRY[name].needs:
        write_interface(project_dir, need, {})
    stage, ctx = fake_stage(name), make_ctx(name)
    before = stage_key(stage, ctx)
    # R-003 names electronics and mechanical only
    edit(project_dir / "spec" / "R-003.yaml", "value: 7", "value: 30")
    assert stage_key(stage, ctx) != before


def test_a_new_requirement_changes_a_spec_wide_key(root, project_dir, make_ctx, fake_stage):
    write_interface(project_dir, "verify_plan", {})
    stage, ctx = fake_stage("architect"), make_ctx("architect")
    before = stage_key(stage, ctx)
    spec_dir = project_dir / "spec"
    new = (spec_dir / "R-005.yaml").read_text().replace("R-005", "R-006")
    (spec_dir / "R-006.yaml").write_text(new)
    assert stage_key(stage, ctx) != before


def test_a_draft_requirement_is_not_part_of_a_spec_wide_key(
    root, project_dir, make_ctx, fake_stage, edit
):
    spec_dir = project_dir / "spec"
    edit(spec_dir / "R-003.yaml", "status: approved", "status: draft")
    write_interface(project_dir, "verify_plan", {})
    wide, ctx = fake_stage("architect"), make_ctx("architect")
    before = stage_key(wide, ctx)
    edit(spec_dir / "R-003.yaml", "value: 7", "value: 30")
    assert stage_key(wide, ctx) == before
    edit(spec_dir / "R-003.yaml", "status: draft", "status: approved")
    assert stage_key(wide, ctx) != before


def test_a_stage_that_is_not_spec_wide_ignores_requirements_that_do_not_name_it(
    root, project_dir, make_ctx, fake_stage, edit
):
    write_interface(project_dir, "architect", {})
    stage, ctx = fake_stage("firmware"), make_ctx("firmware")
    before = stage_key(stage, ctx)
    edit(project_dir / "spec" / "R-003.yaml", "value: 7", "value: 30")
    assert stage_key(stage, ctx) == before


def test_a_stage_can_opt_in_with_reads_all_requirements(
    root, project_dir, make_ctx, fake_stage, edit
):
    write_interface(project_dir, "architect", {})
    stage, ctx = fake_stage("firmware"), make_ctx("firmware")
    stage.reads_all_requirements = True
    before = stage_key(stage, ctx)
    edit(project_dir / "spec" / "R-003.yaml", "value: 7", "value: 30")
    assert stage_key(stage, ctx) != before


def test_reads_all_requirements_false_changes_nothing(
    root, project_dir, make_ctx, fake_stage, edit
):
    write_interface(project_dir, "architect", {})
    stage, ctx = fake_stage("firmware"), make_ctx("firmware")
    plain = stage_key(stage, ctx)
    stage.reads_all_requirements = False
    assert stage_key(stage, ctx) == plain
