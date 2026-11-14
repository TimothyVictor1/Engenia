"""Per-project pack resolution: projects/<p>/project.yaml names the pack, buoy is the default."""

from pathlib import Path

import pytest

from engenia.contracts.models import StageContext
from engenia.harness.packs import pack_file, project_pack

REPO = Path(__file__).resolve().parents[2]


def ctx_for(root: Path, project: str = "demo") -> StageContext:
    return StageContext(project=project, stage="architect", root=root)


def test_missing_project_yaml_defaults_to_the_buoy_pack(root):
    assert project_pack(ctx_for(root)) == "buoy"


def test_project_yaml_names_the_pack(root):
    (root / "projects" / "demo" / "project.yaml").write_text("pack: enclosure\n")
    assert project_pack(ctx_for(root)) == "enclosure"


def test_pack_file_prefers_the_run_roots_copy(root):
    local = root / "packs" / "enclosure" / "pack.yaml"
    local.parent.mkdir(parents=True)
    local.write_text("templates: {}\n")
    ctx = ctx_for(root)
    (root / "projects" / "demo" / "project.yaml").write_text("pack: enclosure\n")
    assert pack_file(ctx, "pack.yaml") == local
    assert (
        pack_file(ctx, "default_contract.json")
        == REPO / "packs" / "enclosure" / "default_contract.json"
    )


def test_pack_file_falls_back_to_the_repository(root):
    (root / "projects" / "demo" / "project.yaml").write_text("pack: buoy\n")
    assert pack_file(ctx_for(root), "pack.yaml") == REPO / "packs" / "buoy" / "pack.yaml"


def test_unsupported_keys_rejected(root):
    (root / "projects" / "demo" / "project.yaml").write_text("pack: buoy\nname: x\n")
    with pytest.raises(ValueError, match="unsupported keys"):
        project_pack(ctx_for(root))


def test_malformed_project_yaml_rejected(root):
    (root / "projects" / "demo" / "project.yaml").write_text("pack: [buoy\n")
    with pytest.raises(ValueError, match="project.yaml"):
        project_pack(ctx_for(root))


def test_bad_pack_name_rejected(root):
    for text in ("pack: Buoy\n", "pack: 2x\n", "pack: ''\n"):
        (root / "projects" / "demo" / "project.yaml").write_text(text)
        with pytest.raises(ValueError, match="lowercase"):
            project_pack(ctx_for(root))
