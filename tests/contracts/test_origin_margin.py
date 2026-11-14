"""Additive contract fields keep legacy documents valid."""

import pytest
from pydantic import ValidationError

from engenia.contracts.models import Requirement, Verdict


@pytest.fixture
def legacy_requirement():
    return {
        "id": "R-001", "statement": "The product shall fit the bay.",
        "verify": {"method": "inspection", "check": "bay_fit"},
        "affects": ["mechanical"], "status": "draft",
    }


@pytest.fixture
def legacy_verdict():
    return {
        "check": "bay_fit", "measured": 1, "limit": {"equals": 1},
        "result": "pass", "evidence_level": "calculated",
    }


def test_legacy_requirement_defaults(legacy_requirement):
    requirement = Requirement.model_validate(legacy_requirement)
    assert requirement.origin == "user"
    assert requirement.reason is None


def test_derived_requirement_round_trip(legacy_requirement):
    data = {**legacy_requirement, "origin": "derived", "reason": "Pack baseline: bay fit."}
    requirement = Requirement.model_validate(data)
    assert Requirement.model_validate_json(requirement.model_dump_json()) == requirement
    assert requirement.origin == "derived"
    assert requirement.reason == data["reason"]


def test_origin_rejects_unknown_values(legacy_requirement):
    with pytest.raises(ValidationError):
        Requirement.model_validate({**legacy_requirement, "origin": "invented"})


def test_legacy_verdict_defaults(legacy_verdict):
    assert Verdict.model_validate(legacy_verdict).margin_pct is None


@pytest.mark.parametrize("margin", [None, 0, 12.5, -20])
def test_margin_round_trip(legacy_verdict, margin):
    verdict = Verdict.model_validate({**legacy_verdict, "margin_pct": margin})
    assert Verdict.model_validate_json(verdict.model_dump_json()).margin_pct == margin


def test_unknown_fields_still_rejected(legacy_verdict):
    with pytest.raises(ValidationError):
        Verdict.model_validate({**legacy_verdict, "unknown": 1})
