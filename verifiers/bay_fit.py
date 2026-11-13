"""Electronics bay fit verifier.

FORMULA:
    fits = board_mm fits battery_bay_mm and battery size fits battery_bay_mm
    fits(a, bay) = all(sorted(a)[i] <= sorted(bay)[i] for i in range(3))

Each part may use a different axis-aligned orientation. ``measured`` is 1.0 when both
parts fit and 0.0 otherwise. Battery dimensions may be published as ``battery_mm``,
``battery_size_mm`` or ``battery_dimensions_mm``.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict, VerdictResult
from verifiers._common import (
    MissingInput,
    calculated_verdict,
    guard,
    vector_from,
)


def _fits(part: tuple[float, ...], bay: tuple[float, ...]) -> bool:
    return all(size <= capacity for size, capacity in zip(sorted(part), sorted(bay), strict=True))


def _battery_size(ctx: CheckContext) -> tuple[float, ...]:
    last_error: MissingInput | None = None
    for key in ("battery_mm", "battery_size_mm", "battery_dimensions_mm"):
        try:
            battery, _ = vector_from(ctx, key, ctx.stage, "electronics")
            return battery
        except MissingInput as exc:
            last_error = exc
    raise MissingInput(
        "electronics interface has no battery dimensions "
        "(expected battery_mm, battery_size_mm or battery_dimensions_mm)"
    ) from last_error


@guard
def run(ctx: CheckContext) -> Verdict:
    board, _ = vector_from(ctx, "board_mm", ctx.stage, "electronics")
    battery = _battery_size(ctx)
    bay, _ = vector_from(ctx, "battery_bay_mm", ctx.stage, "mechanical")

    board_fits = _fits(board, bay)
    battery_fits = _fits(battery, bay)
    passed = board_fits and battery_fits
    if passed:
        message = None
    else:
        problems = []
        if not board_fits:
            problems.append(f"board {list(board)} mm")
        if not battery_fits:
            problems.append(f"battery {list(battery)} mm")
        message = (
            f"{' and '.join(problems)} do not fit battery bay {list(bay)} mm "
            "in any axis-aligned orientation"
        )
    return calculated_verdict(
        ctx,
        1.0 if passed else 0.0,
        VerdictResult.PASS if passed else VerdictResult.FAIL,
        message=message,
    )
