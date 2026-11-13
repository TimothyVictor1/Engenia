"""Shared helpers for verifier scripts.

A verifier reads build outputs only. This module deliberately contains no model, network,
clock or random-number access, so every verdict is reproducible.
"""

from __future__ import annotations

import functools
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from engenia.contracts.models import (
    CheckContext,
    EvidenceLevel,
    Limit,
    Verdict,
    VerdictResult,
)


class MissingInput(Exception):
    """A required build input is absent, unreadable, or not usable."""


def _read_json(path: Path, label: str, shown: str | None = None) -> Any:
    """Read one JSON file, raising :class:`MissingInput` that names it as ``shown``."""
    where = shown if shown is not None else str(path)
    if not path.is_file():
        raise MissingInput(f"missing {label}: {where}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MissingInput(f"cannot read {label} {where}: {exc}") from exc


def _shown_path(ctx: CheckContext, path: Path) -> str:
    """Paths in messages are project-relative, so the text does not change between roots."""
    try:
        return str(path.relative_to(ctx.root))
    except ValueError:
        return str(path)


def read_interface(ctx: CheckContext, stage: str | None = None) -> dict[str, Any]:
    """Read ``build/<stage>/interface.json`` and return its ``facts`` object.

    The stage defaults to ``ctx.stage``. Missing, unreadable or malformed files raise
    :class:`MissingInput` so a verifier can turn the problem into an ``error`` verdict.
    """
    name = stage or ctx.stage
    path = ctx.stage_interface_path(name)
    data = _read_json(path, f"{name} interface", _shown_path(ctx, path))
    if not isinstance(data, dict):
        raise MissingInput(f"{name} interface {path} must contain a JSON object")
    facts = data.get("facts")
    if not isinstance(facts, dict):
        raise MissingInput(f"{name} interface {path} has no facts object")
    return facts


def try_read_interface(ctx: CheckContext, stage: str | None = None) -> dict[str, Any] | None:
    """Like :func:`read_interface`, but return ``None`` when that interface is unavailable."""
    try:
        return read_interface(ctx, stage)
    except MissingInput:
        return None


def read_contract(ctx: CheckContext) -> dict[str, Any]:
    """Read and return ``contract/contract.json`` for the current project."""
    data = _read_json(
        ctx.contract_path, "contract.json", _shown_path(ctx, ctx.contract_path)
    )
    if not isinstance(data, dict):
        raise MissingInput(f"contract.json {ctx.contract_path} must contain a JSON object")
    return data


def as_number(value: Any, label: str) -> float:
    """Convert one JSON value to a finite float or raise :class:`MissingInput`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MissingInput(f"{label} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise MissingInput(f"{label} must be finite, got {value!r}")
    return number


def fact_number(facts: Mapping[str, Any], key: str, source: str) -> float:
    """Read a required numeric fact from an interface facts mapping."""
    if key not in facts:
        raise MissingInput(f"{source} interface has no '{key}' fact")
    return as_number(facts[key], f"{source}.{key}")


def fact_vector(
    facts: Mapping[str, Any],
    key: str,
    source: str,
    *,
    length: int | None = 3,
) -> tuple[float, ...]:
    """Read a required numeric vector fact from an interface facts mapping."""
    if key not in facts:
        raise MissingInput(f"{source} interface has no '{key}' fact")
    value = facts[key]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MissingInput(f"{source}.{key} must be a list of numbers, got {value!r}")
    if length is not None and len(value) != length:
        raise MissingInput(f"{source}.{key} must have {length} values, got {len(value)}")
    return tuple(as_number(item, f"{source}.{key}[{index}]") for index, item in enumerate(value))


def _ordered_stages(ctx: CheckContext, stages: tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    for stage in stages or (ctx.stage,):
        if stage not in ordered:
            ordered.append(stage)
    return ordered


def fact_from(
    ctx: CheckContext, key: str, *stages: str
) -> tuple[float, str]:
    """Return ``(value, stage)`` for the first interface that contains ``key``.

    Interfaces are tried in the supplied order, starting with ``ctx.stage`` when no
    stage is supplied.
    """
    ordered = _ordered_stages(ctx, stages)
    for stage in ordered:
        facts = try_read_interface(ctx, stage)
        if facts is not None and key in facts:
            return fact_number(facts, key, stage), stage
    names = ", ".join(ordered)
    raise MissingInput(f"no '{key}' fact in interface(s): {names}")


def vector_from(
    ctx: CheckContext, key: str, *stages: str, length: int | None = 3
) -> tuple[tuple[float, ...], str]:
    """Return ``(vector, stage)`` for the first interface that contains ``key``."""
    ordered = _ordered_stages(ctx, stages)
    for stage in ordered:
        facts = try_read_interface(ctx, stage)
        if facts is not None and key in facts:
            return fact_vector(facts, key, stage, length=length), stage
    names = ", ".join(ordered)
    raise MissingInput(f"no '{key}' fact in interface(s): {names}")


def interface_with(
    ctx: CheckContext, keys: Sequence[str], *stages: str
) -> tuple[str, dict[str, Any]]:
    """Return the first interface containing every key in ``keys``."""
    ordered = _ordered_stages(ctx, stages)
    for stage in ordered:
        facts = try_read_interface(ctx, stage)
        if facts is not None and all(key in facts for key in keys):
            return stage, facts
    names = ", ".join(ordered)
    wanted = ", ".join(keys)
    raise MissingInput(f"no interface with {wanted} in: {names}")


def water_density_g_cm3(ctx: CheckContext) -> float:
    """Return water density in g/cm3 from params, defaulting to 1.005 g/cm3.

    Accepts ``water_density`` or ``water_density_g_cm3`` in g/cm3, and
    ``water_density_kg_m3`` (the form used in PROJECT.md) in kg/m3.
    """
    params = ctx.params or {}
    if "water_density" in params:
        value = as_number(params["water_density"], "params.water_density")
    elif "water_density_g_cm3" in params:
        value = as_number(params["water_density_g_cm3"], "params.water_density_g_cm3")
    elif "water_density_kg_m3" in params:
        value = as_number(params["water_density_kg_m3"], "params.water_density_kg_m3") / 1000.0
    elif "water_density_kg_m_3" in params:
        value = (
            as_number(params["water_density_kg_m_3"], "params.water_density_kg_m_3") / 1000.0
        )
    else:
        value = 1.005
    if value <= 0:
        raise MissingInput(f"water density must be positive, got {value}")
    return value


def derating_factor(ctx: CheckContext, default: float = 0.8) -> float:
    """Return the battery derating factor from params, defaulting to 0.8."""
    params = ctx.params or {}
    for key in ("derating", "derating_factor", "battery_derating"):
        if key in params:
            value = as_number(params[key], f"params.{key}")
            break
    else:
        value = default
    if not 0 < value <= 1:
        raise MissingInput(f"derating factor must be in (0, 1], got {value}")
    return value


def _bounds(limit: Limit | Mapping[str, Any] | Any) -> tuple[Any, Any, Any]:
    if isinstance(limit, Limit):
        return limit.min, limit.max, limit.equals
    if isinstance(limit, Mapping):
        return limit.get("min"), limit.get("max"), limit.get("equals")
    return getattr(limit, "min", None), getattr(limit, "max", None), getattr(limit, "equals", None)


def compare(measured: float, limit: Limit | Mapping[str, Any] | Any) -> VerdictResult:
    """Compare a measured value with a limit containing min, max, equals or a combination.

    Every bound that is present must hold. A limit with no bound fails because there is
    nothing to verify. ``equals`` uses a tiny floating-point tolerance.
    """
    low, high, equals = _bounds(limit)
    checks: list[bool] = []
    if low is not None:
        checks.append(float(measured) >= float(low))
    if high is not None:
        checks.append(float(measured) <= float(high))
    if equals is not None:
        checks.append(
            math.isclose(float(measured), float(equals), rel_tol=1e-9, abs_tol=1e-12)
        )
    if not checks:
        return VerdictResult.FAIL
    return VerdictResult.PASS if all(checks) else VerdictResult.FAIL


def comparison_message(measured: float, limit: Limit | Mapping[str, Any] | Any) -> str:
    """Explain why ``measured`` does not satisfy ``limit``."""
    low, high, equals = _bounds(limit)
    reasons: list[str] = []
    if low is not None and float(measured) < float(low):
        reasons.append(f"below min {low}")
    if high is not None and float(measured) > float(high):
        reasons.append(f"above max {high}")
    if equals is not None and not math.isclose(
        float(measured), float(equals), rel_tol=1e-9, abs_tol=1e-12
    ):
        reasons.append(f"not equal to {equals}")
    if not reasons:
        reasons.append("limit has no min, max or equals bound")
    return f"measured {measured} " + ", ".join(reasons)


def _verdict_with_level(
    ctx: CheckContext,
    measured: float | None,
    result: VerdictResult,
    evidence_level: EvidenceLevel,
    *,
    limit: Limit | None = None,
    message: str | None = None,
) -> Verdict:
    """Build a verdict using ``ctx`` for the check identity."""
    return Verdict(
        check=ctx.check,
        requirement=ctx.requirement,
        measured=None if measured is None else float(measured),
        limit=limit if limit is not None else ctx.limit,
        result=result,
        evidence_level=evidence_level,
        message=message,
    )


def calculated_verdict(
    ctx: CheckContext,
    measured: float | None,
    result: VerdictResult,
    *,
    limit: Limit | None = None,
    message: str | None = None,
) -> Verdict:
    """Build a ``calculated`` verdict using ``ctx`` for the check identity."""
    return _verdict_with_level(
        ctx, measured, result, EvidenceLevel.CALCULATED, limit=limit, message=message
    )


def simulated_verdict(
    ctx: CheckContext,
    measured: float | None,
    result: VerdictResult,
    *,
    limit: Limit | None = None,
    message: str | None = None,
) -> Verdict:
    """Build a ``simulated`` verdict: the measured value comes from a simulation job."""
    return _verdict_with_level(
        ctx, measured, result, EvidenceLevel.SIMULATED, limit=limit, message=message
    )


def checked_verdict(
    ctx: CheckContext,
    measured: float,
    *,
    limit: Limit | None = None,
) -> Verdict:
    """Compare ``measured`` with ``limit`` (or ``ctx.limit``) and build a verdict."""
    selected = limit if limit is not None else ctx.limit
    result = compare(measured, selected)
    message = None if result == VerdictResult.PASS else comparison_message(measured, selected)
    return calculated_verdict(ctx, measured, result, limit=selected, message=message)


def checked_simulated_verdict(
    ctx: CheckContext,
    measured: float,
    *,
    limit: Limit | None = None,
) -> Verdict:
    """Like :func:`checked_verdict`, but with ``evidence_level`` "simulated"."""
    selected = limit if limit is not None else ctx.limit
    result = compare(measured, selected)
    message = None if result == VerdictResult.PASS else comparison_message(measured, selected)
    return simulated_verdict(ctx, measured, result, limit=selected, message=message)


def error_verdict(ctx: CheckContext, message: str) -> Verdict:
    """Build an ``error`` verdict with no measured value."""
    return calculated_verdict(
        ctx,
        None,
        VerdictResult.ERROR,
        message=message,
    )


def guard(runner: Callable[[CheckContext], Verdict]) -> Callable[[CheckContext], Verdict]:
    """Turn any unexpected exception into an ``error`` verdict, never an exception."""

    @functools.wraps(runner)
    def wrapped(ctx: CheckContext) -> Verdict:
        try:
            return runner(ctx)
        except Exception as exc:  # noqa: BLE001 - verifiers must return error, never raise
            return error_verdict(ctx, f"{ctx.check}: {exc}")

    return wrapped
