"""Numbers a duct requirement states: the propeller, the flange bolts and a thrust load.

The requirement schema carries one value and a limit, so the propeller diameter ("a 70 mm
propeller"), the flange ("4 x M4") and a thrust ("15 N of thrust") live in the statement, read by
the thruster_duct pack's proposer and by the prop_clearance and bending_stress verifiers through
this one parser. No heavy imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_PROPELLER = re.compile(_NUMBER + r"\s*mm\s+(?:diameter\s+)?(?:propeller|prop)\b|"
                        r"\b(?:propeller|prop)\s+(?:of\s+)?(?:diameter\s+)?" + _NUMBER
                        + r"\s*mm\b", re.IGNORECASE)
_BOLTS = re.compile(r"\b(\d+)\s*[x×]\s*M(\d+(?:\.\d+)?)\b")
_THRUST = re.compile(_NUMBER + r"\s*N\b(?:\s+of)?\s+(?:thrust|axial|load)|"
                     r"\bthrust\s+(?:of\s+)?" + _NUMBER + r"\s*N\b")


@dataclass(frozen=True)
class FlangeBolts:
    count: int
    size_mm: float  # the M size: 4 for M4


def parse_propeller_mm(statement: str) -> float | None:
    """The propeller diameter a statement names ("a 70 mm propeller"), or None."""
    found = _PROPELLER.search(statement or "")
    return float(next(g for g in found.groups() if g)) if found else None


def parse_bolts(statement: str) -> FlangeBolts | None:
    """The flange bolts a statement names ("4 x M4"), or None."""
    found = _BOLTS.search(statement or "")
    return FlangeBolts(int(found.group(1)), float(found.group(2))) if found else None


def parse_thrust_n(statement: str) -> float | None:
    """The thrust in newtons a statement names ("15 N of thrust"), or None."""
    found = _THRUST.search(statement or "")
    return float(next(g for g in found.groups() if g)) if found else None
