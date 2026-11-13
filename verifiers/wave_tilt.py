"""Wave tilt verifier: worst tilt among scenarios within the qualifying height.

FACTS (simulation interface):
    wave_tilt: {scenario: {"wave_height_m": float, "max_tilt_deg": float}}

PARAMS:
    max_wave_height_m (default 0.5, R-011's stated significant wave height):
        scenarios whose wave_height_m is at most this height qualify.
        Alias: significant_wave_height_m.

MEASURED: the largest max_tilt_deg among qualifying scenarios, compared with
the check plan limit (R-011: max 30 deg).

RESULT:
    pass/fail   at least one scenario qualifies
    error       no scenario qualifies, or the facts are missing or unusable:
                no simulation interface, no wave_tilt fact, an entry that is
                not an object with numeric wave_height_m and max_tilt_deg, a
                negative wave height, or a tilt outside the plausible 0 to 90
                degree range.

ASSUMES: max_tilt_deg is the simulated pitch/roll amplitude for that scenario
         at its significant wave height, in degrees.
"""

from __future__ import annotations

import math
from typing import Any

from engenia.contracts.models import CheckContext, EvidenceLevel, Verdict, VerdictResult
from verifiers._common import (
    MissingInput,
    as_number,
    compare,
    comparison_message,
    error_verdict,
    guard,
    read_interface,
)

DEFAULT_MAX_WAVE_HEIGHT_M = 0.5
PARAM_KEYS = ("max_wave_height_m", "significant_wave_height_m")
TILT_RANGE = (0.0, 90.0)


def _threshold(ctx: CheckContext) -> float:
    """The qualifying wave height from params, defaulting to 0.5 m."""
    params: dict[str, Any] = ctx.params or {}
    for key in PARAM_KEYS:
        if key in params:
            value = as_number(params[key], f"params.{key}")
            if value <= 0:
                raise MissingInput(f"params.{key} must be positive, got {value}")
            return value
    return DEFAULT_MAX_WAVE_HEIGHT_M


def _tilt_entries(facts: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Validate the wave_tilt fact into {scenario: (wave_height_m, max_tilt_deg)}."""
    if "wave_tilt" not in facts:
        raise MissingInput("simulation interface has no 'wave_tilt' fact")
    raw = facts["wave_tilt"]
    if not isinstance(raw, dict):
        raise MissingInput(f"simulation interface 'wave_tilt' must be a mapping, got {raw!r}")
    entries: dict[str, tuple[float, float]] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise MissingInput(f"wave_tilt[{name!r}] must be an object, got {entry!r}")
        height = as_number(entry.get("wave_height_m"), f"wave_tilt[{name!r}].wave_height_m")
        tilt = as_number(entry.get("max_tilt_deg"), f"wave_tilt[{name!r}].max_tilt_deg")
        if height < 0:
            raise MissingInput(
                f"wave_tilt[{name!r}].wave_height_m must not be negative, got {height}"
            )
        if not math.isfinite(tilt) or not TILT_RANGE[0] <= tilt <= TILT_RANGE[1]:
            raise MissingInput(
                f"wave_tilt[{name!r}].max_tilt_deg {tilt} is outside the plausible "
                f"{TILT_RANGE[0]} to {TILT_RANGE[1]} degree range"
            )
        entries[str(name)] = (height, tilt)
    return entries


@guard
def run(ctx: CheckContext) -> Verdict:
    entries = _tilt_entries(read_interface(ctx))
    threshold = _threshold(ctx)
    qualifying = [tilt for height, tilt in entries.values() if height <= threshold]
    if not qualifying:
        return error_verdict(
            ctx,
            f"no scenario with wave_height_m at most {threshold} m "
            f"(wave_tilt holds {len(entries)} scenario(s))",
        )
    measured = max(qualifying)
    result = compare(measured, ctx.limit)
    message = None if result == VerdictResult.PASS else comparison_message(measured, ctx.limit)
    return Verdict(
        check=ctx.check,
        requirement=ctx.requirement,
        measured=measured,
        limit=ctx.limit,
        result=result,
        evidence_level=EvidenceLevel.SIMULATED,
        message=message,
    )
