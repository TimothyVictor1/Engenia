"""Print-bed fit verifier.

FORMULA:
    fits = all(sorted(bbox_mm)[i] <= sorted(bed_mm)[i] for i in range(3))

Sorting both triples is equivalent to trying every axis-aligned rotation. ``measured`` is
1.0 when a rotation fits and 0.0 when none does. ``bed_mm`` is read from params.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict, VerdictResult
from verifiers._common import (
    MissingInput,
    calculated_verdict,
    fact_vector,
    guard,
    vector_from,
)


def _fits(bbox: tuple[float, ...], bed: tuple[float, ...]) -> bool:
    return all(size <= capacity for size, capacity in zip(sorted(bbox), sorted(bed), strict=True))


@guard
def run(ctx: CheckContext) -> Verdict:
    bbox, _ = vector_from(ctx, "bbox_mm", ctx.stage, "mechanical")
    if "bed_mm" not in (ctx.params or {}):
        raise MissingInput("params.bed_mm is required for print_bed_fit")
    bed = fact_vector(ctx.params, "bed_mm", "params")

    passed = _fits(bbox, bed)
    if passed:
        message = None
    else:
        message = (
            f"bbox {list(bbox)} mm does not fit bed {list(bed)} mm "
            "in any axis-aligned orientation"
        )
    return calculated_verdict(
        ctx,
        1.0 if passed else 0.0,
        VerdictResult.PASS if passed else VerdictResult.FAIL,
        message=message,
    )
