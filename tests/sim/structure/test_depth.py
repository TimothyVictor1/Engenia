"""Structural load selection is independent of requirement IDs."""

from types import SimpleNamespace

import pytest
import yaml

from engenia.sim.structure.solver import _depth


def test_deepest_approved_structural_requirement_is_selected(tmp_path):
    for number, depth, status in [(1, 2, "approved"), (2, 4, "approved"), (3, 9, "draft")]:
        (tmp_path / f"R-{number:03d}.yaml").write_text(yaml.safe_dump({
            "id": f"R-{number:03d}", "status": status,
            "statement": f"The hull shall survive at {depth} m submersion depth.",
            "verify": {"check": "stress_safety_factor"},
        }))
    assert _depth(SimpleNamespace(spec_dir=tmp_path)) == 4


def test_structural_requirement_without_load_case_fails_closed(tmp_path):
    (tmp_path / "R-001.yaml").write_text(yaml.safe_dump({
        "id": "R-001", "status": "approved", "statement": "The hull shall survive.",
        "verify": {"check": "stress_safety_factor"},
    }))
    with pytest.raises(ValueError, match="R-001 must state a depth"):
        _depth(SimpleNamespace(spec_dir=tmp_path))
