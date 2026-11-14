import json
from pathlib import Path

import yaml

from engenia.agents.firmware.stage import default_detector, propose_firmware
from engenia.contracts.models import StageContext
from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, text_response, tool_call_response
from engenia.harness.proposer import propose_firmware_detector, proposer_fact

BUILD = ("run_build", {})
REPO = Path(__file__).resolve().parents[2]


def fctx(root):
    ctx = StageContext(project="demo", stage="firmware", root=root)
    interface = ctx.stage_interface_path("electronics")
    interface.parent.mkdir(parents=True, exist_ok=True)
    interface.write_text(json.dumps({"facts": {"duty_cycle": {
        "sample": 0.003, "transmit": 0.0005, "sleep": 0.9965,
    }}}))
    return ctx


def writes(proposal):
    return [tool_call_response(("write_file", {"path": "proposal.json",
                                               "content": json.dumps(proposal)}), BUILD),
            text_response("done")]


def test_without_a_model_the_defaults_are_used_and_recorded(root):
    ctx = fctx(root)
    assert propose_firmware_detector(ctx, [], "tone", default_detector) == default_detector("tone")
    assert proposer_fact(ctx) == "deterministic"


def test_model_proposal_is_used_when_it_passes(root):
    ctx = fctx(root)
    good = {"frequency_hz": 250, "threshold": 0.2, "hold_s": 0.1}
    llm = FakeLLM(writes(good))
    got = propose_firmware_detector(ctx, [], "tone", default_detector, model="a/b", chat_fn=llm,
                                    limits=Limits(max_attempts=3))
    assert got == {"frequency_hz": 250.0, "threshold": 0.2, "hold_s": 0.1}
    assert proposer_fact(ctx) == "model"


def test_out_of_range_proposal_is_sent_back_then_falls_back(root):
    ctx = fctx(root)
    bad = {"frequency_hz": 5000, "threshold": 0.2, "hold_s": 0.1}
    llm = FakeLLM(writes(bad) * 3)
    got = propose_firmware_detector(ctx, [], "tone", default_detector, model="a/b", chat_fn=llm,
                                    limits=Limits(max_attempts=3))
    assert got == default_detector("tone") and proposer_fact(ctx) == "deterministic"
    second = [m["content"] for m in llm.calls[2].messages if m["role"] == "user"]
    assert any("5000.0" in text for text in second)


def test_threshold_detector_takes_no_frequency(root):
    ctx = fctx(root)
    result = propose_firmware_detector(ctx, [], "threshold", lambda: default_detector("threshold"))
    assert result == {"threshold": 0.25, "hold_s": 0.15}
    llm = FakeLLM(writes({"frequency_hz": 200, "threshold": 0.3, "hold_s": 0.2}) * 3)
    got = propose_firmware_detector(
        ctx, [], "threshold", lambda: default_detector("threshold"), model="a/b",
        chat_fn=llm, limits=Limits(max_attempts=3),
    )
    assert got == {"threshold": 0.25, "hold_s": 0.15} and proposer_fact(ctx) == "deterministic"
    assert any("exactly ['threshold', 'hold_s']" in message
               for call in llm.calls for message in
               [m["content"] for m in call.messages if m["role"] == "user"])


def test_propose_firmware_records_detector_and_proposer(root):
    ctx = fctx(root)
    contract = root / "projects/demo/contract"
    contract.mkdir(parents=True)
    (contract / "contract.json").write_text(
        (REPO / "packs/buoy/default_contract.json").read_text())
    propose_firmware(ctx)
    config = json.loads((ctx.src_dir / "config.json").read_text())
    assert config["detector"] == default_detector("tone")
    assert "detector_type" not in config  # the long-standing tone snapshot keeps its shape
    assert json.loads((ctx.src_dir / "proposer.json").read_text()) == {"proposer": "deterministic"}


def test_pack_selects_the_threshold_detector_sources(root):
    ctx = fctx(root)
    (ctx.project_dir / "contract").mkdir(parents=True)
    (ctx.project_dir / "contract/contract.json").write_text(
        (REPO / "packs/enclosure/default_contract.json").read_text())
    (ctx.project_dir / "project.yaml").write_text("pack: enclosure\n")
    packs = root / "packs" / "enclosure"
    packs.mkdir(parents=True)
    (packs / "pack.yaml").write_text(
        (REPO / "packs/enclosure/pack.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    propose_firmware(ctx)
    config = json.loads((ctx.src_dir / "config.json").read_text())
    assert config["detector_type"] == "threshold"
    assert config["detector"] == {"threshold": 0.25, "hold_s": 0.15}
    assert "rms" in (ctx.src_dir / "src" / "detector.c").read_text()
    assert "frequency" not in (ctx.src_dir / "src" / "detector.h").read_text()
    replay = (ctx.src_dir / "tools" / "replay.py").read_text()
    assert "--frequency" not in replay
    assert yaml.safe_load((ctx.project_dir / "project.yaml").read_text())["pack"] == "enclosure"


def test_unknown_pack_detector_fails_loudly(root):
    ctx = fctx(root)
    packs = root / "packs" / "buoy"
    packs.mkdir(parents=True)
    (packs / "pack.yaml").write_text("firmware: {detector: siren}\n")
    try:
        propose_firmware(ctx)
    except ValueError as exc:
        assert "siren" in str(exc) and "tone" in str(exc)
    else:
        raise AssertionError("an unknown detector family must fail, not fall back")
