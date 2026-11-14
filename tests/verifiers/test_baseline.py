"""Physical baseline boundary conditions fail closed."""

import math

from engenia.contracts.models import Limit
from verifiers.metacentric_height import run


def test_neutral_stability_does_not_pass_strict_baseline(write_interface, make_ctx):
    write_interface("mechanical", {
        "bbox_mm": [20, 20, 20], "displaced_volume_cm3": math.pi,
        "centre_of_mass_mm": [0, 0, 7.5],
    })
    ctx = make_ctx(stage="mechanical", limit=Limit(min=0), params={"strict_min": True})
    verdict = run(ctx)
    assert verdict.result == "fail"
    assert verdict.measured == 0
    assert verdict.margin_pct is None
