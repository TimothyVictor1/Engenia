"""Point loads stated in a requirement ("holds a 0.5 kg load 150 mm from the mast axis").

The requirement schema has no field for a load, so the statement carries it, the same way a
pressure requirement carries its depth (engenia.sim.depth). The bracket pack's proposer and the
bending_stress verifier read it with this one parser, so they cannot disagree. No heavy imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from engenia.sim.depth import GRAVITY_M_S2

_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_MASS = re.compile(_NUMBER + r"\s*kg\b", re.IGNORECASE)
_FORCE = re.compile(_NUMBER + r"\s*N\b")  # newtons: capital N only
_LEVER = re.compile(_NUMBER + r"\s*mm\s+(?:out\s+|away\s+)?(?:from|beyond)\b", re.IGNORECASE)
_MAST = re.compile(
    _NUMBER + r"\s*mm\s+(?:diameter\s+)?mast\b|\bmast\s+(?:of\s+)?(?:diameter\s+)?" + _NUMBER
    + r"\s*mm\b", re.IGNORECASE)


@dataclass(frozen=True)
class PointLoad:
    force_n: float
    lever_mm: float  # from the mounting datum (mast axis or wall face) to the load
    mast_mm: float | None  # mast diameter, when the statement names one


def parse_load(statement: str) -> PointLoad | None:
    """The load a statement names, or None when it gives no load or no lever arm."""
    mass, force, lever = (
        _MASS.search(statement), _FORCE.search(statement), _LEVER.search(statement)
    )
    if lever is None or (mass is None and force is None):
        return None
    newtons = float(mass.group(1)) * GRAVITY_M_S2 if mass else float(force.group(1))
    mast = _MAST.search(statement)
    diameter = float(next(g for g in mast.groups() if g)) if mast else None
    return PointLoad(newtons, float(lever.group(1)), diameter)


def scenario_extra_max(pack_dir: Path, check: str, key: str, default: float) -> float:
    """The largest `extra[key]` among the pack's red-team scenarios that target `check`
    (`default` when none carries it). Scenarios are static pack files, so a verifier and a
    proposer that read them cannot disagree and need not wait for a stage."""
    value = default
    for path in sorted((Path(pack_dir) / "scenarios").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        extra = data.get("extra") or {}
        if check in (data.get("target_checks") or []) and key in extra:
            value = max(value, float(extra[key]))
    return value


def scenario_load_factor(pack_dir: Path, check: str) -> float:
    """The largest `extra.load_factor` of the scenarios that target `check` (1 when none does).
    A gust or a thrust surge multiplies the load the requirement states."""
    return scenario_extra_max(pack_dir, check, "load_factor", 1.0)
