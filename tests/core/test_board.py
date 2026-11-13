"""The contract board: change requests, accepted within budgets, rejected otherwise."""

import json

import pytest

from engenia.contracts.models import ChangeRequest, StageContext
from engenia.core import board
from engenia.core.engine import execute

PROJECT = "harbour-listener"


@pytest.fixture
def contract_path(project_dir):
    path = project_dir / "contract" / "contract.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1,
        "budgets": {
            "mechanical": {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
            "electronics": {"mass_g": 60, "bay_mm": [70, 40, 22], "energy_mwh_per_day": 180},
            "firmware": {"flash_kb": 512, "ram_kb": 64},
        },
        "interfaces": {
            "pin_map": {"HYDROPHONE_ADC": "GPIO34"},
            "mounts": {"board_holes_mm": [[3, 3], [67, 3]]},
        },
        "totals": {"mass_g_max": 400},
    }, indent=2) + "\n")
    return path


@pytest.fixture
def ask(root, make_ctx, contract_path):
    def do(path, requested, stage="electronics", reason="needs more room"):
        return board.submit_change_request(
            make_ctx(stage), path=path, requested=requested, reason=reason
        )

    return do


def decide(root):
    return {d.id: d for d in board.process_change_requests(root, PROJECT)}


def saved(root, request):
    path = root / "projects" / PROJECT / "contract" / "change_requests" / f"{request.id}.json"
    return ChangeRequest.model_validate_json(path.read_text())


def test_a_stage_reads_its_budget_and_the_interfaces(root, contract_path):
    contract = board.load_contract(root, PROJECT)
    assert board.read_budget(contract, "firmware") == {"flash_kb": 512, "ram_kb": 64}
    assert board.read_budget(contract, "redteam") is None
    assert board.read_interfaces(contract)["pin_map"] == {"HYDROPHONE_ADC": "GPIO34"}


def test_a_project_without_a_contract_reads_the_pack_default(root):
    assert board.load_contract(root, PROJECT).version >= 1


def test_a_request_is_written_open_with_a_fresh_id(ask, root):
    first = ask("budgets.electronics.bay_mm", [70, 40, 26])
    second = ask("budgets.electronics.bay_mm", [70, 40, 27])
    assert (first.id, second.id) == ("CR-001", "CR-002")
    assert saved(root, first).status == "open" and saved(root, first).from_stage == "electronics"


def test_a_request_within_budgets_is_accepted_and_applied(ask, root, contract_path):
    request = ask("budgets.electronics.bay_mm", [70, 40, 26])
    decision = decide(root)[request.id]
    assert decision.status == "accepted"
    assert saved(root, request).status == "accepted"
    contract = json.loads(contract_path.read_text())
    assert contract["budgets"]["electronics"]["bay_mm"] == [70, 40, 26]
    assert contract["version"] == 2  # readers of the contract rebuild


@pytest.mark.parametrize(("path", "requested", "why"), [
    ("budgets.electronics.mass_g", 120, "over the 400 g total"),
    ("budgets.electronics.bay_mm", [70, 40, 300], "does not fit"),
    ("budgets.firmware.flash_kb", -1, "positive"),
    ("budgets.firmware.flash_kb", "lots", "not valid"),
    ("budgets.firmware.nonsense", 1, "does not exist"),
    ("totals.mass_g_max", 900, "not a budget"),
    ("interfaces.pin_map", {}, "not a budget"),
    ("version", 9, "not a budget"),
])
def test_a_request_that_breaks_the_budgets_is_rejected_with_the_reason(
    ask, root, contract_path, path, requested, why
):
    before = contract_path.read_text()
    request = ask(path, requested)
    decision = decide(root)[request.id]
    assert decision.status == "rejected" and why in decision.reason
    assert saved(root, request).status == "rejected"
    assert contract_path.read_text() == before  # nothing applied
    recorded = json.loads((contract_path.parent / "decisions.json").read_text())
    assert [(e["id"], e["status"]) for e in recorded] == [(request.id, "rejected")]
    assert why in recorded[0]["reason"]


def test_requests_are_decided_in_order_against_the_contract_the_last_one_left(
    ask, root, contract_path
):
    ask("budgets.electronics.mass_g", 95)  # 300 + 95 = 395: fits
    ask("budgets.electronics.mass_g", 101)  # would be 401: no
    ask("budgets.mechanical.mass_g", 305)  # 305 + 95 = 400: fits only because CR-001 is in
    decisions = decide(root)
    assert [d.status for d in decisions.values()] == ["accepted", "rejected", "accepted"]


def test_decided_requests_are_not_decided_again(ask, root):
    ask("budgets.electronics.bay_mm", [70, 40, 26])
    assert len(board.process_change_requests(root, PROJECT)) == 1
    assert board.process_change_requests(root, PROJECT) == []


def test_a_no_op_request_is_accepted_without_a_new_version(ask, root, contract_path):
    request = ask("budgets.firmware.ram_kb", 64)
    assert decide(root)[request.id].status == "accepted"
    assert json.loads(contract_path.read_text())["version"] == 1


def test_the_engine_decides_requests_before_and_after_a_run(
    root, ask, contract_path, fake_registry, make_ctx
):
    early = ask("budgets.electronics.bay_mm", [70, 40, 26])

    registry = fake_registry()
    stage = registry["firmware"]
    original = stage.build

    def build(ctx):
        original(ctx)
        board.submit_change_request(ctx, path="budgets.firmware.ram_kb", requested=128, reason="x")
        # The new request is still open here: nothing is decided mid-run.
        assert saved(root, ChangeRequest(
            id="CR-002", from_stage="firmware", path="p", requested=1, reason="r", status="open",
        )).status == "open"
        assert json.loads(contract_path.read_text())["version"] == 2

    stage.build = build
    report = execute(PROJECT, root, registry=registry)
    assert [(d.id, d.status.value) for d in report.decisions] == [
        ("CR-001", "accepted"), ("CR-002", "accepted"),
    ]
    assert early.id == "CR-001"
    assert report.contract_changed
    assert json.loads(contract_path.read_text())["version"] == 3


def test_a_request_file_that_cannot_be_read_is_an_error(root, contract_path):
    directory = contract_path.parent / "change_requests"
    directory.mkdir()
    (directory / "CR-001.json").write_text("{not json")
    with pytest.raises(board.BoardError, match="CR-001.json"):
        board.process_change_requests(root, PROJECT)


def test_stage_context_is_enough_to_submit(root, contract_path):
    ctx = StageContext(project=PROJECT, stage="mechanical", root=root)
    assert board.submit_change_request(ctx, path="budgets.mechanical.mass_g", requested=1,
                                       reason="r").from_stage == "mechanical"


# --- build/mismatches.json -------------------------------------------------------------------


def run_with_facts(root, fake_registry, facts_by_stage):
    registry = fake_registry(follow_spec=False)
    for name, extra in facts_by_stage.items():
        base = registry[name].interface
        registry[name].interface = lambda ctx, base=base, extra=extra: {**base(ctx), **extra}
    execute(PROJECT, root, registry=registry)
    return json.loads((root / "projects" / PROJECT / "build" / "mismatches.json").read_text())


def test_mismatches_json_is_an_empty_list_when_the_stages_agree(root, fake_registry, contract_path):
    assert run_with_facts(root, fake_registry, {}) == []


def test_interface_facts_that_disagree_with_the_contract_are_listed(
    root, fake_registry, contract_path
):
    found = run_with_facts(root, fake_registry, {
        "firmware": {"pins_used": {"HYDROPHONE_ADC": "GPIO35"}, "contract_version": 0,
                     "flash_kb": 900},
        "electronics": {"mass_g": 61, "board_holes_mm": [[0, 0]]},
    })
    assert {(m["check"], tuple(m["stages"])) for m in found} == {
        ("contract_version", ("firmware",)),
        ("budget_flash_kb", ("firmware",)),
        ("budget_mass_g", ("electronics",)),
        ("pin_map", ("firmware",)),
        ("board_holes_mm", ("electronics",)),
    }
    assert all(m["source"] == "engine" and m["message"] for m in found)


def test_the_integration_stages_own_mismatches_are_included(root, fake_registry, contract_path):
    registry = fake_registry(follow_spec=False)
    original = registry["integration"].build

    def build(ctx):
        original(ctx)
        (ctx.build_dir / "mismatches.json").write_text(json.dumps(
            [{"check": "int_pins_match", "stages": ["electronics", "firmware"], "message": "m"}]
        ))

    registry["integration"].build = build
    execute(PROJECT, root, registry=registry)
    found = json.loads((root / "projects" / PROJECT / "build" / "mismatches.json").read_text())
    assert found == [{"check": "int_pins_match", "stages": ["electronics", "firmware"],
                      "message": "m", "source": "integration"}]
