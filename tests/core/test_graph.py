from types import SimpleNamespace

import pytest

from engenia.contracts.models import STAGE_NAMES
from engenia.core.graph import GraphError, downstream, requirements_for, topo_order
from engenia.core.spec import load_requirements
from engenia.stages.registry import REGISTRY


def reg(**needs):
    return {name: SimpleNamespace(name=name, needs=list(n)) for name, n in needs.items()}


def test_topo_order_covers_every_stage_and_respects_needs():
    order = topo_order()
    assert sorted(order) == sorted(STAGE_NAMES)
    for name, stage in REGISTRY.items():
        assert all(order.index(need) < order.index(name) for need in stage.needs)


def test_topo_order_is_stable():
    assert topo_order() == topo_order()
    assert topo_order(reg(a=[], b=["a"], c=[])) == ["a", "b", "c"]


def test_downstream_is_transitive_and_in_order():
    assert downstream("architect") == [
        "mechanical", "electronics", "firmware", "integration", "simulation", "final", "report",
    ]
    assert downstream("redteam") == ["simulation", "final", "report"]
    assert downstream("mechanical") == ["integration", "simulation", "final", "report"]


def test_downstream_of_a_last_stage_is_empty():
    assert downstream("report") == []


def test_downstream_of_an_unknown_stage():
    with pytest.raises(GraphError, match="unknown stage 'plumbing'"):
        downstream("plumbing")


def test_unknown_need_is_an_error():
    with pytest.raises(GraphError, match="stage 'b' needs unknown stage 'zzz'"):
        topo_order(reg(a=[], b=["zzz"]))


def test_cycle_is_an_error_that_shows_the_cycle():
    with pytest.raises(GraphError, match="cycle in stage graph: a -> c -> b -> a"):
        topo_order(reg(a=["c"], b=["a"], c=["b"]))


def test_a_stage_that_needs_itself_is_a_cycle():
    with pytest.raises(GraphError, match="cycle in stage graph: a -> a"):
        topo_order(reg(a=["a"]))


def test_stage_registered_under_the_wrong_name():
    with pytest.raises(GraphError, match="registered as 'a'"):
        topo_order({"a": SimpleNamespace(name="b", needs=[])})


def test_requirements_for_returns_the_requirements_that_affect_a_stage(root):
    requirements = load_requirements(root / "projects" / "harbour-listener" / "spec")
    ids = lambda stage: [r.id for r in requirements_for(stage, requirements)]  # noqa: E731
    assert ids("mechanical") == ["R-001", "R-003", "R-005"]
    assert ids("electronics") == ["R-001", "R-003"]
    assert ids("firmware") == []


def test_requirements_for_an_unknown_stage(root):
    with pytest.raises(GraphError):
        requirements_for("plumbing", [])
