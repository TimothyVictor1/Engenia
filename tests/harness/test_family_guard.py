import pytest

from engenia.harness.llm import ModelFamilyError, check_model_families, model_family


def env(design="", verify="", allow=""):
    return {"ENGENIA_DESIGN_MODEL": design, "ENGENIA_VERIFY_MODEL": verify,
            "ENGENIA_ALLOW_SAME_FAMILY": allow}


def test_family_is_the_part_before_the_slash():
    assert model_family("Qwen/Qwen3-235B") == "qwen"
    assert model_family("meta-llama/Llama-3.3-70B") == "meta-llama"
    assert model_family("local-model") == "local-model"


def test_different_families_pass():
    assert check_model_families(env("Qwen/Qwen3-235B", "meta-llama/Llama-3.3-70B")) is None


def test_nothing_to_compare_when_one_is_unset():
    assert check_model_families(env("Qwen/Qwen3-235B", "")) is None
    assert check_model_families(env()) is None


@pytest.mark.parametrize("design,verify", [
    ("Qwen/Qwen3-235B", "Qwen/Qwen3-32B"),
    ("meta-llama/A", "meta-llama/B"),
    ("same-model", "same-model"),
])
def test_same_model_or_family_refuses_with_a_clear_message(design, verify):
    with pytest.raises(ModelFamilyError) as caught:
        check_model_families(env(design, verify))
    message = str(caught.value)
    assert design in message and verify in message
    assert "ENGENIA_ALLOW_SAME_FAMILY=1" in message


def test_override_returns_the_note_to_record():
    note = check_model_families(env("Qwen/A", "Qwen/B", "1"))
    assert note is not None and "overrode" in note


def test_only_the_value_1_overrides():
    with pytest.raises(ModelFamilyError):
        check_model_families(env("Qwen/A", "Qwen/B", "true"))


def test_design_loop_refuses_before_calling_the_model(monkeypatch, ctx, stage):
    from engenia.harness.design_loop import generate_until_pass
    from engenia.harness.llm import FakeLLM

    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Qwen/A")
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Qwen/B")
    llm = FakeLLM([])
    with pytest.raises(ModelFamilyError):
        generate_until_pass(stage, ctx, chat_fn=llm)
    assert llm.calls == []
