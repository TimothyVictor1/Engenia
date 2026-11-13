import pytest

from engenia.core.spec import SpecError, load_requirements, load_spec


def test_loads_the_example_spec(root):
    requirements = load_requirements(root / "projects" / "harbour-listener" / "spec")
    assert [r.id for r in requirements] == ["R-001", "R-003", "R-005"]
    assert requirements[1].affects == ["electronics", "mechanical"]
    assert requirements[2].verify.limit.min == 0.20


def test_keeps_the_exact_file_bytes(project_dir):
    spec_dir = project_dir / "spec"
    files = load_spec(spec_dir)
    for f in files:
        assert f.raw == (spec_dir / f"{f.requirement.id}.yaml").read_bytes()


def test_duplicate_ids_are_an_error_that_names_both_files(project_dir):
    spec_dir = project_dir / "spec"
    (spec_dir / "R-004.yaml").write_text((spec_dir / "R-001.yaml").read_text())
    with pytest.raises(SpecError) as caught:
        load_requirements(spec_dir)
    text = str(caught.value)
    assert "duplicate id R-001" in text
    assert "R-001.yaml" in text and "R-004.yaml" in text


def test_id_must_match_the_file_name(project_dir):
    spec_dir = project_dir / "spec"
    (spec_dir / "R-001.yaml").rename(spec_dir / "R-002.yaml")
    with pytest.raises(SpecError) as caught:
        load_requirements(spec_dir)
    assert "R-002.yaml: id R-001 does not match the file name (expected id R-002)" in str(
        caught.value
    )


def test_every_problem_is_reported_at_once(project_dir):
    spec_dir = project_dir / "spec"
    (spec_dir / "R-002.yaml").write_text("id: [unclosed\n")
    text = (spec_dir / "R-001.yaml").read_text().replace("id: R-001", "id: R-004")
    (spec_dir / "R-004.yaml").write_text(text.replace("shall not exceed", "must not exceed"))
    (spec_dir / "R-007.yaml").write_text("id: R-007\nstatement: The buoy shall float.\n")
    with pytest.raises(SpecError) as caught:
        load_requirements(spec_dir)
    problems = caught.value.problems
    assert any(p.startswith("R-002.yaml: invalid YAML") for p in problems)
    assert any(p.startswith("R-004.yaml:") and '"shall"' in p for p in problems)
    assert any(p.startswith("R-007.yaml: verify") for p in problems)  # missing field


def test_unknown_field_is_a_schema_error(project_dir, edit):
    spec_dir = project_dir / "spec"
    edit(spec_dir / "R-001.yaml", "status: approved", "status: approved\nowner: tim")
    with pytest.raises(SpecError, match=r"R-001.yaml: owner"):
        load_requirements(spec_dir)


def test_empty_spec_folder_is_an_error(tmp_path):
    (tmp_path / "spec").mkdir()
    with pytest.raises(SpecError, match="no requirement files"):
        load_requirements(tmp_path / "spec")


def test_non_utf8_file_is_an_error(project_dir):
    spec_dir = project_dir / "spec"
    (spec_dir / "R-006.yaml").write_bytes(b"id: R-006\nstatement: \xff\xfe\n")
    with pytest.raises(SpecError, match="R-006.yaml: not valid UTF-8"):
        load_requirements(spec_dir)
