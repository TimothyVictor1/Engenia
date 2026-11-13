from __future__ import annotations

import pytest

from engenia.contracts.models import Limit
from verifiers._common import MissingInput, compare, read_interface


@pytest.mark.parametrize(
    ("measured", "limit", "expected"),
    [
        (5, Limit(min=4), "pass"),
        (3, Limit(min=4), "fail"),
        (5, Limit(max=6), "pass"),
        (7, Limit(max=6), "fail"),
        (5, Limit(equals=5), "pass"),
        (4, Limit(equals=5), "fail"),
        (5, Limit(min=0, max=10), "pass"),
        (11, Limit(min=0, max=10), "fail"),
        (-1, Limit(min=0, max=10), "fail"),
        (5, Limit(min=0, equals=5), "pass"),
        (6, Limit(min=0, equals=5), "fail"),
        (10, {"min": 0, "max": 10}, "pass"),
        (5, Limit(), "fail"),
    ],
)
def test_compare_handles_all_limit_kinds(measured, limit, expected):
    assert compare(measured, limit) == expected


def test_read_interface_returns_facts(write_interface, make_ctx):
    write_interface("mechanical", {"dry_mass_g": 123})
    facts = read_interface(make_ctx(stage="mechanical"))
    assert facts == {"dry_mass_g": 123}


def test_read_interface_missing_file_raises_clear_error(make_ctx):
    with pytest.raises(MissingInput, match="missing mechanical interface"):
        read_interface(make_ctx(stage="mechanical"))
